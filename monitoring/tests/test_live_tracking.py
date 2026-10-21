import copy
import unittest
from datetime import timedelta
from unittest.mock import patch

from monitoring.issues import reconcile
from monitoring.monitor import MonitorError, REPOSITORY, scan
from test_issues import FakeIssues, MemoryIssues
from test_monitor import FakeAPI, NOW, config, empty_state
from test_recent import pair, recent_config, recent_run


class LiveTrackingTests(unittest.TestCase):
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
