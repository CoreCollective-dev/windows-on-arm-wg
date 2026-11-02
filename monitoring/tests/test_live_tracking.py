import copy
import unittest
from datetime import timedelta
from unittest.mock import patch

from monitoring.issues import reconcile, sync_issue_lifecycle
from monitoring.monitor import MonitorError, REPOSITORY, scan
from test_issues import FakeIssues, MemoryIssues, issue
from test_monitor import FakeAPI, NOW, config, empty_state
from test_recent import pair, recent_config, recent_run


class LiveTrackingTests(unittest.TestCase):
    def config(self):
        data = recent_config()
        data["packages"][0]["channels"][0]["baseline_search"] = False
        return data

    def test_healthy_bootstrap_closes_child_but_keeps_master_and_relationship(self):
        report, _ = scan(FakeAPI(runs=[recent_run()], jobs=pair()), self.config(), empty_state(), NOW)
        api = MemoryIssues()
        with patch("monitoring.issues.utcnow", return_value=NOW):
            result = reconcile(api, self.config(), report, bootstrap=True)
        self.assertEqual(api.issues[0]["state"], "open")
        self.assertEqual(api.children[0]["state"], "closed")
        self.assertEqual(result["issue_states"], {"closed": 1})
        self.assertEqual(len(api.children), 1)

    def test_closed_healthy_tracker_reopens_on_regression_then_closes_on_recovery(self):
        config = self.config()
        api = MemoryIssues()
        report, state = scan(FakeAPI(runs=[recent_run()], jobs=pair()), config, empty_state(), NOW)
        with patch("monitoring.issues.utcnow", return_value=NOW):
            bootstrap = reconcile(api, config, report, bootstrap=True, state=state, persist=lambda: None)
        child = api.children[0]
        self.assertEqual(child["state"], "closed")
        package_state = state["packages"]["package"]
        self.assertTrue(package_state["issue_notifications_initialized"])
        package_state["participant_preferences"] = {"123": {"login": "opted-out", "comment_id": 50, "opted_out": True}}
        report, state = scan(FakeAPI(runs=[recent_run(20, "failure")], jobs=pair(20, arm="failure")),
                             config, state, NOW + timedelta(days=1))
        with patch("monitoring.issues.utcnow", return_value=NOW + timedelta(days=1)):
            reconcile(api, config, report, mapping=bootstrap["mapping"], state=state, persist=lambda: None)
        self.assertEqual(child["state"], "open")
        self.assertTrue(state["packages"]["package"]["issue_notifications_initialized"])
        self.assertTrue(state["packages"]["package"]["participant_preferences"]["123"]["opted_out"])
        notifications = [c for comments in api.comments.values() for c in comments if "wheel-monitor:event=" in c["body"]]
        self.assertEqual(len(notifications), 1)
        report, state = scan(FakeAPI(runs=[recent_run(30, "failure")], jobs=pair(30, arm="failure")),
                             config, state, NOW + timedelta(days=2))
        with patch("monitoring.issues.utcnow", return_value=NOW + timedelta(days=2)):
            reconcile(api, config, report, mapping=bootstrap["mapping"], state=state, persist=lambda: None)
        self.assertEqual(child["state"], "open")
        self.assertEqual(sum("wheel-monitor:event=" in c["body"] for comments in api.comments.values() for c in comments), 1)
        report, state = scan(FakeAPI(runs=[recent_run(40)], jobs=pair(40)), config, state, NOW + timedelta(days=3))
        with patch("monitoring.issues.utcnow", return_value=NOW + timedelta(days=3)):
            reconcile(api, config, report, mapping=bootstrap["mapping"], state=state, persist=lambda: None)
        self.assertEqual(child["state"], "closed")
        self.assertEqual(api.issues[0]["state"], "open")
        self.assertEqual(len(api.children), 1)
        self.assertEqual(len(api.issues), 2)
        self.assertEqual(sum("wheel-monitor:event=" in c["body"] for comments in api.comments.values() for c in comments), 2)

    def test_closed_issue_does_not_drop_pending_recovery_or_reset_initialization(self):
        config = self.config()
        api = MemoryIssues()
        report, state = scan(FakeAPI(runs=[recent_run(10, "failure")], jobs=pair(10, arm="failure")),
                             config, empty_state(), NOW)
        with patch("monitoring.issues.utcnow", return_value=NOW):
            bootstrap = reconcile(api, config, report, bootstrap=True, state=state, persist=lambda: None)
        api.children[0]["state"] = "closed"
        report, state = scan(FakeAPI(runs=[recent_run(20)], jobs=pair(20)), config, state, NOW + timedelta(days=1))
        with patch("monitoring.issues.utcnow", return_value=NOW + timedelta(days=1)):
            reconcile(api, config, report, mapping=bootstrap["mapping"], state=state, persist=lambda: None)
        self.assertEqual(api.children[0]["state"], "closed")
        self.assertTrue(state["packages"]["package"]["issue_notifications_initialized"])
        self.assertFalse(state["packages"]["package"]["pending_notifications"])
        notifications = [c for comments in api.comments.values() for c in comments if "wheel-monitor:event=" in c["body"]]
        self.assertEqual(len(notifications), 2)
        self.assertIn("Recovery", notifications[-1]["body"])

    def test_inconclusive_or_paused_health_preserves_existing_issue_state(self):
        for health in ("Unknown", "Attention", "Paused"):
            for existing in ("open", "closed"):
                with self.subTest(health=health, state=existing):
                    api = FakeIssues(apply=True)
                    item = issue(state=existing)
                    sync_issue_lifecycle(api, item, {"health": health, "errors": []})
                    self.assertEqual(item["state"], existing)
                    self.assertEqual(api.writes, 0)

    def test_monitoring_error_cannot_close_an_open_issue_using_retained_green_baseline(self):
        api = FakeIssues(apply=True)
        item = issue()
        sync_issue_lifecycle(api, item, {"health": "Healthy", "errors": ["503"]})
        self.assertEqual(item["state"], "open")
        self.assertEqual(api.writes, 0)

    def test_known_failure_with_monitoring_error_remains_open(self):
        api = MemoryIssues()
        item = issue(state="closed")
        api.issues.append(item)
        sync_issue_lifecycle(api, item, {"health": "Broken", "errors": ["503"]})
        self.assertEqual(item["state"], "open")

    def test_paused_packages_are_not_created(self):
        data = config()
        paused = copy.deepcopy(data["packages"][0])
        paused.update(package="paused", channels=[], setup_reason="Excluded", setup_health="Paused")
        data["packages"].append(paused)
        report, _ = scan(FakeAPI(), data, empty_state(), NOW)
        api = MemoryIssues()
        with patch("monitoring.issues.utcnow", return_value=NOW):
            result = reconcile(api, data, report, bootstrap=True)
        self.assertEqual(len(api.issues), 2)
        self.assertEqual(result["enrolled_packages"], 1)
        self.assertEqual(result["paused_excluded"], 1)
        self.assertNotIn("paused", result["mapping"]["packages"])

    def test_mapping_cannot_silently_point_to_another_issue(self):
        report, _ = scan(FakeAPI(), config(), empty_state(), NOW)
        api = MemoryIssues()
        with patch("monitoring.issues.utcnow", return_value=NOW):
            result = reconcile(api, config(), report, bootstrap=True)
            result["mapping"]["packages"]["package"] = 999
            with self.assertRaises(MonitorError):
                reconcile(api, config(), report, mapping=result["mapping"])

    def test_outbox_survives_repeated_scan_until_writer_acknowledges(self):
        data = recent_config()
        data["packages"][0]["channels"][0]["baseline_search"] = False
        api = FakeAPI(runs=[recent_run(conclusion="failure")], jobs=pair(arm="failure"))
        _, state = scan(api, data, empty_state(), NOW)
        first = state["packages"]["package"]["pending_notifications"]
        self.assertEqual(len(first), 1)
        _, state = scan(api, data, state, NOW)
        self.assertEqual(state["packages"]["package"]["pending_notifications"], first)

    def test_daily_same_failure_on_a_new_run_has_no_second_notification(self):
        data = recent_config()
        data["packages"][0]["channels"][0]["baseline_search"] = False
        report, state = scan(FakeAPI(runs=[recent_run(conclusion="failure")], jobs=pair(arm="failure")),
                             data, empty_state(), NOW)
        api = MemoryIssues()
        with patch("monitoring.issues.utcnow", return_value=NOW):
            result = reconcile(api, data, report, bootstrap=True, state=state, persist=lambda: None)
        notifications = sum("wheel-monitor:event=" in c["body"] for comments in api.comments.values() for c in comments)
        self.assertEqual(notifications, 1)
        report, state = scan(FakeAPI(runs=[recent_run(20, "failure")], jobs=pair(20, arm="failure")),
                             data, state, NOW + timedelta(days=1))
        with patch("monitoring.issues.utcnow", return_value=NOW + timedelta(days=1)):
            reconcile(api, data, report, mapping=result["mapping"], state=state, persist=lambda: None)
        self.assertEqual(sum("wheel-monitor:event=" in c["body"] for comments in api.comments.values() for c in comments), 1)
        self.assertFalse(state["packages"]["package"]["pending_notifications"])

    def test_notification_state_must_match_report_before_writes(self):
        report, state = scan(FakeAPI(), config(), empty_state(), NOW)
        state["checked_at"] = "old"
        api = FakeIssues(apply=True)
        with patch("monitoring.issues.utcnow", return_value=NOW), self.assertRaises(MonitorError):
            reconcile(api, config(), report, bootstrap=True, state=state, persist=lambda: None)
        self.assertEqual(api.writes, 0)

    def test_quiet_days_preserve_failure_without_creating_notification(self):
        data = recent_config()
        data["packages"][0]["channels"][0]["baseline_search"] = False
        _, state = scan(FakeAPI(runs=[recent_run(conclusion="failure")], jobs=pair(arm="failure")),
                        data, empty_state(), NOW)
        state["packages"]["package"]["pending_notifications"] = []
        _, state = scan(FakeAPI(runs=[]), data, state, NOW + timedelta(days=1))
        self.assertEqual(state["packages"]["package"]["health"], "Broken")
        self.assertFalse(state["packages"]["package"]["pending_notifications"])
