import copy
import subprocess
import unittest
from datetime import timedelta
from unittest.mock import patch

from monitoring.issues import (
    IssueAPI, MASTER, STATUS, checked_report, identities, marker, reconcile,
    status_body, sync_health, update_status,
)
from monitoring.monitor import MonitorError, REPOSITORY, scan
from test_monitor import FakeAPI, NOW, config, empty_state, job


def issue(number=1, key=MASTER, author="khmyznikov", state="open"):
    return {
        "id": number + 100, "number": number, "body": key, "user": {"login": author},
        "state": state, "labels": [], "html_url": f"https://github.com/{REPOSITORY}/issues/{number}",
    }


class FakeIssues:
    def __init__(self, issues=None, apply=False):
        self.issues = issues or []
        self.apply = apply
        self.writes = 0
        self.calls = []

    def pages(self, path, key=None, params=None):
        self.calls.append(path)
        return self.issues if path.endswith("/issues") else []

    def write(self, method, resource, payload):
        self.writes += 1
        raise AssertionError("Unexpected mutation")


class MemoryIssues(FakeIssues):
    def __init__(self):
        super().__init__(apply=True)
        self.labels = []
        self.comments = {}
        self.children = []

    def pages(self, path, key=None, params=None):
        if path.endswith("/labels"):
            return self.labels
        if path.endswith("/sub_issues"):
            return self.children
        if path.endswith("/comments"):
            return self.comments.get(path, [])
        return self.issues

    def write(self, method, resource, payload):
        self.writes += 1
        if resource == "labels":
            self.labels.append(payload)
            return payload
        if resource == "issues":
            item = issue(len(self.issues) + 10, key=payload["body"])
            item["labels"] = [{"name": name} for name in payload["labels"]]
            self.issues.append(item)
            return item
        if resource.endswith("/sub_issues"):
            self.children.append(next(i for i in self.issues if i["id"] == payload["sub_issue_id"]))
            return self.children[-1]
        if resource.endswith("/comments"):
            path = f"repos/{REPOSITORY}/{resource}"
            comment = {"id": self.writes + 500, "user": {"login": "github-actions[bot]"}, **payload}
            self.comments.setdefault(path, []).append(comment)
            return comment
        if resource.startswith("issues/comments/"):
            for comments in self.comments.values():
                for comment in comments:
                    if comment["id"] == int(resource.rsplit("/", 1)[1]):
                        comment.update(payload)
                        return comment
        if method == "PATCH" and "labels" in payload:
            item = next(i for i in self.issues if i["number"] == int(resource.rsplit("/", 1)[1]))
            item["labels"] = [{"name": name} for name in payload["labels"]]
            return item
        raise AssertionError((method, resource, payload))


