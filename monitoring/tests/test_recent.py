import copy
import unittest
from datetime import timedelta

from monitoring.monitor import COLLECTION_POLICY, evaluate, scan, stamp
from monitoring.recent import observe_recent, same_request, stream_identity
from monitoring.wheel_log import cibuildwheel_jobs
from test_monitor import CHANNEL, FakeAPI, NOW, config, empty_state, job, run


def recent_run(identifier=10, conclusion="success", age=2, event="pull_request", sha="abc", branch="feature", number=1):
    value = run(identifier, conclusion, age=age, branch=branch, event=event)
    value.update(head_sha=sha, head_repository={"id": 42, "full_name": "contributor/project"},
                 pull_requests=[{"number": number, "head": {"sha": sha, "repo": {"id": 42}}}])
    return value


def pair(identifier=10, arm="success", amd="success", sha="abc"):
    return [{**job(arm), "run_id": identifier, "head_sha": sha},
            {**job(amd, name="Windows AMD64 cp314"), "run_id": identifier, "head_sha": sha}]


def recent_config():
    data = config()
    data["collection_policy"] = COLLECTION_POLICY
    data["packages"][0]["channels"][0]["collection"] = "rolling-24h"
    data["packages"][0]["channels"][0]["events"].append("pull_request")
    return data


class RecentTests(unittest.TestCase):
    def test_all_runs_are_assessed_not_only_latest_success(self):
        runs = [recent_run(10, "failure", age=4), recent_run(20, age=2)]
        api = FakeAPI(runs=runs)
        api.pages = lambda path, key, params=None: pair(10, "failure") if "/runs/10/" in path else pair(20)
        report, _ = scan(api, recent_config(), empty_state(), NOW)
        package = report["packages"][0]
        self.assertEqual(len(package["observations"]), 2)
        self.assertTrue(package["observations"][0]["value"]["arm_specific_failure"])
        self.assertEqual(package["health"], "Healthy")
        self.assertEqual([c["kind"] for c in package["planned_notification"]["changes"]], ["incident", "recovery"])

    def test_failure_on_one_pr_not_cleared_by_another(self):
        api = FakeAPI(runs=[recent_run(10, "failure", age=4, number=1), recent_run(20, age=2, number=2)])
        api.pages = lambda path, key, params=None: pair(10, "failure") if "/runs/10/" in path else pair(20)
        report, _ = scan(api, recent_config(), empty_state(), NOW)
        self.assertEqual(report["packages"][0]["health"], "Broken")
        self.assertEqual(len(report["packages"][0]["results"]), 2)

    def test_forks_with_same_branch_and_commit_are_not_comparators(self):
        arm = recent_run()
        other = copy.deepcopy(arm)
        other["head_repository"]["id"] = 99
        self.assertFalse(same_request(other, arm))

    def test_push_success_cannot_be_pr_baseline(self):
        arm = recent_run()
        self.assertFalse(same_request(recent_run(event="push"), arm))

    def test_old_successful_sha_cannot_compare_new_pr(self):
        self.assertFalse(same_request(recent_run(sha="old"), recent_run(sha="new")))

    def test_missing_pr_identity_falls_back_to_immutable_commit(self):
        api = FakeAPI()
        api.pages = lambda *args: []
        value = recent_run()
        value["pull_requests"] = []
        self.assertEqual(stream_identity(api, CHANNEL, value), "pr/42/commit-abc")

    def test_pr_pending_is_rechecked_after_leaving_window(self):
        api = FakeAPI(runs=[])
        old = recent_run(age=30)
        api.get = lambda path, params=None: (
            {"state": "open"} if "/pulls/" in path else
            old if "/runs/10" in path else {"total_count": 0, "workflow_runs": []}
        )
        api.pages = lambda *args: pair()
        result = observe_recent(api, CHANNEL, {"pending_runs": [10]}, NOW)
        self.assertEqual(len(result["observations"]), 1)

    def test_empty_window_not_healthy_or_broken(self):
        result = observe_recent(FakeAPI(runs=[]), CHANNEL, {}, NOW)
        self.assertEqual(result["results"]["window"]["health"], "Unknown")

    def test_query_is_exactly_24_hours_and_not_success_filtered(self):
        api = FakeAPI(runs=[])
        observe_recent(api, CHANNEL, {}, NOW)
        parameters = api.paths[0][1]
        self.assertEqual(parameters["created"], stamp(NOW - timedelta(hours=24)) + ".." + stamp(NOW))
        self.assertNotIn("status", parameters)
        self.assertNotIn("event", parameters)
        self.assertNotIn("branch", parameters)

    def test_shared_failure_produces_no_incident(self):
        api = FakeAPI(runs=[recent_run(conclusion="failure")], jobs=pair(arm="failure", amd="failure"))
        report, _ = scan(api, recent_config(), empty_state(), NOW)
        self.assertIsNone(report["packages"][0]["planned_notification"])

    def test_replay_same_window_does_not_repeat_transitions(self):
        runs = [recent_run(10, "failure", age=4), recent_run(20, age=2)]
        api = FakeAPI(runs=runs)
        api.pages = lambda path, key, params=None: pair(10, "failure") if "/runs/10/" in path else pair(20)
        _, state = scan(api, recent_config(), empty_state(), NOW)
        report, _ = scan(api, recent_config(), state, NOW)
        self.assertIsNone(report["packages"][0]["planned_notification"])

    def test_late_old_run_rerun_cannot_open_incident_after_newer_pass(self):
        runs = [recent_run(10, age=4), recent_run(20, age=2)]
        api = FakeAPI(runs=runs)
        api.pages = lambda path, key, params=None: pair(10) if "/runs/10/" in path else pair(20)
        _, state = scan(api, recent_config(), empty_state(), NOW)
        api.runs[0]["run_attempt"] = 2
        api.pages = lambda path, key, params=None: pair(10, "failure") if "/runs/10/" in path else pair(20)
        report, _ = scan(api, recent_config(), state, NOW)
        self.assertEqual(report["packages"][0]["health"], "Healthy")
        self.assertIsNone(report["packages"][0]["planned_notification"])


