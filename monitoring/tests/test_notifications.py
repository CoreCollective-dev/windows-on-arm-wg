import copy
import unittest

from monitoring.monitor import MonitorError, scan
from monitoring.notifications import EVENT, deliver, event_pages, participants
from test_issues import MemoryIssues, issue
from test_monitor import FakeAPI, NOW, config, empty_state, job


def person(identifier, login, kind="User"):
    return {"id": identifier, "login": login, "type": kind}


def comment(identifier, user, body):
    return {"id": identifier, "user": user, "body": body}


def snapshot():
    report, _ = scan(FakeAPI(jobs=[job("failure")]), config(), empty_state(), NOW)
    value = next(iter(report["packages"][0]["results"].values()))
    return {"last_known": {"wheel/main/cp314": value}}


def tracker():
    result = issue()
    result["user"] = person(1, "khmyznikov")
    result["assignees"] = []
    return result


class ParticipantTests(unittest.TestCase):
    def test_author_assignees_commenters_are_deduplicated_and_bots_excluded(self):
        item = tracker()
        item["assignees"] = [person(1, "khmyznikov"), person(2, "assigned")]
        comments = [comment(1, person(3, "commenter"), "hello"),
                    comment(2, person(3, "commenter"), "again"),
                    comment(3, person(4, "github-actions[bot]", "Bot"), "@not-a-participant")]
        audience, _ = participants(item, comments, {}, "owner")
        self.assertEqual(audience, ["assigned", "commenter", "khmyznikov"])

    def test_deleted_unsubscribe_is_not_undone_by_earlier_subscribe(self):
        user = person(3, "commenter")
        comments = [comment(10, user, "/monitor subscribe"), comment(20, user, "/monitor unsubscribe")]
        audience, preferences = participants(tracker(), comments, {}, "owner")
        self.assertNotIn("commenter", audience)
        audience, preferences = participants(tracker(), comments[:1], preferences, "owner")
        self.assertNotIn("commenter", audience)
        audience, _ = participants(tracker(), comments[:1] + [comment(30, user, "/monitor subscribe")], preferences, "owner")
        self.assertIn("commenter", audience)

    def test_commands_change_only_their_authenticated_author(self):
        commands = [comment(1, person(3, "commenter"), "/monitor unsubscribe @khmyznikov")]
        audience, _ = participants(tracker(), commands, {}, "owner")
        self.assertEqual(audience, ["commenter", "khmyznikov"])

    def test_bot_created_issue_uses_fallback_owner(self):
        item = tracker()
        item["user"] = person(4, "github-actions[bot]", "Bot")
        audience, _ = participants(item, [], {}, "khmyznikov")
        self.assertEqual(audience, ["khmyznikov"])


class DeliveryTests(unittest.TestCase):
    def test_initial_regression_is_delivered_once_and_repeated_sync_is_silent(self):
        api = MemoryIssues()
        state, comments, saves = {}, [], []
        package = {"package": "package", "owner": "khmyznikov"}
        deliver(api, tracker(), package, snapshot(), comments, state, lambda: saves.append(copy.deepcopy(state)))
        writes = api.writes
        deliver(api, tracker(), package, snapshot(), comments, state, lambda: None)
        self.assertEqual(api.writes, writes)
        self.assertEqual(len(comments), 1)
        self.assertIn("@khmyznikov", comments[0]["body"])
        self.assertFalse(state["pending_notifications"])
        self.assertTrue(any(event.get("pages") for saved in saves for event in saved.get("pending_notifications", [])))

    def test_ambiguous_post_is_found_by_marker_before_retry(self):
        api = MemoryIssues()
        original = api.write
        posted = []

        def write(method, resource, payload):
            value = original(method, resource, payload)
            posted.append(value)
            raise MonitorError("Response lost")

        api.write = write
        state = {}
        package = {"package": "package", "owner": "khmyznikov"}
        with self.assertRaises(MonitorError):
            deliver(api, tracker(), package, snapshot(), [], state, lambda: None)
        api.write = original
        writes = api.writes
        deliver(api, tracker(), package, snapshot(), posted, state, lambda: None)
        self.assertEqual(api.writes, writes)
        self.assertFalse(state["pending_notifications"])

    def test_preexisting_initial_marker_survives_state_ack_loss(self):
        api, comments = MemoryIssues(), []
        package = {"package": "package", "owner": "khmyznikov"}
        deliver(api, tracker(), package, snapshot(), comments, {}, lambda: None)
        writes = api.writes
        deliver(api, tracker(), package, snapshot(), comments, {}, lambda: None)
        self.assertEqual(api.writes, writes)

    def test_existing_initial_comment_does_not_discard_new_material_event(self):
        api, comments = MemoryIssues(), []
        package = {"package": "package", "owner": "khmyznikov"}
        deliver(api, tracker(), package, snapshot(), comments, {}, lambda: None)
        state = {"pending_notifications": [{
            "key": "package/next", "changes": [{"kind": "incident", "target": "new-target",
                                               "after": {"detail": "New target failed"}}],
        }]}
        deliver(api, tracker(), package, snapshot(), comments, state, lambda: None)
        self.assertEqual(len(comments), 2)
        self.assertIn("new-target", comments[1]["body"])
        self.assertFalse(state["pending_notifications"])

    def test_newly_bootstrapped_healthy_package_has_no_incident_comment(self):
        api = MemoryIssues()
        state = {}
        deliver(api, tracker(), {"package": "package", "owner": "owner"}, {"last_known": {}}, [], state, lambda: None)
        self.assertEqual(api.writes, 0)
        self.assertTrue(state["issue_notifications_initialized"])

    def test_payload_mentions_are_chunked_without_dropping_people(self):
        event = {"key": "package/1", "changes": [
            {"kind": "incident", "target": "cp314", "after": {"detail": "failure @untrusted", "jobs": []}},
        ]}
        audience = ["user" + str(i) for i in range(95)]
        pages = event_pages("package", event, audience)
        self.assertEqual(len(pages), 3)
        text = "\n".join(p["body"] for p in pages)
        for user in audience:
            self.assertEqual(sum(("@" + user) in p["body"].split("Participants: ", 1)[-1].split("\n", 1)[0].split()
                                 for p in pages), 1)
        self.assertNotIn("@untrusted", text)
        self.assertEqual(len({p["marker"] for p in pages}), 3)

    def test_recovery_notification_uses_saved_event_and_preserves_audience(self):
        api, state = MemoryIssues(), {"issue_notifications_initialized": True}
        event = {"key": "package/2", "changes": [{"kind": "recovery", "target": "cp314",
                                                 "after": {"detail": "passed", "jobs": []}}]}
        state["pending_notifications"] = [event]
        comments = []
        deliver(api, tracker(), {"package": "package", "owner": "owner"}, {}, comments, state, lambda: None)
        self.assertIn("Recovery", comments[0]["body"])
        self.assertFalse(state["pending_notifications"])
