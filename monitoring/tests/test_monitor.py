import copy
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from monitoring.monitor import (
    GitHub, MonitorError, REPOSITORY, SCHEMA, attempt_jobs, discover,
    evaluate, literal, normalize, observe_channel, plan, restore, scan, stamp, summary, validate,
)


NOW = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)
CHANNEL = {
    "id": "wheel", "repository": "example/package", "workflow": "build.yml",
    "events": ["push"], "scopes": [{"id": "main", "ref": "^main$"}],
    "targets": [{"id": "cp314", "job": "^Windows ARM64 cp314$"}],
    "required_steps": [{"stage": "build", "pattern": "^Build wheel$"}],
    "baseline": "Verified against a recorded public run",
}


def config():
    return {"schema": SCHEMA, "tracker": REPOSITORY, "packages": [
        {"package": "package", "owner": "owner", "channels": [copy.deepcopy(CHANNEL)]},
    ]}


def empty_state():
    return {"schema": SCHEMA, "tracker": REPOSITORY, "packages": {}}


def job(conclusion="success", name="Windows ARM64 cp314", steps=None):
    return {
        "name": name, "conclusion": conclusion, "status": "completed", "head_sha": "abc",
        "html_url": "https://github.com/example/package/actions/runs/10/job/20",
        "steps": steps if steps is not None else [{"name": "Build wheel", "conclusion": conclusion}],
    }


def run(identifier=10, conclusion="success", age=2, attempt=1, branch="main", event="push"):
    return {
        "id": identifier, "run_attempt": attempt, "head_sha": "abc", "status": "completed",
        "conclusion": conclusion, "head_branch": branch, "event": event,
        "created_at": stamp(NOW - timedelta(hours=age + 1)),
        "updated_at": stamp(NOW - timedelta(hours=age)),
        "html_url": f"https://github.com/example/package/actions/runs/{identifier}",
    }


class FakeAPI:
    def __init__(self, runs=None, jobs=None, error=None):
        self.runs = runs if runs is not None else [run()]
        self.jobs = jobs if jobs is not None else [job()]
        self.error = error
        self.requests = 0
        self.paths = []

    def get(self, path, params=None):
        self.requests += 1
        self.paths.append((path, params))
        if self.error:
            raise MonitorError(self.error)
        return {"total_count": len(self.runs), "workflow_runs": self.runs}

    def pages(self, path, key, params=None):
        self.requests += 1
        self.paths.append((path, params))
        if self.error:
            raise MonitorError(self.error)
        return self.jobs


class ValidationTests(unittest.TestCase):
    def test_pilot_registry_is_valid(self):
        path = Path(__file__).parents[1] / "pilot.json"
        self.assertEqual(len(validate(json.loads(path.read_text()))["packages"]), 5)

    def test_normalization(self):
        self.assertEqual(normalize("zope.Interface"), "zope-interface")

    def test_duplicate_package(self):
        data = config()
        data["packages"] *= 2
        with self.assertRaises(MonitorError):
            validate(data)

    def test_selector_must_be_anchored(self):
        data = config()
        data["packages"][0]["channels"][0]["targets"][0]["job"] = "arm64"
        with self.assertRaises(MonitorError):
            validate(data)

    def test_invalid_regex(self):
        data = config()
        data["packages"][0]["channels"][0]["targets"][0]["job"] = "^[$"
        with self.assertRaises(MonitorError):
            validate(data)

    def test_invalid_repo(self):
        data = config()
        data["packages"][0]["channels"][0]["repository"] = "https://example.com/?token="
        with self.assertRaises(MonitorError):
            validate(data)

    def test_pull_requests_not_enrolled(self):
        data = config()
        data["packages"][0]["channels"][0]["events"] = ["pull_request"]
        with self.assertRaises(MonitorError):
            validate(data)

    def test_state_schema_mismatch(self):
        with self.assertRaises(MonitorError):
            scan(FakeAPI(), config(), {"schema": 99}, NOW)

    def test_inventory_contains_all_candidates_and_pilot(self):
        root = Path(__file__).parents[1]
        inventory = json.loads((root / "inventory.json").read_text())
        pilot = validate(json.loads((root / "pilot.json").read_text()))
        names = {p["package"] for p in inventory["packages"]}
        self.assertEqual(len(names), 84)
        self.assertTrue({p["package"] for p in pilot["packages"]}.issubset(names))


