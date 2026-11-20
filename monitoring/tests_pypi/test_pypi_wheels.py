import base64
import contextlib
import io
import json
import os
import subprocess
import unittest
from datetime import date
from unittest.mock import Mock, patch

from monitoring import pypi_wheels as monitor
from monitoring.monitor import APIError, MonitorError


def files(name, *filenames):
    return monitor.release_status(name, {
        "name": name,
        "files": [
            {"filename": file, "yanked": False} if isinstance(file, str)
            else {"filename": file[0], "yanked": file[1]}
            for file in filenames
        ],
    })


class PyPIWheelMonitorTests(unittest.TestCase):
    def test_ranking_uses_clickpy_order_and_rejects_incomplete_response(self):
        projects = [{"project": "Zebra_lib"}, {"project": "acorn"}]
        month = date(2026, 10, 1)
        with patch.object(monitor, "fetch_json", return_value={"data": projects, "rows": 2}):
            self.assertEqual(monitor.ranked_packages(month), ["zebra-lib", "acorn"])
        with patch.object(monitor, "fetch_json", return_value={"data": projects, "rows": 3}):
            with self.assertRaises(MonitorError):
                monitor.ranked_packages(month)

    def test_discovery_takes_the_first_thousand_projects_with_wheel_history(self):
        ranking = [f"pkg{i}" for i in range(2200)]
        current = monitor.Releases(monitor.Version("2.0"), monitor.Version("1.0"))
        no_history = monitor.Releases(monitor.Version("2.0"), None)
        lookup = Mock(side_effect=lambda name: current if int(name[3:]) % 2 else no_history)

        discovery = monitor.discover(ranking, {}, lookup)

        self.assertEqual(discovery.selected, set(ranking[1:2000:2]))
        self.assertEqual(discovery.depth, 2000)
        self.assertEqual(discovery.unresolved, {})
        self.assertLess(lookup.call_count, len(ranking))

    def test_unread_candidate_cannot_be_displaced_by_a_lower_ranked_project(self):
        def lookup(name):
            if name == "unread":
                raise MonitorError("HTTP 503")
            return monitor.Releases(monitor.Version("2.0"), monitor.Version("1.0") if name == "lower" else None)

        # A remembered package still qualifies even if PyPI no longer lists its old wheels.
        discovery = monitor.discover(["unread", "remembered", "lower"], {"remembered": "1.0"}, lookup, limit=2)
        self.assertEqual(discovery.selected, {"remembered"})
        self.assertEqual(discovery.unresolved, {"unread": "HTTP 503"})

    def test_collection_reuses_discovery_and_checks_previously_tracked_projects(self):
        arm = monitor.Releases(monitor.Version("2.0"), monitor.Version("2.0"))
        lookup = Mock(return_value=arm)
        remembered = {"outside": "1.0"}
        discovery = monitor.discover(["inside"], remembered, lookup)
        state, _, supported, _, _ = monitor.collect(
            discovery.selected, remembered, lookup, observations=discovery.observations,
        )
        self.assertEqual(state, {"inside": "2.0", "outside": "2.0"})
        self.assertEqual(set(supported), {"inside", "outside"})
        self.assertEqual([call.args[0] for call in lookup.call_args_list], ["inside", "outside"])

    def test_latest_stable_only_considers_active_files_and_exact_platform_tags(self):
        status = files(
            "example-lib",
            "example_lib-2.9-cp311-abi3-win_amd64.win_arm64.whl",
            "example_lib-2.10.tar.gz",
            "example_lib-2.10-py3-none-any.whl",
            "example_lib-2.10-cp311-abi3-win_arm64extra.whl",
            ("example_lib-2.10-cp311-abi3-win_arm64.whl", "withdrawn"),
            ("example_lib-2.11-cp311-abi3-win_arm64.whl", True),
            "example_lib-3.0rc1-cp311-abi3-win_arm64.whl",
            "example_lib-3.0.dev1-cp311-abi3-win_arm64.whl",
        )
        self.assertEqual(str(status.latest), "2.10")
        self.assertEqual(str(status.supported), "2.9")
        self.assertEqual(str(status.prerelease_with_wheel), "3.0rc1")

    def test_newer_prerelease_closes_regression_without_advancing_stable_baseline(self):
        name = "passagemath-categories"
        older_arm = "passagemath_categories-10.8.13-cp313-cp313-win_arm64.whl"
        stable = "passagemath_categories-10.8.14.tar.gz"
        prerelease_arm = "passagemath_categories-10.8.15rc0-cp313-cp313-win_arm64.whl"
        newer_prerelease = "passagemath_categories-10.10.1a1.tar.gz"
        lookup = Mock(return_value=files(name, older_arm, stable, prerelease_arm, newer_prerelease))

        state, missing, supported, _, _ = monitor.collect({name}, {}, lookup)
        self.assertEqual(state, {name: "10.8.13"})
        self.assertEqual(missing, {})
        self.assertEqual(supported, {name: monitor.Version("10.8.15rc0")})
        tracker = {name: {"number": 42, "state": "open"}}
        self.assertEqual(list(monitor.issue_changes(tracker, missing, supported)), [
            ("close", name, 42, {"state": "closed", "state_reason": "completed"}),
        ])

        lookup.return_value = files(name, stable, prerelease_arm, "passagemath_categories-10.8.15.tar.gz")
        _, missing, supported, _, _ = monitor.collect(set(), state, lookup)
        tracker[name]["state"] = "closed"
        (action, _, number, _), = monitor.issue_changes(tracker, missing, supported)
        self.assertEqual((action, number), ("reopen", 42))

    def test_yanked_or_incompatible_prerelease_does_not_clear_regression(self):
        name = "example"
        release = files(
            name, "example-1.0-cp313-cp313-win_arm64.whl", "example-2.0.tar.gz",
            ("example-3.0rc1-cp313-cp313-win_arm64.whl", True),
            "example-3.0rc1-py3-none-any.whl",
            "example-3.0rc1-cp313-cp313-win_amd64.whl",
        )
        _, missing, supported, _, _ = monitor.collect({name}, {}, Mock(return_value=release))
        self.assertIn(name, missing)
        self.assertEqual(supported, {})

    def test_prerelease_wheel_alone_does_not_enroll_package(self):
        name = "example"
        release = files(name, "example-1.0.tar.gz", "example-2.0rc1-cp313-cp313-win_arm64.whl")
        self.assertEqual(monitor.discover([name], {}, Mock(return_value=release)).selected, set())

    def test_first_scan_finds_existing_loss_and_later_loss_reuses_tracker(self):
        name = "cryptography"
        v46_arm = "cryptography-46.0.0-cp311-abi3-win_arm64.whl"
        v47_x64 = "cryptography-47.0.0-cp311-abi3-win_amd64.whl"
        v47_arm = "cryptography-47.0.0-cp311-abi3-win_arm64.whl"
        lookup = Mock(return_value=files(name, v46_arm, v47_x64))

        state, missing, supported, _, _ = monitor.collect({name}, {}, lookup)
        self.assertEqual(state, {name: "46.0.0"})
        (action, _, number, payload), = monitor.issue_changes({}, missing, supported)
        self.assertEqual((action, number), ("open", None))
        self.assertIn("cryptography: ", payload["title"])
        self.assertIn("/cryptography/46.0.0/#files", payload["body"])
        self.assertIn("/cryptography/47.0.0/#files", payload["body"])
        issue = {"number": 42, "state": "open", "body": payload["body"]}
        self.assertEqual(list(monitor.issue_changes({name: issue}, missing, supported)), [])

        # A late upload to the same release restores support, even outside the ranking.
        lookup.return_value = files(name, v47_x64, v47_arm)
        newcomer = files("newcomer", "newcomer-1.0-cp311-abi3-win_arm64.whl")

        def lookup_project(project):
            return lookup(project) if project == name else newcomer

        state, missing, supported, _, _ = monitor.collect({"newcomer"}, state, lookup_project)
        self.assertEqual(state, {name: "47.0.0", "newcomer": "1.0"})
        self.assertEqual(list(monitor.issue_changes({name: issue}, missing, supported)), [
            ("close", name, 42, {"state": "closed", "state_reason": "completed"}),
        ])

        # Keep the old baseline even if the package removes its older wheels.
        lookup.return_value = files(name, "cryptography-48.0.0.tar.gz")
        state, missing, supported, _, _ = monitor.collect(set(), state, lookup_project)
        issue["state"] = "closed"
        (action, _, number, payload), = monitor.issue_changes({name: issue}, missing, supported)
        self.assertEqual((action, number), ("reopen", 42))
        self.assertIn("/cryptography/48.0.0/#files", payload["body"])

    def test_errors_and_inconclusive_results_never_change_package_issues(self):
        remembered = {"unavailable": "1.0", "yanked": "1.0", "same-version": "1.0"}

        def lookup(name):
            if name == "unavailable":
                raise MonitorError("HTTP 503")
            if name == "yanked":
                return files(name, ("yanked-1.0-py3-none-win_arm64.whl", True))
            return files(name, "same_version-1.0.tar.gz")

        state, missing, supported, unknown, errors = monitor.collect(set(), remembered, lookup)
        self.assertEqual(state, remembered)
        self.assertEqual(set(unknown), {"yanked", "same-version"})
        self.assertEqual(errors, {"unavailable": "HTTP 503"})
        trackers = {name: {"number": i, "state": "open"} for i, name in enumerate(remembered, 1)}
        self.assertEqual(list(monitor.issue_changes(trackers, missing, supported)), [])

    def test_tracker_identity_ignores_human_issues_and_daily_monitor_issues(self):
        body = "<!-- pypi-arm64-wheel-monitor:package=example -->\n\nDetails."
        tracker = {"number": 4, "body": body, "user": {"login": "github-actions[bot]"}}
        issues = [
            {**tracker, "number": 1, "user": {"login": "contributor"}},
            {**tracker, "number": 2, "body": "<!-- wheel-monitor:package=example -->"},
            {**tracker, "number": 3, "pull_request": {}},
            tracker,
        ]
        self.assertEqual(monitor.tracked_issues(issues), {"example": tracker})
        with self.assertRaises(MonitorError):
            monitor.tracked_issues([tracker, {**tracker, "number": 5}])

    def test_state_branch_can_be_initialized_then_updated_without_touching_main(self):
        packages = {"example": "1.0"}
        api = Mock()
        api.get.side_effect = APIError("git/ref/heads/wheel-monitor-pypi-state", 404)
        self.assertEqual(monitor.load_state(api), ({}, None))

        def response(sha):
            return subprocess.CompletedProcess("gh", 0, stdout=json.dumps({"sha": sha}))

        with patch.object(monitor.subprocess, "run", side_effect=map(response, ("tree", "commit", "ref"))) as gh:
            monitor.write_state(packages, None)
        created_tree, created_commit, created_ref = [json.loads(call.kwargs["input"]) for call in gh.call_args_list]
        content = created_tree["tree"][0]["content"].encode()
        self.assertEqual(created_commit["parents"], [])
        self.assertEqual(created_commit["tree"], "tree")
        self.assertEqual(created_ref, {"ref": f"refs/heads/{monitor.STATE_BRANCH}", "sha": "commit"})

        api.get.side_effect = [{}, {"sha": "file", "content": base64.b64encode(content).decode()}]
        remembered, sha = monitor.load_state(api)
        self.assertEqual(remembered, packages)
        with patch.object(monitor.subprocess, "run", return_value=response("new-file")) as gh:
            monitor.write_state({"example": "2.0"}, sha)
        request = json.loads(gh.call_args.kwargs["input"])
        self.assertEqual(request["branch"], monitor.STATE_BRANCH)
        self.assertEqual(request["sha"], "file")
        self.assertEqual(json.loads(base64.b64decode(request["content"]))["packages"], {"example": "2.0"})

    def test_missing_file_on_existing_state_branch_does_not_reset_history(self):
        api = Mock()
        api.get.side_effect = [{}, APIError("contents/state.json", 404)]
        with self.assertRaises(APIError):
            monitor.load_state(api)

    def test_preview_does_not_save_history_or_create_issues(self):
        api = Mock()
        api.pages.return_value = []
        latest, baseline = monitor.Version("2.0"), monitor.Version("1.0")
        scan = ({"example": "1.0"}, {"example": (latest, baseline)}, {}, [], {})
        with (
            patch("sys.argv", ["pypi_wheels"]),
            patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}),
            patch.object(monitor, "IssueAPI", return_value=api),
            patch.object(monitor, "load_state", return_value=({}, None)),
            patch.object(monitor, "TOP_COUNT", 1),
            patch.object(monitor, "ranked_packages", return_value=["example"]),
            patch.object(monitor, "discover", return_value=monitor.Discovery({"example"}, {}, 1, {})),
            patch.object(monitor, "collect", return_value=scan),
            patch.object(monitor, "write_state") as save,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(monitor.main(), 0)
        self.assertIn("Open `example`", output.getvalue())
        save.assert_not_called()
        api.write.assert_not_called()
