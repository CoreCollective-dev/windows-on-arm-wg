import copy
import json
import re
import tempfile
import unittest
from pathlib import Path

from monitoring.monitor import FAILURE_POLICY, GitHub, MonitorError, attempt_jobs, control_jobs, evaluate, plan, read_json, scan
from monitoring.pairing import comparator, is_amd64, platform_key
from test_monitor import CHANNEL, FakeAPI, NOW, config, empty_state, job, run


def platform_jobs(arm="success", amd="success"):
    return [job(arm), job(amd, name="Windows AMD64 cp314")]


class PlatformComparisonTests(unittest.TestCase):
    def assess(self, jobs, target=None):
        return evaluate(jobs, target or CHANNEL["targets"][0], CHANNEL["required_steps"])

    def test_full_platform_truth_table(self):
        states = ("success", "failure", "timed_out", "skipped", "cancelled", None)
        for arm in states:
            for amd in states:
                with self.subTest(arm=arm, amd=amd):
                    result = self.assess(platform_jobs(arm, amd))
                    expected = arm in ("failure", "timed_out") and amd == "success"
                    self.assertEqual(result["health"] == "Broken", expected)
                    self.assertEqual(result["arm_specific_failure"], expected)
                    self.assertEqual(result["notify"], expected)

    def test_utf8_job_identifiers_survive_registry_import(self):
        name = "\U0001f4e6 Build wheels / windows-11-arm"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text(json.dumps({"job": "^" + re.escape(name) + "$"}, ensure_ascii=False), encoding="utf-8")
            self.assertIsNotNone(re.fullmatch(read_json(path)["job"], name))

    def test_arm_success_and_amd_failure_not_a_regression(self):
        result = self.assess(platform_jobs("success", "failure"))
        self.assertEqual(result["health"], "Healthy")
        self.assertFalse(result["arm_specific_failure"])

    def test_amd_missing_cannot_confirm_arm_failure(self):
        self.assertFalse(self.assess([job("failure")])["arm_specific_failure"])

    def test_missing_comparator_is_explicit_unknown(self):
        target = copy.deepcopy(CHANNEL["targets"][0])
        target.pop("amd64_job")
        result = self.assess(platform_jobs("failure"), target)
        self.assertEqual(result["health"], "Unknown")
        self.assertIn("not configured", result["detail"])

    def test_failed_platform_build_cannot_hide_behind_skipped_build_step(self):
        jobs = platform_jobs()
        jobs[0].update(conclusion="failure", steps=[
            {"name": "Initialize submodules", "conclusion": "failure"},
            {"name": "Build wheel", "conclusion": "skipped"},
        ])
        self.assertTrue(self.assess(jobs)["arm_specific_failure"])

    def test_upload_failure_is_not_build_test_failure(self):
        jobs = platform_jobs()
        jobs[0].update(conclusion="failure", steps=[
            {"name": "Build wheel", "conclusion": "success"},
            {"name": "Upload artifact", "conclusion": "failure"},
        ])
        self.assertFalse(self.assess(jobs)["arm_specific_failure"])

    def test_amd_different_sha_not_comparable(self):
        jobs = platform_jobs("failure")
        jobs[1]["head_sha"] = "different"
        self.assertFalse(self.assess(jobs)["arm_specific_failure"])

    def test_amd_different_run_not_comparable(self):
        jobs = platform_jobs("failure")
        jobs[1]["run_id"] = 999
        self.assertFalse(self.assess(jobs)["arm_specific_failure"])

    def test_explicit_cross_workflow_same_sha_pair(self):
        jobs = platform_jobs("failure")
        jobs[1]["run_id"] = 20
        target = {**CHANNEL["targets"][0], "amd64_workflow": "windows-amd64.yml"}
        context = {"arm_run_id": 10, "amd64_run_id": 20, "source_sha": "abc", "ref": "main"}
        result = evaluate(jobs[:1], target, CHANNEL["required_steps"], jobs[1:], context)
        self.assertTrue(result["arm_specific_failure"])
        jobs[1]["head_sha"] = "different"
        result = evaluate(jobs[:1], target, CHANNEL["required_steps"], jobs[1:], context)
        self.assertFalse(result["arm_specific_failure"])

    def test_cross_workflow_lookup_requires_ref_sha_and_event(self):
        target = {**CHANNEL["targets"][0], "amd64_workflow": "amd64.yml", "amd64_events": ["workflow_dispatch"]}
        valid = run(20, event="workflow_dispatch")
        candidates = [valid, run(21, event="pull_request"), run(22, branch="other"),
                      {**run(23, event="workflow_dispatch"), "head_sha": "other"}]
        api = FakeAPI()

        def pages(path, key, params=None):
            if path.endswith("/runs"):
                return candidates
            self.assertIn("/runs/20/", path)
            return [{**job(name="Windows AMD64 cp314"), "run_id": 20}]

        api.pages = pages
        jobs, context = control_jobs(api, CHANNEL, target, run())
        self.assertEqual(context["amd64_run_id"], 20)
        self.assertEqual(jobs[0]["head_sha"], "abc")

    def test_latest_failed_control_is_not_replaced_by_older_success(self):
        target = {**CHANNEL["targets"][0], "amd64_workflow": "amd64.yml"}
        api = FakeAPI()

        def pages(path, key, params=None):
            if path.endswith("/runs"):
                return [run(20), run(21, conclusion="failure")]
            self.assertIn("/runs/21/", path)
            return [{**job("failure", name="Windows AMD64 cp314"), "run_id": 21}]

        api.pages = pages
        jobs, context = control_jobs(api, CHANNEL, target, run())
        self.assertEqual(context["amd64_run_id"], 21)
        self.assertFalse(evaluate([job("failure")], target, CHANNEL["required_steps"], jobs, context)["arm_specific_failure"])

    def test_mutable_external_source_guard_withholds_failure(self):
        target = {**CHANNEL["targets"][0], "comparison_blocker": "actual source SHAs unverified"}
        self.assertFalse(self.assess(platform_jobs("failure"), target)["arm_specific_failure"])

    def test_resolved_source_capture_is_checked_not_only_workflow_sha(self):
        source = "a" * 40
        target = {
            "job": r"^Build main-windows \(arm64\) @ (?P<source_sha>[0-9a-f]{40})$",
            "amd64_job": r"^Build main-windows \(x64\) @ (?P<source_sha>[0-9a-f]{40})$",
            "resolved_source_group": "source_sha",
        }
        jobs = [job("failure", name=f"Build main-windows (arm64) @ {source}"),
                job(name=f"Build main-windows (x64) @ {source}")]
        self.assertTrue(self.assess(jobs, target)["arm_specific_failure"])
        jobs[1]["name"] = f"Build main-windows (x64) @ {'b' * 40}"
        self.assertFalse(self.assess(jobs, target)["arm_specific_failure"])

    def test_other_built_source_refs_do_not_recover_or_override_reviewed_scope(self):
        current = config()
        channel = current["packages"][0]["channels"][0]
        channel["source_ref"] = {
            "pattern": r"^Build (?P<ref>.+) \((?:arm64|x64)\) @ [0-9a-f]{40}$",
            "group": "ref", "value": "main-windows",
        }
        channel["targets"] = [{
            "id": "cp314",
            "job": r"^Build main-windows \(arm64\) @ (?P<sha>[0-9a-f]{40})$",
            "amd64_job": r"^Build main-windows \(x64\) @ (?P<sha>[0-9a-f]{40})$",
            "resolved_source_group": "sha",
        }]
        api = FakeAPI(runs=[run(20, age=1), run(10, age=2)])

        def pages(path, key):
            ref, identifier = ("release/3.9.x-windows", 20) if "/runs/20/" in path else ("main-windows", 10)
            return [{**job(name=f"Build {ref} ({arch}) @ {'a' * 40}"), "run_id": identifier}
                    for arch in ("arm64", "x64")]

        api.pages = pages
        report, _ = scan(api, current, empty_state(), NOW)
        result = report["packages"][0]["results"]["wheel/main/cp314"]
        self.assertEqual(result["health"], "Healthy")
        self.assertEqual(result["run_id"], 10)
        self.assertEqual(len(result["out_of_scope_runs"]), 1)

    def test_same_job_not_a_control(self):
        target = {**CHANNEL["targets"][0], "amd64_job": "^Windows ARM64 cp314$"}
        self.assertFalse(self.assess([job("failure")], target)["arm_specific_failure"])

    def test_ambiguous_amd_selector_cannot_confirm_failure(self):
        jobs = platform_jobs("failure") + [job(name="Windows AMD64 cp314")]
        self.assertFalse(self.assess(jobs)["arm_specific_failure"])

    def test_job_collection_does_not_collapse_ambiguous_matrix_names(self):
        api = FakeAPI(jobs=platform_jobs("failure") + [job("failure", name="Windows AMD64 cp314")])
        records = attempt_jobs(api, "example/package", run())
        self.assertEqual(len(records), 3)
        self.assertFalse(self.assess(records)["arm_specific_failure"])

    def test_job_pagination_cannot_silently_drop_part_of_the_matrix(self):
        api = GitHub()
        responses = [{"total_count": 2, "jobs": [job()]}, {"total_count": 2, "jobs": []}]
        api.get = lambda path, params: responses.pop(0)
        with self.assertRaises(MonitorError):
            api.pages("repos/example/package/jobs", "jobs")

    def test_amd_aggregate_requires_all_equivalent_jobs_to_succeed(self):
        target = {**CHANNEL["targets"][0], "amd64_jobs": ["^AMD64 one$", "^AMD64 two$"]}
        target.pop("amd64_job")
        jobs = [job("failure"), job(name="AMD64 one"), job(name="AMD64 two")]
        jobs[1]["labels"] = jobs[2]["labels"] = ["windows-latest"]
        self.assertTrue(self.assess(jobs, target)["arm_specific_failure"])
        jobs[2]["conclusion"] = "skipped"
        self.assertFalse(self.assess(jobs, target)["arm_specific_failure"])
        jobs[2] = job("failure", name="AMD64 two")
        jobs[2]["labels"] = ["windows-latest"]
        self.assertFalse(self.assess(jobs, target)["arm_specific_failure"])

    def test_explicit_linux_comparator_cannot_create_windows_failure(self):
        target = {**CHANNEL["targets"][0], "amd64_job": "^Linux AMD64 cp314$"}
        self.assertFalse(self.assess([job("failure"), job(name="Linux AMD64 cp314")], target)["arm_specific_failure"])

    def test_x86_control_is_not_amd64(self):
        target = {**CHANNEL["targets"][0], "amd64_job": "^Windows x86 cp314$"}
        self.assertFalse(self.assess([job("failure"), job(name="Windows x86 cp314")], target)["arm_specific_failure"])

    def test_generic_64_bit_name_on_arm_host_is_not_amd64_proof(self):
        value = {**job(), "name": "Windows 64-bit", "labels": ["windows-11-arm"]}
        self.assertFalse(is_amd64(value))

    def test_failed_arm_tests_need_successful_amd_tests(self):
        jobs = platform_jobs()
        jobs[0].update(conclusion="failure", steps=[
            {"name": "Build wheel", "conclusion": "success"},
            {"name": "Test", "conclusion": "failure"},
        ])
        self.assertFalse(self.assess(jobs)["arm_specific_failure"])
        jobs[1]["steps"].append({"name": "Test", "conclusion": "success"})
        self.assertTrue(self.assess(jobs)["arm_specific_failure"])

    def test_partial_rerun_control_same_sha_is_accepted(self):
        jobs = platform_jobs("failure")
        jobs[0]["run_attempt"] = 2
        jobs[1]["run_attempt"] = 1
        self.assertTrue(self.assess(jobs)["arm_specific_failure"])

    def test_both_platforms_omitted_are_not_applicable(self):
        self.assertTrue(self.assess([])["not_applicable"])

    def test_no_build_run_is_inapplicable_even_when_source_guard_is_configured(self):
        target = {**CHANNEL["targets"][0], "comparison_blocker": "source resolution must be verified"}
        self.assertTrue(self.assess([], target)["not_applicable"])

    def test_shared_failure_cannot_open_or_recover_incident(self):
        old = {"cell": self.assess(platform_jobs("failure"))}
        shared = {"cell": self.assess(platform_jobs("failure", "failure"))}
        self.assertIsNone(plan({}, shared, 0))
        self.assertIsNone(plan(old, shared, 1))

    def test_non_build_run_is_ignored_instead_of_missing_target_failure(self):
        api = FakeAPI(runs=[run(20, age=1), run(10, age=2)])
        api.pages = lambda path, key: [] if "/runs/20/" in path else platform_jobs()
        report, _ = scan(api, config(), empty_state(), NOW)
        result = report["packages"][0]["results"]["wheel/main/cp314"]
        self.assertEqual(result["health"], "Healthy")
        self.assertEqual(result["run_id"], 10)
        self.assertEqual(len(result["ignored_runs"]), 1)

    def test_renamed_windows_builds_are_not_misclassified_as_no_build_run(self):
        jobs = [job(name="New Windows ARM64 build"), job(name="New Windows AMD64 build")]
        result = self.assess(jobs)
        self.assertEqual(result["condition"], "selector-drift")
        self.assertFalse(result.get("not_applicable", False))
        self.assertFalse(result["arm_specific_failure"])

    def test_arm_recovery_does_not_require_amd_to_be_green(self):
        old = {"cell": self.assess(platform_jobs("failure"))}
        recovered = {"cell": self.assess(platform_jobs("success", "failure"))}
        self.assertEqual(plan(old, recovered, 1)["changes"][0]["kind"], "recovery")

    def test_legacy_uncompared_failure_not_retained_after_policy_change(self):
        _, state = scan(FakeAPI(jobs=platform_jobs("failure")), config(), empty_state(), NOW)
        state.pop("failure_policy")
        report, _ = scan(FakeAPI(error="403"), config(), state, NOW)
        self.assertTrue(report["state_policy_migrated"])
        self.assertNotEqual(report["packages"][0]["health"], "Broken")
        self.assertEqual(report["failure_policy"], FAILURE_POLICY)

    def test_onboarding_placeholder_does_not_become_a_phantom_attention_target(self):
        setup = config()
        setup["packages"][0]["channels"] = []
        setup["packages"][0]["setup_reason"] = "Needs platform mapping"
        _, state = scan(FakeAPI(), setup, empty_state(), NOW)
        report, state = scan(FakeAPI(error="502"), config(), state, NOW)
        self.assertEqual(report["packages"][0]["health"], "Unknown")
        self.assertNotIn("setup", report["packages"][0]["results"])
        report, _ = scan(FakeAPI(), config(), state, NOW)
        self.assertEqual(report["packages"][0]["health"], "Healthy")

    def test_no_observation_state_can_accept_first_real_run(self):
        from monitoring.monitor import observe_channel
        prior = {"results": {"main/cp314": {
            "health": "Unknown", "condition": "no-observation", "detail": "No prior run", "jobs": [],
        }}}
        result = observe_channel(FakeAPI(), CHANNEL, prior, NOW)
        self.assertEqual(result["results"]["main/cp314"]["health"], "Healthy")

    def test_exact_platform_pair_preserves_python_and_abi(self):
        arm = {**job(), "name": "Build cp314t-win_arm64", "labels": ["windows-11-arm"]}
        amd = {**job(), "name": "Build cp314t-win_amd64", "labels": ["windows-latest"]}
        other_python = {**amd, "name": "Build cp313-win_amd64"}
        self.assertEqual(comparator(arm, [arm, amd, other_python]), amd)
        self.assertIsNone(comparator(arm, [arm, other_python]))

    def test_linux_control_cannot_match(self):
        arm = {**job(), "name": "Build Windows ARM64 cp314"}
        linux = {**job(), "name": "Build Linux x86_64 cp314"}
        self.assertIsNone(comparator(arm, [arm, linux]))

    def test_ambiguous_name_candidates_not_guessed(self):
        arm = {**job(), "name": "Build wheels on windows-11-arm"}
        amd = {**job(), "name": "Build wheels on windows-2022"}
        other = {**job(), "name": "Build wheels on windows-2025"}
        self.assertIsNone(comparator(arm, [arm, amd, other]))