class EvaluationTests(unittest.TestCase):
    def assess(self, jobs):
        return evaluate(jobs, CHANNEL["targets"][0], CHANNEL["required_steps"])

    def test_success(self):
        self.assertEqual(self.assess([job()])["health"], "Healthy")

    def test_missing_job_is_not_green(self):
        self.assertEqual(self.assess([])["health"], "Attention")

    def test_other_arm64_platform_not_selected(self):
        self.assertEqual(self.assess([job(name="macOS ARM64 cp314")])["condition"], "coverage")

    def test_build_failure(self):
        result = self.assess([job("failure")])
        self.assertEqual((result["health"], result["condition"]), ("Broken", "build-failure"))

    def test_allowed_failure_step_still_broken(self):
        self.assertEqual(self.assess([job(steps=[{"name": "Build wheel", "conclusion": "failure"}])])["health"], "Broken")

    def test_build_success_does_not_mask_test_failure(self):
        steps = [{"name": "Build wheel", "conclusion": "success"}, {"name": "Test", "conclusion": "failure"}]
        self.assertEqual(self.assess([job("failure", steps=steps)])["health"], "Broken")

    def test_cancelled_not_recovery(self):
        self.assertEqual(self.assess([job("cancelled")])["condition"], "pending")

    def test_missing_required_step(self):
        self.assertEqual(self.assess([job(steps=[])])["health"], "Attention")

    def test_required_skipped_job(self):
        self.assertEqual(self.assess([job("skipped", steps=[])])["health"], "Attention")

    def test_multiple_matches_cannot_hide_failure(self):
        self.assertEqual(self.assess([job(), job("failure")])["health"], "Broken")


class CollectionTests(unittest.TestCase):
    def test_failed_workflow_is_not_filtered_out(self):
        result = observe_channel(FakeAPI([run(conclusion="failure")], [job("failure")]), CHANNEL, {}, NOW)
        self.assertEqual(result["results"]["main/cp314"]["health"], "Broken")

    def test_unrelated_workflow_failure_does_not_break_windows(self):
        result = observe_channel(FakeAPI([run(conclusion="failure")]), CHANNEL, {}, NOW)
        self.assertEqual(result["results"]["main/cp314"]["health"], "Healthy")

    def test_pr_and_wrong_ref_ignored(self):
        api = FakeAPI([run(event="pull_request"), run(20, branch="other")])
        self.assertEqual(observe_channel(api, CHANNEL, {}, NOW)["results"]["main/cp314"]["health"], "Unknown")

    def test_old_rerun_cannot_clear_new_failure(self):
        api = FakeAPI([run(9, attempt=2, age=10), run(10)])
        observe_channel(api, CHANNEL, {}, NOW)
        self.assertIn("/runs/10/attempts/1/jobs", api.paths[-1][0])

    def test_settlement_uses_latest_attempt(self):
        api = FakeAPI([run(age=0.1)])
        self.assertEqual(observe_channel(api, CHANNEL, {}, NOW)["results"]["main/cp314"]["health"], "Unknown")

    def test_cancelled_workflow_is_not_a_success(self):
        api = FakeAPI([run(conclusion="cancelled")])
        self.assertEqual(observe_channel(api, CHANNEL, {}, NOW)["results"]["main/cp314"]["health"], "Unknown")

    def test_rerun_merges_same_sha_previous_jobs(self):
        api = FakeAPI()
        api.pages = lambda path, key: [job(name="unrerun")] if "/1/" in path else [job("failure")]
        jobs = attempt_jobs(api, "example/package", run(attempt=2))
        self.assertEqual({j["name"] for j in jobs}, {"unrerun", "Windows ARM64 cp314"})

    def test_rerun_must_not_mix_sha(self):
        api = FakeAPI(jobs=[{**job(), "head_sha": "different"}])
        with self.assertRaises(MonitorError):
            attempt_jobs(api, "example/package", run())

    def test_rerun_updates_same_job(self):
        api = FakeAPI()
        api.pages = lambda path, key: [job("failure")] if "/1/" in path else [job()]
        self.assertEqual(attempt_jobs(api, "example/package", run(attempt=2))[0]["conclusion"], "success")

    def test_manual_builder_has_no_generic_stale_alarm(self):
        result = observe_channel(FakeAPI([run(age=500)]), CHANNEL, {}, NOW)
        self.assertEqual(result["results"]["main/cp314"]["health"], "Healthy")

    def test_regular_stale_result(self):
        channel = {**CHANNEL, "max_silence_hours": 72}
        result = observe_channel(FakeAPI([run(age=100)]), channel, {}, NOW)
        self.assertEqual(result["results"]["main/cp314"]["condition"], "stale")

    def test_stale_does_not_erase_failure(self):
        channel = {**CHANNEL, "max_silence_hours": 72}
        result = observe_channel(FakeAPI([run(age=100)], [job("failure")]), channel, {}, NOW)
        self.assertEqual(result["results"]["main/cp314"]["health"], "Broken")

    def test_missing_target_does_not_erase_known_failure(self):
        prior = observe_channel(FakeAPI(jobs=[job("failure")]), CHANNEL, {}, NOW)
        current = observe_channel(FakeAPI([run(20)], jobs=[]), CHANNEL, prior, NOW)
        self.assertEqual(current["results"]["main/cp314"]["health"], "Broken")
        self.assertIn("missing", current["results"]["main/cp314"]["warning"])

    def test_no_success_shaped_history_truncation(self):
        api = FakeAPI()
        responses = [{"total_count": 101, "workflow_runs": [run()]}, {"workflow_runs": []}]
        api.get = lambda path, params: responses.pop(0)
        with self.assertRaises(MonitorError):
            discover(api, CHANNEL, NOW - timedelta(days=1), NOW)

    def test_busy_window_is_split_and_deduplicated(self):
        api = FakeAPI()
        start = NOW - timedelta(days=1)
        full = f"{stamp(start)}..{stamp(NOW)}"

        def response(path, params):
            if params["created"] == full:
                return {"total_count": 1001, "workflow_runs": []}
            return {"total_count": 1, "workflow_runs": [run()]}

        api.get = response
        self.assertEqual(len(discover(api, CHANNEL, start, NOW)), 1)


