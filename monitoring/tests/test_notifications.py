import copy
import hashlib
import unittest

from monitoring.monitor import MonitorError, REPOSITORY, plan_master, scan
from monitoring.notifications import (
    EVENT, MASTER_EVENT, OLD_EVENT_GUIDANCE, PACKAGE_GUIDANCE, deliver, deliver_master, event_pages,
)
from monitoring.issues import sync_notification_guidance
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


class NativeSubscriptionTests(unittest.TestCase):
    def test_participants_and_commands_do_not_create_explicit_mentions_or_preferences(self):
        item = tracker()
        item["assignees"] = [person(1, "khmyznikov"), person(2, "assigned")]
        comments = [comment(1, person(3, "commenter"), "hello"),
                    comment(2, person(3, "commenter"), "/monitor subscribe"),
                    comment(4, person(2, "assigned"), "/monitor unsubscribe"),
                    comment(3, person(4, "github-actions[bot]", "Bot"), "@not-a-participant")]
        original = copy.deepcopy(comments)
        api, state = MemoryIssues(), {}
        deliver(api, item, {"package": "package", "owner": "owner"}, snapshot(), comments, state, lambda: None)
        self.assertEqual(comments[:-1], original)
        for user in ("khmyznikov", "assigned", "commenter", "owner"):
            self.assertNotIn("@" + user, comments[-1]["body"])
        self.assertNotIn("participant_preferences", state)
        self.assertIn(PACKAGE_GUIDANCE, comments[-1]["body"])

    def test_bot_created_issue_does_not_mention_fallback_owner(self):
        item = tracker()
        item["user"] = person(4, "github-actions[bot]", "Bot")
        comments = []
        deliver(MemoryIssues(), item, {"package": "package", "owner": "khmyznikov"}, snapshot(), comments, {}, lambda: None)
        self.assertNotIn("@khmyznikov", comments[0]["body"])


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
        self.assertNotIn("@khmyznikov", comments[0]["body"])
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
        forged = event_pages("package", event, tracker_url=self.mapping()["package=package"]["html_url"])[0]
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

    def test_evidence_is_chunked_without_dropping_targets_or_adding_mentions(self):
        event = {"key": "package/1", "changes": [
            {"kind": "incident", "target": f"target-{index}", "after": {
                "detail": "failure @untrusted", "arm64": {"jobs": [
                    {"name": "x" * 180, "url": "https://example.com/job"} for _ in range(10)
                ]},
            }} for index in range(40)
        ]}
        pages = event_pages("package", event)
        self.assertGreater(len(pages), 1)
        text = "\n".join(p["body"] for p in pages)
        for index in range(40):
            self.assertEqual(text.count(f"`target-{index}`"), 1)
        self.assertNotIn("@untrusted", text)
        self.assertEqual(len({p["marker"] for p in pages}), len(pages))

    def test_recovery_notification_uses_saved_event_without_mentions(self):
        api, state = MemoryIssues(), {"issue_notifications_initialized": True}
        event = {"key": "package/2", "changes": [{"kind": "recovery", "target": "cp314",
                                                 "after": {"detail": "passed", "jobs": []}}]}
        state["pending_notifications"] = [event]
        comments = []
        deliver(api, tracker(), {"package": "package", "owner": "owner"}, {}, comments, state, lambda: None)
        self.assertIn("Recovery", comments[0]["body"])
        self.assertFalse(state["pending_notifications"])
        self.assertNotIn("@owner", comments[0]["body"])


class NativeMigrationTests(unittest.TestCase):
    def legacy_event(self):
        event = {"key": "package/legacy", "changes": [
            {"kind": "incident", "target": "cp314", "after": {"detail": "Failure evidence"}},
        ], "audience": ["khmyznikov", "other"]}
        page = event_pages("package", event)[0]
        page["body"] = page["body"].replace(PACKAGE_GUIDANCE, OLD_EVENT_GUIDANCE).replace(
            "\nA new failure is reported", "\nParticipants: @khmyznikov @other\n\nA new failure is reported",
        )
        continuation = EVENT + hashlib.sha256((event["key"] + "/1").encode()).hexdigest() + " -->"
        event["pages"] = [page, {"marker": continuation, "body": "Old mention-only continuation\n" + continuation}]
        return event

    def test_legacy_pending_pages_drop_mentions_and_continuations_without_losing_evidence(self):
        event = self.legacy_event()
        marker = event["pages"][0]["marker"]
        state = {"issue_notifications_initialized": True, "participant_preferences": {"1": {"opted_out": True}},
                 "pending_notifications": [event]}
        api, comments = MemoryIssues(), []
        deliver(api, tracker(), {"package": "package"}, {}, comments, state, lambda: None)
        self.assertEqual(len(comments), 1)
        self.assertIn("Failure evidence", comments[0]["body"])
        self.assertIn(marker, comments[0]["body"])
        self.assertNotIn("@khmyznikov", comments[0]["body"])
        self.assertNotIn("@other", comments[0]["body"])
        self.assertNotIn("/monitor ", comments[0]["body"])
        self.assertNotIn("audience", event)
        self.assertNotIn("participant_preferences", state)
        self.assertTrue(state["issue_notifications_initialized"])
        self.assertFalse(state["pending_notifications"])

    def test_existing_legacy_comment_is_edited_in_place_and_not_reposted(self):
        api, event = MemoryIssues(), self.legacy_event()
        posted = api.write("POST", "issues/1/comments", {"body": event["pages"][0]["body"]})
        comments = api.pages(f"repos/{REPOSITORY}/issues/1/comments")
        state = {"issue_notifications_initialized": True, "pending_notifications": [event]}
        writes = api.writes
        sync_notification_guidance(api, comments)
        deliver(api, tracker(), {"package": "package"}, {}, comments, state, lambda: None)
        self.assertEqual(api.writes, writes + 1)
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["id"], posted["id"])
        self.assertIn(PACKAGE_GUIDANCE, comments[0]["body"])
        self.assertNotIn("Participants:", comments[0]["body"])
        self.assertFalse(state["pending_notifications"])

    def test_human_comment_and_modified_guidance_are_preserved(self):
        event = self.legacy_event()
        comments = [comment(20, person(3, "human"), event["pages"][0]["body"]),
                    comment(21, person(1, "khmyznikov"), event["pages"][0]["body"].replace(
                        OLD_EVENT_GUIDANCE, OLD_EVENT_GUIDANCE + " Human addition."))]
        original = copy.deepcopy(comments)
        api = MemoryIssues()
        sync_notification_guidance(api, comments)
        self.assertEqual(comments, original)
        self.assertEqual(api.writes, 0)
