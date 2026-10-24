import copy
import unittest
from datetime import timedelta

from monitoring.monitor import MonitorError, scan
from monitoring.recent import resolve_pull_requests, stream_identity
from test_monitor import FakeAPI, NOW, empty_state
from test_recent import pair, recent_config, recent_run


def pull(sha="new", origin=42, number=9, branch="feature"):
    return {"number": number, "state": "open", "merged_at": None,
            "head": {"sha": sha, "ref": branch,
                     "repo": {"id": origin, "full_name": "contributor/project"}}}


class IdentityTests(unittest.TestCase):
    def test_omitted_association_is_resolved_by_repo_branch_and_commit_membership(self):
        run = recent_run(sha="old")
        run["pull_requests"] = []
        api = FakeAPI()
        api.pages = lambda path, key=None, params=None: (
            [{"sha": "old"}, {"sha": "new"}] if path.endswith("/commits") else [pull()]
        )
        self.assertEqual(stream_identity(api, {"repository": "example/package"}, run), "pr/42/9")

    def test_branch_name_alone_does_not_prove_old_commit_membership(self):
        run = recent_run(sha="old")
        run["pull_requests"] = []
        api = FakeAPI()
        api.pages = lambda path, key=None, params=None: [{"sha": "new"}] if path.endswith("/commits") else [pull()]
        self.assertEqual(stream_identity(api, {"repository": "example/package"}, run), "pr/42/commit-old")

    def test_other_fork_is_not_a_pr_identity_match(self):
        run = recent_run()
        run["pull_requests"] = []
        api = FakeAPI()
        api.pages = lambda *args, **kwargs: [pull(sha="abc", origin=99)]
        self.assertEqual(resolve_pull_requests(api, {"repository": "example/package"}, run), [])

    def test_ambiguous_reused_branch_prs_do_not_merge(self):
        run = recent_run()
        run["pull_requests"] = []
        api = FakeAPI()
        api.pages = lambda *args, **kwargs: [pull(sha="abc", number=8), pull(sha="abc", number=9)]
        self.assertEqual(resolve_pull_requests(api, {"repository": "example/package"}, run), [])


class CatchupTests(unittest.TestCase):
    def fixture(self, recovered=True):
        config = recent_config()
        channel = config["packages"][0]["channels"][0]
        channel.update(baseline_search=False)
        failed = recent_run(10, "failure", age=4, sha="old")
        failed["pull_requests"] = []
        first = FakeAPI(runs=[failed], jobs=pair(10, arm="failure", sha="old"))
        first.pages = lambda path, key=None, params=None: (
            [] if path.endswith("/pulls") else pair(10, arm="failure", sha="old")
        )
        _, state = scan(first, config, empty_state(), NOW)
        state["packages"]["package"]["pending_notifications"] = []
        channel["recovery_catchup"] = True
        passed = recent_run(20, age=2, sha="new")
        passed["pull_requests"] = []
        later = NOW + timedelta(days=2)
        api = FakeAPI(runs=[])

        def get(path, params=None):
            api.requests += 1
            if "/pulls/" in path:
                return pull()
            if "/runs/10" in path:
                return failed
            if params and params.get("branch") == "feature":
                return {"total_count": 2, "workflow_runs": [passed, failed]}
            return {"total_count": 0, "workflow_runs": []}

        def pages(path, key=None, params=None):
            api.requests += 1
            if path.endswith("/pulls"):
                return [pull()]
            if path.endswith("/commits"):
                return [{"sha": "old"}, {"sha": "new"}]
            if "/runs/10/" in path:
                return pair(10, arm="failure", sha="old")
            if "/runs/20/" in path:
                return pair(20, arm="success" if recovered else "failure", sha="new")
            raise AssertionError(path)

        api.get, api.pages = get, pages
        return config, state, api, later

    def test_same_pr_recovery_outside_daily_window_migrates_baseline_and_alert_identity(self):
        config, state, api, later = self.fixture()
        report, saved = scan(api, config, state, later)
        package = report["packages"][0]
        self.assertEqual(package["health"], "Healthy")
        self.assertFalse(report["errors"])
        self.assertTrue(all("/pr/42/9/" in key for key in package["last_known"]))
        event = package["planned_notification"]
        self.assertEqual([change["kind"] for change in event["changes"]], ["recovery"])
        self.assertEqual(event["changes"][0]["before"]["run_id"], 10)
        self.assertEqual(event["changes"][0]["after"]["run_id"], 20)
        self.assertTrue(all("/commit-" not in key for key in saved["packages"]["package"]["notification_results"]))

    def test_equivalent_new_failure_after_identity_migration_does_not_repeat_alert(self):
        config, state, api, later = self.fixture(recovered=False)
        report, saved = scan(api, config, state, later)
        self.assertEqual(report["packages"][0]["health"], "Broken")
        self.assertIsNone(report["packages"][0]["planned_notification"])
        self.assertFalse(saved["packages"]["package"]["pending_notifications"])

    def test_api_failure_during_catchup_preserves_known_failure(self):
        config, state, api, later = self.fixture()
        api.get = lambda *args, **kwargs: (_ for _ in ()).throw(MonitorError("503"))
        report, _ = scan(api, config, state, later)
        self.assertEqual(report["packages"][0]["health"], "Broken")
        self.assertTrue(report["errors"])

    def test_replaying_recovered_window_does_not_repeat_recovery(self):
        config, state, api, later = self.fixture()
        report, state = scan(api, config, state, later)
        self.assertEqual(report["packages"][0]["health"], "Healthy")
        state["packages"]["package"]["pending_notifications"] = []
        again, _ = scan(api, config, state, later)
        self.assertEqual(again["packages"][0]["health"], "Healthy")
        self.assertIsNone(again["packages"][0]["planned_notification"])