class TransitionTests(unittest.TestCase):
    def test_initial_healthy_no_ping(self):
        self.assertIsNone(plan({}, {"cell": {"health": "Healthy", "condition": "pass"}}, 0))

    def test_repeated_failure_no_ping(self):
        result = {"cell": {"health": "Broken", "condition": "build-failure"}}
        self.assertIsNone(plan(result, result, 1))

    def test_recurrence_has_new_sequence(self):
        good = {"cell": {"health": "Healthy", "condition": "pass"}}
        bad = {"cell": {"health": "Broken", "condition": "build-failure"}}
        self.assertEqual(plan(good, bad, 2)["sequence"], 3)

    def test_recovery(self):
        old = {"cell": {"health": "Broken", "condition": "build-failure"}}
        new = {"cell": {"health": "Healthy", "condition": "pass"}}
        self.assertEqual(plan(old, new, 1)["changes"][0]["kind"], "recovery")

    def test_scope_independence(self):
        old = {"main/cp314": {"health": "Broken", "condition": "build-failure"}}
        new = {**old, "release/cp314": {"health": "Healthy", "condition": "pass"}}
        self.assertIsNone(plan(old, new, 1))

    def test_removed_target_not_a_recovery(self):
        old = {"cell": {"health": "Broken", "condition": "build-failure"}}
        self.assertEqual(plan(old, {}, 1)["changes"][0]["kind"], "setup-change")

    def test_scan_does_not_mutate_input(self):
        state = empty_state()
        scan(FakeAPI(), config(), state, NOW)
        self.assertEqual(state, empty_state())

    def test_errors_preserve_known_broken(self):
        _, state = scan(FakeAPI(jobs=[job("failure")]), config(), empty_state(), NOW)
        report, state = scan(FakeAPI(error="403"), config(), state, NOW, scheduled=True)
        self.assertEqual(report["packages"][0]["health"], "Broken")
        self.assertTrue(report["errors"])

    def test_gap_alert_after_two_scheduled_checks(self):
        api = FakeAPI(error="403")
        first, state = scan(api, config(), empty_state(), NOW, scheduled=True)
        self.assertIsNone(first["packages"][0]["planned_notification"])
        second, _ = scan(api, config(), state, NOW + timedelta(days=1), scheduled=True)
        self.assertIsNotNone(second["packages"][0]["planned_notification"])

    def test_manual_checks_do_not_increment_gap_threshold(self):
        _, state = scan(FakeAPI(error="403"), config(), empty_state(), NOW)
        self.assertEqual(state["packages"]["package"]["gap_polls"], 0)

    def test_gap_recovery_does_not_leave_phantom_missing_target(self):
        _, state = scan(FakeAPI(error="403"), config(), empty_state(), NOW, scheduled=True)
        report, _ = scan(FakeAPI(), config(), state, NOW + timedelta(days=1), scheduled=True)
        self.assertEqual(report["packages"][0]["health"], "Healthy")
        self.assertEqual(len(report["packages"][0]["results"]), 1)

    def test_removed_failed_target_retains_failure(self):
        original = config()
        original["packages"][0]["channels"][0]["targets"].append(
            {"id": "cp314t", "job": "^Windows ARM64 cp314t$"}
        )
        _, state = scan(FakeAPI(jobs=[job(), job("failure", name="Windows ARM64 cp314t")]),
                        original, empty_state(), NOW)
        report, _ = scan(FakeAPI(), config(), state, NOW)
        self.assertEqual(report["packages"][0]["health"], "Broken")
        self.assertEqual(report["packages"][0]["results"]["wheel/main/cp314t"]["condition"], "setup-change")

    def test_summary_neutralizes_mentions(self):
        self.assertNotIn("@victim", literal("error @victim"))