class WheelLogTests(unittest.TestCase):
    def test_independent_builds_inside_one_physical_job(self):
        text = "\n".join([
            "Building cp313-win_amd64 wheel",
            "\x1b[32m\u2713 \x1b[0mcp313-win_amd64 finished in 42 seconds",
            "Building cp313-win_arm64 wheel",
            "cibuildwheel: error: Command returned non-zero exit status 1.",
        ])
        aggregate = {**job("failure", name="Build windows-latest wheels"), "id": 123}
        derived = cibuildwheel_jobs(aggregate, text)
        target = {"job": "^cibuildwheel cp313-win_arm64$", "amd64_job": "^cibuildwheel cp313-win_amd64$"}
        result = evaluate(derived, target, [{"stage": "build", "pattern": "^cibuildwheel build$"}])
        self.assertTrue(result["arm_specific_failure"])
        self.assertEqual(result["arm64"]["jobs"][0]["run_id"], result["amd64"]["jobs"][0]["run_id"])

    def test_job_failure_does_not_prove_incomplete_arm_block_failed(self):
        text = "\n".join(["Building cp313-win_amd64 wheel", "\u2713 cp313-win_amd64 finished in 42 seconds",
                          "Building cp313-win_arm64 wheel", "Downloading dependency"])
        records = cibuildwheel_jobs({**job("failure"), "id": 123}, text)
        self.assertIsNone(records[-1]["conclusion"])

    def test_upload_error_after_completed_block_does_not_change_its_success(self):
        text = "Building cp313-win_arm64 wheel\n\u2713 cp313-win_arm64 finished in a minute\ncibuildwheel: error: upload"
        records = cibuildwheel_jobs({**job("failure"), "id": 123}, text)
        self.assertEqual(records[0]["conclusion"], "success")

    def test_printed_plan_is_not_build_success(self):
        records = cibuildwheel_jobs({**job(), "id": 123}, "cp313-win_amd64, cp313-win_arm64\nAll wheels built")
        self.assertEqual(records, [])

    def test_timestamped_action_error_is_assigned_only_to_active_interpreter(self):
        text = "\n".join([
            "2026-10-20T10:00:00.000Z Building cp313-win_amd64 wheel",
            "2026-10-20T10:01:00.000Z \u2713 cp313-win_amd64 finished in a minute",
            "2026-10-20T10:02:00.000Z Building cp313-win_arm64 wheel",
            "2026-10-20T10:03:00.000Z ##[error]cibuildwheel: error: build failed",
        ])
        records = cibuildwheel_jobs({**job("failure"), "id": 123}, text)
        self.assertEqual([record["conclusion"] for record in records], ["success", "failure"])
