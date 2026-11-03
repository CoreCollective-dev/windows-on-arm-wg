import copy
import unittest
from datetime import timedelta

from monitoring.baselines import definition, retain_health, supersedes_failure
from monitoring.monitor import APIError, MonitorError, scan
from test_monitor import FakeAPI, NOW, empty_state
from test_recent import pair, recent_config, recent_run


class BaselineTests(unittest.TestCase):
    def config(self):
        data = recent_config()
        data["packages"][0]["channels"][0]["baseline_search"] = False
        return data

    def initial(self, arm="success", amd="success"):
        api = FakeAPI(runs=[recent_run(conclusion="failure" if arm == "failure" else "success")],
                      jobs=pair(arm=arm, amd=amd))
        report, state = scan(api, self.config(), empty_state(), NOW)
        return report, state

    def test_quiet_day_preserves_success_and_reports_no_changes(self):
        _, state = self.initial()
        report, _ = scan(FakeAPI(runs=[]), self.config(), state, NOW + timedelta(days=1))
        package = report["packages"][0]
        self.assertEqual(package["health"], "Healthy")
        self.assertEqual(package["activity"], "No changes - no new applicable builds")
        self.assertTrue(package["last_verified_at"])
        self.assertIsNone(package["planned_notification"])

    def test_quiet_day_preserves_failure(self):
        _, state = self.initial("failure")
        report, _ = scan(FakeAPI(runs=[]), self.config(), state, NOW + timedelta(days=1))
        self.assertEqual(report["packages"][0]["health"], "Broken")
        self.assertIn("No changes", report["packages"][0]["activity"])

    def test_shared_failure_does_not_replace_verified_success(self):
        _, state = self.initial()
        api = FakeAPI(runs=[recent_run(20, "failure")], jobs=pair(20, "failure", "failure"))
        report, _ = scan(api, self.config(), state, NOW)
        self.assertEqual(report["packages"][0]["health"], "Healthy")
        self.assertEqual(report["packages"][0]["activity"], "Comparison inconclusive")

    def test_newer_shared_failure_supersedes_known_failure_without_recovery(self):
        _, state = self.initial("failure")
        api = FakeAPI(runs=[recent_run(20, "failure")], jobs=pair(20, "failure", "failure"))
        report, saved = scan(api, self.config(), state, NOW)
        package = report["packages"][0]
        self.assertEqual(package["health"], "Unknown")
        self.assertIsNone(package["planned_notification"])
        current = next(iter(package["last_known"].values()))
        self.assertTrue(current["shared_failure"])
        self.assertFalse(current["arm_specific_failure"])
        previous = next(iter(package["superseded"].values()))
        self.assertEqual(previous["run_id"], 10)
        self.assertTrue(previous["arm_specific_failure"])
        self.assertEqual(previous["superseded_by"]["run_id"], 20)
        self.assertTrue(saved["packages"]["package"]["channels"]["wheel"]["superseded"])
        self.assertTrue(next(iter(saved["packages"]["package"]["notification_results"].values()))["shared_failure"])

    def test_shared_failure_does_not_supersede_a_different_pr_or_fork(self):
        for number, origin in ((2, 42), (1, 99)):
            with self.subTest(number=number, origin=origin):
                _, state = self.initial("failure")
                candidate = recent_run(20, "failure", number=number)
                candidate["head_repository"]["id"] = origin
                candidate["pull_requests"][0]["head"]["repo"]["id"] = origin
                report, _ = scan(FakeAPI(runs=[candidate], jobs=pair(20, "failure", "failure")),
                                 self.config(), state, NOW)
                self.assertEqual(report["packages"][0]["health"], "Broken")
                self.assertFalse(report["packages"][0]["superseded"])

    def test_old_or_same_attempt_shared_failure_does_not_supersede_failure(self):
        for candidate in (recent_run(9, "failure", age=3), recent_run(10, "failure")):
            with self.subTest(run=candidate["id"]):
                _, state = self.initial("failure")
                report, _ = scan(FakeAPI(runs=[candidate], jobs=pair(candidate["id"], "failure", "failure")),
                                 self.config(), state, NOW)
                self.assertEqual(report["packages"][0]["health"], "Broken")
                self.assertFalse(report["packages"][0]["superseded"])

    def test_newer_rerun_shared_failure_can_supersede_same_run(self):
        _, state = self.initial("failure")
        candidate = recent_run(10, "failure")
        candidate["run_attempt"] = 2
        report, _ = scan(FakeAPI(runs=[candidate], jobs=pair(10, "failure", "failure")),
                         self.config(), state, NOW)
        self.assertEqual(report["packages"][0]["health"], "Unknown")
        self.assertEqual(next(iter(report["packages"][0]["superseded"].values()))["superseded_by"]["attempt"], 2)

    def test_newer_control_run_or_attempt_can_supersede_same_arm_run(self):
        report, _ = self.initial("failure")
        previous = next(iter(report["packages"][0]["last_known"].values()))
        for run_id, attempt in ((20, 1), (10, 2)):
            with self.subTest(control_run=run_id, control_attempt=attempt):
                shared = copy.deepcopy(previous)
                shared.update(health="Unknown", arm_specific_failure=False, shared_failure=True)
                shared["amd64"]["jobs"][0].update(run_id=run_id, run_attempt=attempt)
                self.assertTrue(supersedes_failure(previous, shared))
                self.assertFalse(supersedes_failure(shared, previous))

    def test_skipped_pending_missing_and_incomparable_controls_preserve_failure(self):
        variants = [pair(20, "failure", status) for status in ("skipped", "cancelled", None)]
        variants.append(pair(20, "failure")[:1])
        mismatched = pair(20, "failure", "failure")
        mismatched[1]["head_sha"] = "other"
        variants.append(mismatched)
        for jobs in variants:
            with self.subTest(jobs=jobs):
                _, state = self.initial("failure")
                api = FakeAPI(runs=[recent_run(20, "failure")], jobs=jobs)
                api.jobs = jobs
                report, _ = scan(api, self.config(), state, NOW)
                self.assertEqual(report["packages"][0]["health"], "Broken")
                self.assertFalse(report["packages"][0]["superseded"])

    def test_pr_lifecycle_error_does_not_apply_shared_supersession(self):
        _, state = self.initial("failure")
        api = FakeAPI(runs=[recent_run(20, "failure")], jobs=pair(20, "failure", "failure"))
        original = api.get
        def get(path, params=None):
            if "/pulls/" in path:
                raise MonitorError("503")
            return original(path, params)
        api.get = get
        report, _ = scan(api, self.config(), state, NOW)
        self.assertEqual(report["packages"][0]["health"], "Broken")
        self.assertFalse(report["packages"][0]["superseded"])
        self.assertTrue(report["errors"])

    def test_quiet_days_preserve_shared_unknown_and_history(self):
        _, state = self.initial("failure")
        _, state = scan(FakeAPI(runs=[recent_run(20, "failure")], jobs=pair(20, "failure", "failure")),
                        self.config(), state, NOW)
        report, _ = scan(FakeAPI(runs=[]), self.config(), state, NOW + timedelta(days=1))
        self.assertEqual(report["packages"][0]["health"], "Unknown")
        self.assertEqual(next(iter(report["packages"][0]["superseded"].values()))["run_id"], 10)
        self.assertIsNone(report["packages"][0]["planned_notification"])

    def test_new_arm_regression_after_shared_is_new_incident_not_suppressed(self):
        _, state = self.initial("failure")
        _, state = scan(FakeAPI(runs=[recent_run(20, "failure")], jobs=pair(20, "failure", "failure")),
                        self.config(), state, NOW)
        report, _ = scan(FakeAPI(runs=[recent_run(30, "failure")], jobs=pair(30, "failure")),
                         self.config(), state, NOW)
        self.assertEqual(report["packages"][0]["health"], "Broken")
        self.assertEqual([change["kind"] for change in report["packages"][0]["planned_notification"]["changes"]],
                         ["incident"])

    def test_genuine_passing_stage_after_shared_can_report_real_recovery(self):
        _, state = self.initial("failure")
        _, state = scan(FakeAPI(runs=[recent_run(20, "failure")], jobs=pair(20, "failure", "failure")),
                        self.config(), state, NOW)
        report, _ = scan(FakeAPI(runs=[recent_run(30)], jobs=pair(30)), self.config(), state, NOW)
        self.assertEqual(report["packages"][0]["health"], "Healthy")
        self.assertEqual([change["kind"] for change in report["packages"][0]["planned_notification"]["changes"]],
                         ["recovery"])

    def test_shared_failure_on_one_pr_leaves_another_active_regression_broken(self):
        first = FakeAPI(runs=[recent_run(10, "failure", number=1), recent_run(11, "failure", number=2)])
        first.pages = lambda path, key, params=None: pair(11 if "/runs/11/" in path else 10, "failure")
        _, state = scan(first, self.config(), empty_state(), NOW)
        report, _ = scan(FakeAPI(runs=[recent_run(20, "failure", number=1)], jobs=pair(20, "failure", "failure")),
                         self.config(), state, NOW)
        package = report["packages"][0]
        self.assertEqual(package["health"], "Broken")
        self.assertEqual(len(package["superseded"]), 1)
        self.assertEqual(sum(v.get("arm_specific_failure", False) for v in package["last_known"].values()), 1)

    def test_api_error_retains_known_health_with_warning(self):
        _, state = self.initial()
        report, _ = scan(FakeAPI(error="503"), self.config(), state, NOW)
        self.assertEqual(report["packages"][0]["health"], "Healthy")
        self.assertEqual(report["packages"][0]["activity"], "Monitoring warning")
        self.assertTrue(report["errors"])

    def test_wrong_pr_cannot_recover_failure_after_it_leaves_window(self):
        _, state = self.initial("failure")
        api = FakeAPI(runs=[recent_run(20, number=2)], jobs=pair(20))
        report, _ = scan(api, self.config(), state, NOW + timedelta(days=1))
        self.assertEqual(report["packages"][0]["health"], "Broken")
        self.assertEqual(len(report["packages"][0]["last_known"]), 2)

    def test_recovery_same_pr_updates_last_known(self):
        _, state = self.initial("failure")
        report, _ = scan(FakeAPI(runs=[recent_run(20)], jobs=pair(20)), self.config(), state, NOW)
        self.assertEqual(report["packages"][0]["health"], "Healthy")
        self.assertEqual(report["packages"][0]["planned_notification"]["changes"][0]["kind"], "recovery")

    def test_never_verified_remains_unknown_not_fake_green(self):
        report, _ = scan(FakeAPI(runs=[]), self.config(), empty_state(), NOW)
        self.assertEqual(report["packages"][0]["health"], "Unknown")
        self.assertTrue(report["packages"][0]["baseline_warnings"])

    def test_seed_is_validated_by_real_job_results(self):
        config = self.config()
        channel = config["packages"][0]["channels"][0]
        channel.update(baseline_search=True, baseline_run_id=10)
        seed = {**recent_run(age=100), "path": ".github/workflows/build.yml"}
        api = FakeAPI(runs=[], jobs=pair())
        api.get = lambda path, params=None: (
            {"state": "open"} if "/pulls/" in path else
            seed if "/runs/10" in path else {"total_count": 0, "workflow_runs": []}
        )
        report, state = scan(api, config, empty_state(), NOW)
        self.assertEqual(report["packages"][0]["health"], "Healthy")
        self.assertIsNone(report["packages"][0]["planned_notification"])
        self.assertEqual(state["packages"]["package"]["channels"]["wheel"]["baseline"]["candidate_runs"], [10])

    def test_seed_wrong_workflow_is_error_not_success(self):
        config = self.config()
        config["packages"][0]["channels"][0].update(baseline_search=True, baseline_run_id=10)
        api = FakeAPI(runs=[])
        api.get = lambda path, params=None: {**recent_run(age=100), "path": ".github/workflows/other.yml"} if "/runs/10" in path else {"total_count": 0, "workflow_runs": []}
        report, _ = scan(api, config, empty_state(), NOW)
        self.assertEqual(report["packages"][0]["health"], "Unknown")
        self.assertTrue(report["errors"])

    def test_newer_failure_wins_over_older_successful_seed(self):
        config = self.config()
        config["packages"][0]["channels"][0].update(baseline_search=True, baseline_run_id=10)
        seed = {**recent_run(age=100), "path": ".github/workflows/build.yml"}
        newest = {**recent_run(20, "failure", age=50), "path": ".github/workflows/build.yml"}
        api = FakeAPI(runs=[])
        api.get = lambda path, params=None: (
            {"state": "open"} if "/pulls/" in path else seed if "/runs/10" in path
            else {"total_count": 0, "workflow_runs": []} if params and params.get("created", "").startswith("2026-10-18")
            else {"total_count": 1, "workflow_runs": [newest]}
        )
        api.pages = lambda path, key, params=None: pair(20, "failure") if "/runs/20/" in path else pair()
        report, _ = scan(api, config, empty_state(), NOW)
        self.assertEqual(report["packages"][0]["health"], "Broken")
        self.assertIsNone(report["packages"][0]["planned_notification"])

    def test_historical_search_never_uses_a_success_only_filter(self):
        config = self.config()
        config["packages"][0]["channels"][0]["baseline_search"] = True
        api = FakeAPI(runs=[])
        scan(api, config, empty_state(), NOW)
        queries = [params for path, params in api.paths if "/workflows/" in path]
        self.assertTrue(queries)
        self.assertTrue(all("status" not in params for params in queries))

    def test_closed_unmerged_pr_retired_not_marked_fixed(self):
        _, state = self.initial("failure")
        api = FakeAPI(runs=[])
        api.get = lambda path, params=None: {"state": "closed", "merged_at": None} if "/pulls/" in path else {"total_count": 0, "workflow_runs": []}
        report, _ = scan(api, self.config(), state, NOW + timedelta(days=1))
        package = report["packages"][0]
        self.assertNotEqual(package["health"], "Broken")
        self.assertIsNone(package["planned_notification"])
        retired = next(iter(package["retired"].values()))
        self.assertIn("not a recovery", retired["retirement"])

    def test_baseline_initialized_only_once(self):
        _, state = self.initial()
        config = self.config()
        channel = config["packages"][0]["channels"][0]
        channel["baseline_search"] = True
        api = FakeAPI(runs=[])
        report, _ = scan(api, config, state, NOW + timedelta(days=1))
        self.assertEqual(report["packages"][0]["health"], "Healthy")
        self.assertFalse(any("/workflows/" in path and params and params.get("created", "").startswith("2026-07")
                             for path, params in api.paths))

    def test_missing_seed_uses_discovery_with_an_explicit_note(self):
        config = self.config()
        config["packages"][0]["channels"][0].update(baseline_search=True, baseline_run_id=999)
        seed = {**recent_run(age=100), "path": ".github/workflows/build.yml"}
        api = FakeAPI(runs=[], jobs=pair())

        def get(path, params=None):
            if "/runs/999" in path:
                raise APIError(path, 404)
            if "/pulls/" in path:
                return {"state": "open"}
            if params and params.get("created", "").startswith("2026-10-18"):
                return {"total_count": 0, "workflow_runs": []}
            return {"total_count": 1, "workflow_runs": [seed]}

        api.get = get
        report, _ = scan(api, config, empty_state(), NOW)
        self.assertEqual(report["packages"][0]["health"], "Healthy")
        self.assertTrue(any("no longer accessible" in note for note in report["packages"][0]["history_warnings"]))

    def test_invalid_baseline_run_id_is_rejected_before_reads(self):
        from monitoring.monitor import validate
        config = self.config()
        config["packages"][0]["channels"][0]["baseline_run_id"] = "successful"
        with self.assertRaises(MonitorError):
            validate(config)

    def test_expired_step_evidence_does_not_seed_a_green_result(self):
        config = self.config()
        config["packages"][0]["channels"][0]["baseline_search"] = True
        seed = {**recent_run(age=100), "path": ".github/workflows/build.yml"}
        api = FakeAPI(runs=[])
        api.get = lambda path, params=None: {"total_count": 0, "workflow_runs": []} if params and params.get("created", "").startswith("2026-10-18") else {"total_count": 1, "workflow_runs": [seed]}
        api.pages = lambda *args: [{**value, "steps": []} for value in pair()]
        report, _ = scan(api, config, empty_state(), NOW)
        self.assertEqual(report["packages"][0]["health"], "Unknown")
        self.assertTrue(report["packages"][0]["history_warnings"])

    def test_healthy_baseline_and_failure_survive_beyond_90_days(self):
        for conclusion, health in (("success", "Healthy"), ("failure", "Broken")):
            with self.subTest(health=health):
                _, state = self.initial(conclusion)
                report, _ = scan(FakeAPI(runs=[]), self.config(), state, NOW + timedelta(days=100))
                self.assertEqual(report["packages"][0]["health"], health)

    def test_changed_selector_does_not_keep_old_green_baseline(self):
        _, state = self.initial()
        config = self.config()
        config["packages"][0]["channels"][0]["targets"][0]["amd64_job"] = "^Different AMD64 job$"
        report, _ = scan(FakeAPI(runs=[]), config, state, NOW)
        self.assertEqual(report["packages"][0]["health"], "Unknown")

    def test_changing_seed_does_not_discard_known_failure(self):
        _, state = self.initial("failure")
        config = self.config()
        config["packages"][0]["channels"][0]["baseline_run_id"] = 999
        report, _ = scan(FakeAPI(runs=[]), config, state, NOW + timedelta(days=1))
        self.assertEqual(report["packages"][0]["health"], "Broken")