class APITests(unittest.TestCase):
    def test_api_is_get_only(self):
        response = subprocess.CompletedProcess([], 0, b"HTTP/2.0 200 OK\nx-ratelimit-remaining: 500\n\n{\"ok\": true}", b"")
        with patch("subprocess.run", return_value=response) as process:
            api = GitHub()
            self.assertTrue(api.get("repos/owner/repo")["ok"])
            self.assertIn("GET", process.call_args.args[0])
            self.assertEqual(api.remaining, 500)

    def test_disallow_external_host(self):
        with self.assertRaises(MonitorError):
            GitHub().get("https://evil.example")

    def test_budget(self):
        with self.assertRaises(MonitorError):
            GitHub(budget=0).get("repos/owner/repo")

    def test_per_package_budget(self):
        api = GitHub()
        api.package_limit = 0
        with self.assertRaises(MonitorError):
            api.get("repos/owner/repo")

    def test_pagination_more_than_one_hundred(self):
        api = GitHub()
        responses = [list(range(100)), [100]]
        api.get = lambda path, params: responses.pop(0)
        self.assertEqual(len(api.pages("repos/owner/repo")), 101)

    def test_auth_error_not_retried_or_defaulted(self):
        response = subprocess.CompletedProcess([], 1, b"HTTP/2.0 403 Forbidden\n\n{}", b"forbidden")
        with patch("subprocess.run", return_value=response) as process:
            with self.assertRaises(MonitorError):
                GitHub().get("repos/owner/repo")
            self.assertEqual(process.call_count, 1)

    def test_transient_error_retries(self):
        responses = [
            subprocess.CompletedProcess([], 1, b"HTTP/2.0 503 Unavailable\n\n{}", b""),
            subprocess.CompletedProcess([], 0, b"HTTP/2.0 200 OK\n\n{}", b""),
        ]
        with patch("subprocess.run", side_effect=responses):
            self.assertEqual(GitHub(sleeper=lambda _: None).get("repos/owner/repo"), {})


class RestoreTests(unittest.TestCase):
    def test_first_execution_has_no_state(self):
        api = FakeAPI()
        api.pages = lambda *args: []
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(restore(api, directory, 100))

    def test_previous_runs_without_state_fail(self):
        api = FakeAPI()
        api.pages = lambda path, *args: (
            [{"id": 99, "event": "schedule"}] if path.endswith("/runs") else []
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(MonitorError):
                restore(api, directory, 100)

    def test_pr_artifacts_cannot_become_state(self):
        api = FakeAPI()
        api.pages = lambda path, *args: [{"id": 99, "event": "pull_request"}]
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(restore(api, directory, 100))

    def test_restores_trusted_main_workflow_artifact(self):
        api = FakeAPI()
        api.pages = lambda path, *args: (
            [{"id": 99, "event": "workflow_dispatch"}] if path.endswith("/runs")
            else [{"name": "wheel-monitor-report", "expired": False}]
        )

        def download(command, **kwargs):
            destination = Path(command[-1])
            (destination / "state.json").write_text(json.dumps(empty_state()))
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch("subprocess.run", side_effect=download), tempfile.TemporaryDirectory() as directory:
            self.assertEqual(restore(api, directory, 100), 99)
            self.assertEqual(json.loads((Path(directory) / "state.json").read_text()), empty_state())


if __name__ == "__main__":
    unittest.main()