class IssueTests(unittest.TestCase):
    def test_marker_uses_package_identity_not_title(self):
        self.assertEqual(identities([issue(key=marker("numpy"))])["package=numpy"]["number"], 1)

    def test_untrusted_marker_rejected(self):
        with self.assertRaises(MonitorError):
            identities([issue(author="stranger")])

    def test_duplicate_master_rejected(self):
        with self.assertRaises(MonitorError):
            identities([issue(), issue(2)])

    def test_pull_request_is_not_tracker(self):
        self.assertEqual(identities([{**issue(), "pull_request": {}}]), {})

    def test_preview_never_writes(self):
        report, _ = scan(FakeAPI(), config(), empty_state(), NOW)
        api = FakeIssues()
        with patch("monitoring.issues.utcnow", return_value=NOW):
            preview = reconcile(api, config(), report, bootstrap=True)
        self.assertEqual(preview["new_issues"], 2)
        self.assertEqual(api.writes, 0)

    def test_normal_sync_cannot_create_missing_issues(self):
        report, _ = scan(FakeAPI(), config(), empty_state(), NOW)
        with patch("monitoring.issues.utcnow", return_value=NOW), self.assertRaises(MonitorError):
            reconcile(FakeIssues(apply=True), config(), report)

    def test_mismatched_report_rejected(self):
        report, _ = scan(FakeAPI(), config(), empty_state(), NOW)
        report["packages"] = []
        with self.assertRaises(MonitorError):
            checked_report(config(), report, NOW)

    def test_stale_report_rejected(self):
        report, _ = scan(FakeAPI(), config(), empty_state(), NOW)
        with self.assertRaises(MonitorError):
            checked_report(config(), report, NOW + timedelta(hours=73))

    def test_different_registry_revision_rejected(self):
        report, _ = scan(FakeAPI(), config(), empty_state(), NOW)
        changed = config()
        changed["packages"][0]["owner"] = "another-owner"
        with self.assertRaises(MonitorError):
            checked_report(changed, report, NOW)

    def test_mutations_require_apply(self):
        with self.assertRaises(MonitorError):
            IssueAPI().write("POST", "issues", {"title": "test"})

    def test_successful_mutation_invalidates_cached_hierarchy_reads(self):
        api = IssueAPI(apply=True, interval=0)
        api._cache["repos/tracker/issues/7/sub_issues"] = []
        response = subprocess.CompletedProcess([], 0, '{"id": 1}', "")
        with patch("subprocess.run", return_value=response):
            api.write("POST", "issues/7/sub_issues", {"sub_issue_id": 1})
        self.assertEqual(api._cache, {})

    def test_cannot_mutate_upstream_or_delete(self):
        with self.assertRaises(MonitorError):
            IssueAPI(apply=True).write("DELETE", "issues/1", {})
        with self.assertRaises(MonitorError):
            IssueAPI(apply=True).write("POST", "repos/upstream/project/issues", {})

    def test_status_escapes_incidental_mentions(self):
        report, _ = scan(FakeAPI(jobs=[job("failure")]), config(), empty_state(), NOW)
        package = report["packages"][0]
        list(package["results"].values())[0]["detail"] = "failed @someone"
        self.assertNotIn("@someone", status_body(package, report["checked_at"]))

    def test_status_updates_existing_comment_not_human_discussion(self):
        api = FakeIssues(apply=True)
        api.pages = lambda path: [
            {"id": 20, "body": "Human discussion", "user": {"login": "human"}},
            {"id": 21, "body": STATUS + "\nold", "user": {"login": "github-actions[bot]"}},
        ]
        calls = []
        api.write = lambda *args: calls.append(args)
        update_status(api, issue(), STATUS + "\nnew")
        self.assertEqual(calls[0][0:2], ("PATCH", "issues/comments/21"))

    def test_label_sync_preserves_human_labels(self):
        api = FakeIssues(apply=True)
        calls = []
        api.write = lambda *args: calls.append(args)
        item = issue()
        item["labels"] = [{"name": "human-owned"}, {"name": "health:broken"}]
        sync_health(api, item, "Healthy")
        self.assertEqual(calls[0][2]["labels"], ["health:healthy", "human-owned", "wheel-monitor"])

    def test_bootstrap_is_idempotent(self):
        report, _ = scan(FakeAPI(), config(), empty_state(), NOW)
        api = MemoryIssues()
        with patch("monitoring.issues.utcnow", return_value=NOW):
            first = reconcile(api, config(), report, bootstrap=True)
            first_writes = api.writes
            second = reconcile(api, config(), report, bootstrap=True)
        self.assertEqual(len(api.issues), 2)
        self.assertEqual(len(api.children), 1)
        self.assertEqual(first["master"], second["master"])
        self.assertEqual(first_writes, api.writes)

    def test_resume_after_partial_bootstrap(self):
        report, _ = scan(FakeAPI(), config(), empty_state(), NOW)
        api = MemoryIssues()
        original = api.write
        failed = False

        def fail_once(method, resource, payload):
            nonlocal failed
            if resource.endswith("/sub_issues") and not failed:
                failed = True
                raise MonitorError("Ambiguous response")
            return original(method, resource, payload)

        api.write = fail_once
        with patch("monitoring.issues.utcnow", return_value=NOW):
            with self.assertRaises(MonitorError):
                reconcile(api, config(), report, bootstrap=True)
            reconcile(api, config(), report, bootstrap=True)
        self.assertEqual(len(api.issues), 2)
        self.assertEqual(len(api.children), 1)

    def test_closed_tracker_is_paused_not_reopened(self):
        report, _ = scan(FakeAPI(), config(), empty_state(), NOW)
        api = MemoryIssues()
        with patch("monitoring.issues.utcnow", return_value=NOW):
            reconcile(api, config(), report, bootstrap=True)
            child = api.children[0]
            child["state"] = "closed"
            reconcile(api, config(), report)
        self.assertEqual(child["state"], "closed")
        self.assertIn({"name": "health:paused"}, child["labels"])
