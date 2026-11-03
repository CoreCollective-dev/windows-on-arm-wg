import copy
import unittest

from monitoring.monitor import MonitorError, REPOSITORY, plan_master, scan
from monitoring.notifications import EVENT, MASTER_EVENT, deliver, deliver_master, event_pages, participants
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

    def package(self, health="Broken", name="package", errors=None):
        return {"package": name, "health": health, "errors": errors or [],
                "last_known": snapshot()["last_known"] if health == "Broken" else {}, "results": {}}

    def state(self):
        return {"master_notifications": {"health": {"package": "Healthy"},
                                         "sequence": 0, "pending_notifications": []}}

    def mapping(self):
        return {"package=package": issue(2), "package=newcomer": issue(3)}

    def test_initial_adoption_does_not_reannounce_existing_failures(self):
        api, state = MemoryIssues(), {}
        deliver_master(api, tracker(), [self.package()], self.mapping(), [], state, lambda: None)
        self.assertEqual(api.writes, 0)
        self.assertEqual(state["master_notifications"]["health"], {"package": "Broken"})
        self.assertFalse(state["master_notifications"]["pending_notifications"])

    def test_initial_adoption_remembers_known_failure_despite_a_read_warning(self):
        api, state = MemoryIssues(), {}
        deliver_master(api, tracker(), [self.package(errors=["502"])], self.mapping(), [], state, lambda: None)
        plan_master([self.package()], state["master_notifications"])
        self.assertEqual(state["master_notifications"]["health"], {"package": "Broken"})
        self.assertFalse(state["master_notifications"]["pending_notifications"])
        self.assertEqual(api.writes, 0)

    def test_new_regression_links_child_and_evidence_without_mentions_or_package_commands(self):
        api, state, comments, saves = MemoryIssues(), self.state(), [], []
        plan_master([self.package()], state["master_notifications"])
        deliver_master(api, tracker(), [self.package()], self.mapping(), comments, state,
                       lambda: saves.append(copy.deepcopy(state)))
        self.assertEqual(len(comments), 1)
        body = comments[0]["body"]
        self.assertIn("Newly Broken: package", body)
        self.assertIn(self.mapping()["package=package"]["html_url"], body)
        self.assertIn("arm64:", body)
        self.assertIn("amd64:", body)
        self.assertIn(MASTER_EVENT, body)
        self.assertNotIn(EVENT, body)
        self.assertNotIn("@khmyznikov", body)
        self.assertNotIn("/monitor unsubscribe", body)
        self.assertTrue(any(event.get("pages") for saved in saves
                            for event in saved["master_notifications"]["pending_notifications"]))
        self.assertFalse(state["master_notifications"]["pending_notifications"])

    def test_unchanged_failure_and_recovery_are_quiet_but_rebreak_alerts(self):
        api, state, comments = MemoryIssues(), self.state(), []
        for health, expected in (("Broken", 1), ("Broken", 1), ("Healthy", 1), ("Broken", 2)):
            package = self.package(health)
            plan_master([package], state["master_notifications"])
            deliver_master(api, tracker(), [package], self.mapping(), comments, state, lambda: None)
            self.assertEqual(len(comments), expected)
        self.assertNotEqual(comments[0]["body"].split(MASTER_EVENT)[1], comments[1]["body"].split(MASTER_EVENT)[1])

    def test_inconclusive_paused_and_errors_do_not_rearm_a_broken_package(self):
        for health, errors in (("Unknown", []), ("Attention", []), ("Paused", []), ("Healthy", ["502"])):
            with self.subTest(health=health, errors=errors):
                state = self.state()
                state["master_notifications"]["health"]["package"] = "Broken"
                plan_master([self.package(health, errors=errors), self.package()], state["master_notifications"])
                self.assertEqual(state["master_notifications"]["health"]["package"], "Broken")
                self.assertFalse(state["master_notifications"]["pending_notifications"])

    def test_read_error_defers_a_new_broken_alert_until_evidence_is_complete(self):
        state = self.state()
        plan_master([self.package(errors=["502"])], state["master_notifications"])
        self.assertEqual(state["master_notifications"]["health"]["package"], "Healthy")
        self.assertFalse(state["master_notifications"]["pending_notifications"])
        plan_master([self.package()], state["master_notifications"])
        self.assertEqual(len(state["master_notifications"]["pending_notifications"]), 1)

    def test_newly_enrolled_broken_package_and_another_regression_have_separate_links(self):
        api, state, comments = MemoryIssues(), self.state(), []
        packages = [self.package(), self.package(name="newcomer")]
        plan_master(packages, state["master_notifications"])
        deliver_master(api, tracker(), packages, self.mapping(), comments, state, lambda: None)
        self.assertEqual(len(comments), 2)
        self.assertIn(self.mapping()["package=package"]["html_url"], comments[0]["body"])
        self.assertIn(self.mapping()["package=newcomer"]["html_url"], comments[1]["body"])
        self.assertEqual(state["master_notifications"]["sequence"], 2)

    def test_broken_without_verified_comparator_evidence_is_rejected(self):
        package = self.package()
        next(iter(package["last_known"].values()))["arm_specific_failure"] = False
        with self.assertRaisesRegex(MonitorError, "comparative evidence"):
            plan_master([package], self.state()["master_notifications"])

    def test_ambiguous_master_post_is_found_before_retry(self):
        api, state = MemoryIssues(), self.state()
        plan_master([self.package()], state["master_notifications"])
        original = api.write
        def lose_reply(method, resource, payload):
            original(method, resource, payload)
            raise MonitorError("Response lost")
        api.write = lose_reply
        with self.assertRaises(MonitorError):
            deliver_master(api, tracker(), [self.package()], self.mapping(), [], state, lambda: None)
        api.write = original
        writes = api.writes
        comments = api.pages(f"repos/{REPOSITORY}/issues/1/comments")
        deliver_master(api, tracker(), [self.package()], self.mapping(), comments, state, lambda: None)
        self.assertEqual(api.writes, writes)
        self.assertFalse(state["master_notifications"]["pending_notifications"])

    def test_lost_acknowledgment_reuses_frozen_payload_even_after_recovery(self):
        api, state, saves = MemoryIssues(), self.state(), []
        plan_master([self.package()], state["master_notifications"])
        deliver_master(api, tracker(), [self.package()], self.mapping(), [], state,
                       lambda: saves.append(copy.deepcopy(state)))
        restored = next(saved for saved in saves if saved["master_notifications"]["pending_notifications"])
        plan_master([self.package("Healthy")], restored["master_notifications"])
        writes = api.writes
        comments = api.pages(f"repos/{REPOSITORY}/issues/1/comments")
        deliver_master(api, tracker(), [self.package("Healthy")], self.mapping(), comments, restored, lambda: None)
        self.assertEqual(api.writes, writes)
        self.assertFalse(restored["master_notifications"]["pending_notifications"])

    def test_untrusted_master_marker_does_not_suppress_delivery(self):
        api, state = MemoryIssues(), self.state()
        plan_master([self.package()], state["master_notifications"])
        event = state["master_notifications"]["pending_notifications"][0]
        forged = event_pages("package", event, [], tracker_url=self.mapping()["package=package"]["html_url"])[0]
        comments = [comment(20, person(3, "human"), forged["body"])]
        deliver_master(api, tracker(), [self.package()], self.mapping(), comments, state, lambda: None)
        self.assertEqual(api.writes, 1)

    def test_unmapped_package_does_not_silently_discard_pending_alert(self):
        api, state = MemoryIssues(), self.state()
        plan_master([self.package()], state["master_notifications"])
        with self.assertRaisesRegex(MonitorError, "not enrolled"):
            deliver_master(api, tracker(), [self.package()], {}, [], state, lambda: None)
        self.assertEqual(len(state["master_notifications"]["pending_notifications"]), 1)

    def test_partial_master_batch_retries_only_the_unsent_package(self):
        api, state = MemoryIssues(), self.state()
        packages = [self.package(), self.package(name="newcomer")]
        plan_master(packages, state["master_notifications"])
        original = api.write
        def fail_second(method, resource, payload):
            if "Newly Broken: newcomer" in payload["body"]:
                raise MonitorError("Write failed")
            return original(method, resource, payload)
        api.write = fail_second
        with self.assertRaises(MonitorError):
            deliver_master(api, tracker(), packages, self.mapping(), [], state, lambda: None)
        self.assertEqual(len(state["master_notifications"]["pending_notifications"]), 1)
        self.assertEqual(api.writes, 1)
        api.write = original
        comments = api.pages(f"repos/{REPOSITORY}/issues/1/comments")
        deliver_master(api, tracker(), packages, self.mapping(), comments, state, lambda: None)
        self.assertEqual(api.writes, 2)
        self.assertFalse(state["master_notifications"]["pending_notifications"])

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
