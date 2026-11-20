"""Track published Windows ARM64 wheels independently of upstream CI."""

import argparse
import base64
import gzip
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.client import HTTPException
from itertools import batched
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from packaging.utils import (
    InvalidName, InvalidSdistFilename, InvalidWheelFilename, canonicalize_name,
    parse_sdist_filename, parse_wheel_filename,
)
from packaging.version import Version

from monitoring.issues import IssueAPI
from monitoring.monitor import APIError, MonitorError, REPOSITORY, literal


RANKING_INDEX = "https://sql-clickhouse.clickhouse.com/"
TOP_COUNT = 1000
UNFILTERED_RANK_DEPTH = 5000
DISCOVERY_BATCH = 64
STATE_BRANCH = "wheel-monitor-pypi-state"
STATE_FILE = "state.json"
BOT = "github-actions[bot]"
MARKER = re.compile(r"<!-- pypi-arm64-wheel-monitor:package=([a-z0-9-]+) -->")
PARENT_MARKER = re.compile(r"<!-- pypi-arm64-wheel-monitor:(master|overflow|group=[1-9][0-9]*) -->")
SUB_ISSUE_LIMIT = 100


@dataclass(frozen=True)
class Releases:
    latest: Version | None
    supported: Version | None
    prerelease_with_wheel: Version | None = None
    latest_has_no_platform_wheels: bool = False


@dataclass(frozen=True)
class Discovery:
    selected: set[str]
    observations: dict[str, Releases | MonitorError]
    depth: int
    unresolved: dict[str, str]


class ProjectNotFound(MonitorError):
    pass


def fetch_json(url, accept="application/json"):
    request = Request(url, headers={
        "Accept": accept, "Accept-Encoding": "gzip", "User-Agent": "windows-on-arm-wg-wheel-monitor",
    })
    for attempt in range(3):
        try:
            with urlopen(request, timeout=20) as response:
                if response.headers.get("Content-Encoding") == "gzip":
                    with gzip.GzipFile(fileobj=response) as body:
                        return json.load(body)
                return json.load(response)
        except HTTPError as error:
            if error.code == 404:
                raise ProjectNotFound(f"HTTP 404 reading {url}") from error
            if attempt == 2 or (error.code != 429 and not 500 <= error.code < 600):
                raise MonitorError(f"HTTP {error.code} reading {url}") from error
        except (OSError, HTTPException, ValueError, EOFError) as error:
            if attempt == 2:
                raise MonitorError(f"Cannot read JSON from {url}: {error}") from error
        time.sleep(2 ** attempt)


def package_name(value):
    if not isinstance(value, str):
        raise MonitorError("Invalid PyPI package name")
    try:
        return canonicalize_name(value, validate=True)
    except InvalidName as error:
        raise MonitorError(f"Invalid PyPI package name: {value!r}") from error


def ranked_packages(month):
    # ClickPy's wheel metadata can be incomplete, even for popular projects.
    query = f"""
        WITH downloads AS (
            SELECT project, sum(count) AS download_count
            FROM pypi.pypi_downloads_per_month
            WHERE month = '{month.isoformat()}'
            GROUP BY project
        ), popular_projects AS (
            SELECT project FROM downloads
            ORDER BY download_count DESC, project
            LIMIT {UNFILTERED_RANK_DEPTH}
        ), wheel_projects AS (
            SELECT DISTINCT lower(replaceRegexpAll(name, '[-_.]+', '-')) AS project
            FROM pypi.projects
            WHERE endsWith(filename, '.whl')
              AND has(splitByChar('.', arrayElement(splitByChar('-', filename), -1)), 'win_arm64')
        )
        SELECT project, download_count
        FROM downloads
        WHERE project IN (SELECT project FROM popular_projects)
           OR project IN (SELECT project FROM wheel_projects)
        ORDER BY download_count DESC, project
    """
    params = urlencode({"query": query, "user": "demo", "default_format": "JSON"})
    data = fetch_json(f"{RANKING_INDEX}?{params}")
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows or data.get("rows") != len(rows):
        raise MonitorError(f"Missing or incomplete ClickPy download ranking for {month:%Y-%m}")
    names = [package_name(row.get("project") if isinstance(row, dict) else None) for row in rows]
    if len(set(names)) != len(names):
        raise MonitorError("Duplicate project names in the ClickPy download ranking")
    return names


def release_status(name, data):
    if (not isinstance(data, dict) or package_name(data.get("name")) != name
            or not isinstance(data.get("files"), list)):
        raise MonitorError(f"Unexpected PyPI file index for {name}")
    latest = supported = prerelease_with_wheel = None
    latest_has_platform_wheel = False
    for file in data["files"]:
        if (not isinstance(file, dict) or not isinstance(file.get("filename"), str)
                or not isinstance(file.get("yanked"), (bool, str))):
            raise MonitorError(f"Incomplete PyPI file metadata for {name}")
        if file["yanked"] is not False:
            continue
        filename = file["filename"]
        arm64 = platform_wheel = False
        try:
            if filename.endswith(".whl"):
                project, version, _, tags = parse_wheel_filename(filename)
                arm64 = any(tag.platform == "win_arm64" for tag in tags)
                platform_wheel = any(tag.platform != "any" for tag in tags)
            else:
                project, version = parse_sdist_filename(filename)
        except (InvalidSdistFilename, InvalidWheelFilename):
            continue  # Some projects still list legacy distribution formats.
        if project != name:
            continue
        if version.is_prerelease:
            if arm64 and (prerelease_with_wheel is None or version > prerelease_with_wheel):
                prerelease_with_wheel = version
            continue
        if latest is None or version > latest:
            latest = version
            latest_has_platform_wheel = False
        if version == latest:
            latest_has_platform_wheel |= platform_wheel
        if arm64 and (supported is None or version > supported):
            supported = version
    return Releases(latest, supported, prerelease_with_wheel, latest is not None and not latest_has_platform_wheel)


def observe(name):
    try:
        data = fetch_json(f"https://pypi.org/simple/{name}/", "application/vnd.pypi.simple.v1+json")
    except ProjectNotFound:
        return Releases(None, None)
    return release_status(name, data)


def checked(name, lookup):
    try:
        return lookup(name)
    except MonitorError as error:
        return error


def discover(ranking, remembered, lookup=observe, *, limit=TOP_COUNT):
    selected, observations, unresolved = set(), {}, {}
    depth = qualified = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for batch in batched(ranking, DISCOVERY_BATCH):
            results = dict(zip(batch, pool.map(lambda name: checked(name, lookup), batch)))
            observations.update(results)
            for name, release in results.items():
                depth += 1
                if isinstance(release, MonitorError):
                    unresolved[name] = str(release)
                    continue
                if release.latest_has_no_platform_wheels or (name not in remembered and release.supported is None):
                    continue
                qualified += 1
                # Unread projects above this one could all qualify.
                if qualified + len(unresolved) <= limit:
                    selected.add(name)
                if qualified == limit:
                    return Discovery(selected, observations, depth, unresolved)
    return Discovery(selected, observations, depth, unresolved)


def collect(ranked, remembered, lookup=observe, *, observations=None):
    names = sorted(ranked | remembered.keys())
    state, missing, supported, unknown, errors, excluded = remembered.copy(), {}, {}, [], {}, {}
    observations = {} if observations is None else observations

    def recorded(name):
        return observations[name] if name in observations else checked(name, lookup)

    with ThreadPoolExecutor(max_workers=8) as pool:
        for name, release in zip(names, pool.map(recorded, names)):
            if isinstance(release, MonitorError):
                errors[name] = str(release)
                continue
            if release.latest is None:
                unknown.append(name)
                continue
            if release.latest_has_no_platform_wheels:
                excluded[name] = release.latest
                continue
            baseline = Version(state[name]) if name in state else None
            if release.supported is not None and (baseline is None or release.supported > baseline):
                baseline = release.supported
                state[name] = str(baseline)
            if release.latest == release.supported:
                supported[name] = release.latest
            elif release.prerelease_with_wheel is not None and release.prerelease_with_wheel > release.latest:
                supported[name] = release.prerelease_with_wheel
            elif baseline is not None and release.latest > baseline:
                missing[name] = (release.latest, baseline)
            elif baseline is not None:
                unknown.append(name)
    return state, missing, supported, unknown, errors, excluded


def load_state(api):
    try:
        api.get(f"repos/{REPOSITORY}/git/ref/heads/{STATE_BRANCH}")
    except APIError as error:
        if error.status == 404:
            return {}, None
        raise
    data = api.get(f"repos/{REPOSITORY}/contents/{STATE_FILE}", {"ref": STATE_BRANCH})
    try:
        state = json.loads(base64.b64decode(data["content"]))
        packages = state["packages"]
        if state["schema"] != 1 or not isinstance(packages, dict) or not data["sha"]:
            raise ValueError("Invalid state schema")
        for name, version in packages.items():
            if package_name(name) != name or Version(version).is_prerelease:
                raise ValueError(f"Invalid baseline for {name}")
    except (ValueError, KeyError, TypeError) as error:
        raise MonitorError(f"Cannot read state from {STATE_BRANCH}: {error}") from error
    return packages, data["sha"]


def write_state(packages, sha):
    content = json.dumps({"schema": 1, "packages": packages}, indent=2, sort_keys=True) + "\n"
    message = "Update PyPI Windows ARM64 wheel history"

    def write(method, path, payload):
        try:
            result = subprocess.run(
                ["gh", "api", "--method", method, f"repos/{REPOSITORY}/{path}", "--input", "-"],
                input=json.dumps(payload), text=True, encoding="utf-8",
                capture_output=True, timeout=60, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise MonitorError(f"Could not save PyPI monitor state: {error}") from error
        if result.returncode:
            raise MonitorError(f"Could not save PyPI monitor state ({path}): {result.stderr.strip()}")
        try:
            return json.loads(result.stdout)
        except ValueError as error:
            raise MonitorError(f"Unexpected response saving PyPI monitor state ({path})") from error

    if sha is not None:
        write("PUT", f"contents/{STATE_FILE}", {
            "branch": STATE_BRANCH, "sha": sha, "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        })
    else:
        tree = write("POST", "git/trees", {"tree": [
            {"path": STATE_FILE, "mode": "100644", "type": "blob", "content": content},
        ]})
        commit = write("POST", "git/commits", {"message": message, "tree": tree["sha"], "parents": []})
        write("POST", "git/refs", {"ref": f"refs/heads/{STATE_BRANCH}", "sha": commit["sha"]})


def tracked_issues(issues):
    parents, packages = {}, {}
    for issue in issues:
        if "pull_request" in issue or (issue.get("user") or {}).get("login") != BOT:
            continue
        marker = (issue.get("body") or "").partition("\n")[0].rstrip("\r")
        for pattern, found in ((PARENT_MARKER, parents), (MARKER, packages)):
            match = pattern.fullmatch(marker)
            if match:
                key = match[1]
                if key in found:
                    raise MonitorError(f"Multiple PyPI wheel tracker issues for {key}")
                found[key] = issue
                break
    return parents, packages


def sub_issue_ids(api, issue):
    return {child["id"] for child in api.pages(f"repos/{REPOSITORY}/issues/{issue['number']}/sub_issues")}


def plan_hierarchy(api, parents, packages, new_packages):
    master = parents.get("master")
    if master and master["state"] != "open":
        raise MonitorError("The PyPI wheel master issue is closed; reopen it before syncing")
    children = {key: sub_issue_ids(api, issue) for key, issue in parents.items()}
    sizes = {key: len(ids) for key, ids in children.items()}
    links = {}

    def link(key, parent, issue=None):
        if issue and issue["id"] in children.get(parent, ()):
            return
        if sizes.get(parent, 0) >= SUB_ISSUE_LIMIT:
            raise MonitorError(f"PyPI wheel tracker {parent} is out of sub-issue slots")
        links[key] = parent
        sizes[parent] = sizes.get(parent, 0) + 1

    groups = sorted(key for key in parents if key.startswith("group="))
    if "overflow" in parents or groups:
        link("overflow", "master", parents.get("overflow"))
    for group in groups:
        link(group, "overflow", parents[group])

    linked_packages = set().union(*(children.get(key, ()) for key in ["master", *groups]))
    unlinked = new_packages | {name for name, issue in packages.items() if issue["id"] not in linked_packages}
    for name in sorted(unlinked):
        # Reserve one of the master's direct slots for the overflow parent.
        if sizes.get("master", 0) < SUB_ISSUE_LIMIT - 1 and "overflow" not in parents and "overflow" not in links:
            parent = "master"
        else:
            if "overflow" not in parents and "overflow" not in links:
                link("overflow", "master")
            for index in range(1, SUB_ISSUE_LIMIT + 1):
                group = f"group={index}"
                if sizes.get(group, 0) >= SUB_ISSUE_LIMIT:
                    continue
                if group not in parents and group not in links:
                    if sizes.get("overflow", 0) >= SUB_ISSUE_LIMIT:
                        continue
                    link(group, "overflow")
                parent = group
                break
            else:
                raise MonitorError("The PyPI wheel overflow tracker is out of sub-issue slots")
        link(f"package={name}", parent)
    return links


def parent_payload(key):
    if key == "master":
        title = "PyPI Windows ARM64 wheel regressions"
        description = (
            "The weekly PyPI wheel monitor tracks missing Windows ARM64 wheels as sub-issues here.\n\n"
            "This issue stays open. Package issues close if a Windows ARM64 wheel returns on the latest "
            "stable release or a newer prerelease, or if the latest stable release stops shipping "
            "platform-specific wheels altogether.\n\n"
            f"[Weekly workflow](https://github.com/{REPOSITORY}/actions/workflows/wheel-monitor-pypi.yml)"
        )
    elif key == "overflow":
        title = "PyPI Windows ARM64 wheel regressions: overflow"
        description = "Additional package regressions are grouped under this issue to fit GitHub's sub-issue limit."
    else:
        title = f"PyPI Windows ARM64 wheel regressions: overflow group {key.partition('=')[2]}"
        description = "Package regression trackers for the weekly PyPI Windows ARM64 wheel monitor."
    return {"title": title, "body": f"<!-- pypi-arm64-wheel-monitor:{key} -->\n\n{description}"}


def apply_issue_changes(api, parents, packages, links, changes):
    issues = {**parents, **{f"package={name}": issue for name, issue in packages.items()}}
    new_packages = {f"package={name}": payload for action, name, _, payload in changes if action == "open"}
    if "master" not in issues:
        issues["master"] = api.write("POST", "issues", parent_payload("master"))

    expected = {}
    for child, parent in links.items():
        if child not in issues:
            payload = new_packages[child] if child.startswith("package=") else parent_payload(child)
            issues[child] = api.write("POST", "issues", payload)
        child_id = issues[child]["id"]
        # Never replace another issue's existing parent.
        api.write("POST", f"issues/{issues[parent]['number']}/sub_issues", {"sub_issue_id": child_id})
        expected.setdefault(parent, set()).add(child_id)

    for parent, ids in expected.items():
        if not ids.issubset(sub_issue_ids(api, issues[parent])):
            raise MonitorError(f"Some sub-issues were not linked under PyPI wheel tracker {parent}")

    for action, name, number, payload in changes:
        if action != "open":
            api.write("PATCH", f"issues/{number}", payload)
        print(f"{action.capitalize()} issue for {name}")
    print(f"PyPI wheel master issue: https://github.com/{REPOSITORY}/issues/{issues['master']['number']}")


def release_link(name, version):
    url_version = quote(str(version), safe="")
    return f"[{version}](https://pypi.org/project/{name}/{url_version}/#files)"


def issue_changes(trackers, missing, supported, excluded=()):
    for name, (latest, baseline) in missing.items():
        issue = trackers.get(name)
        if issue and issue["state"] == "open":
            continue
        body = (
            f"<!-- pypi-arm64-wheel-monitor:package={name} -->\n\n"
            f"The latest stable PyPI release of `{name}`, {release_link(name, latest)}, "
            "ships platform-specific wheels but has no non-yanked `win_arm64` wheel, "
            "and no newer prerelease provides one.\n\n"
            f"The most recent stable release known to provide one is {release_link(name, baseline)}.\n\n"
            "The weekly PyPI wheel monitor will close this issue if a `win_arm64` wheel returns on the "
            "latest stable release or a newer prerelease, or if the latest stable release stops shipping "
            "platform-specific wheels altogether."
        )
        if issue:
            yield "reopen", name, issue["number"], {"state": "open", "body": body}
        else:
            yield "open", name, None, {"title": f"{name}: PyPI Windows ARM64 wheel missing", "body": body}
    for name in (supported.keys() | excluded) & trackers.keys():
        issue = trackers[name]
        if issue["state"] == "open":
            reason = "not_planned" if name in excluded else "completed"
            yield "close", name, issue["number"], {"state": "closed", "state_reason": reason}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Persist history and synchronize issues")
    args = parser.parse_args()
    if args.apply and (os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
                       or os.environ.get("GITHUB_REF") != "refs/heads/main"):
        raise MonitorError("Writes require the tracker repository's main branch")

    api = IssueAPI(apply=args.apply)
    remembered, sha = load_state(api)
    today = datetime.now(timezone.utc).date()
    month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    ranking = ranked_packages(month)
    discovery = discover(ranking, remembered)
    ranked = discovery.selected
    state, missing, supported, unknown, errors, excluded = collect(
        ranked, remembered, observations=discovery.observations,
    )
    parents, trackers = tracked_issues(api.pages(f"repos/{REPOSITORY}/issues", params={"state": "all"}))
    if trackers.keys() - remembered.keys():
        raise MonitorError(f"Existing tracker issues are missing from {STATE_BRANCH}; recover the monitor state")
    changes = sorted(issue_changes(trackers, missing, supported, excluded))
    links = plan_hierarchy(api, parents, trackers, {name for action, name, _, _ in changes if action == "open"})
    additions = state.keys() - remembered.keys()
    prerelease_heals = {name: version for name, version in supported.items() if version.is_prerelease}
    lines = [
        "## PyPI Windows ARM64 wheel monitor",
        "",
        f"Mode: {'apply' if args.apply else 'preview (no writes)'}",
        f"- Selected {len(ranked):,} of {TOP_COUNT:,} eligible projects after checking "
        f"{discovery.depth:,} of {len(ranking):,} candidates ({month:%B %Y} downloads). "
        f"Candidates include the top {UNFILTERED_RANK_DEPTH:,} overall and projects with indexed Windows ARM64 wheels.",
        f"- Previously tracked outside this selection: {len(remembered.keys() - ranked - excluded.keys()):,}",
        f"- Tracked: {len(state) - len(excluded)} ({len(additions)} new); with Windows ARM64: "
        f"{len(supported) - len(prerelease_heals)} on latest stable, {len(prerelease_heals)} on a newer prerelease",
        f"- Regressions: {len(missing)}; inconclusive: {len(unknown)}; fetch errors: {len(errors)}",
    ]
    master = parents.get("master")
    if master:
        number = master["number"]
        lines.append(f"- Master issue: [#{number}](https://github.com/{REPOSITORY}/issues/{number})")
    else:
        lines.append("- Master issue: create on the first write-enabled run")
    if links:
        lines.append(f"- Planned sub-issue links: {sum(key.startswith('package=') for key in links)} packages; "
                     f"{sum(not key.startswith('package=') for key in links)} overflow trackers")
    if len(ranked) < TOP_COUNT:
        lines += ["", f"Could not confirm {TOP_COUNT:,} eligible projects from the ranking candidates."]
        if discovery.unresolved:
            lines += [
                f"- {len(discovery.unresolved):,} ranking lookups failed. "
                "Only projects guaranteed to be in the top cohort can be newly tracked.",
                *(f"- `{name}`: {literal(error)}" for name, error in sorted(discovery.unresolved.items())),
            ]
        if discovery.depth == len(ranking):
            lines.append("- Reached the end of the ClickPy candidate list.")
    if additions:
        lines += ["", "Newly tracked: " + ", ".join(sorted(additions))]
    if missing:
        lines += ["", "### Releases missing Windows ARM64", *[
            f"- `{name}`: {release_link(name, latest)} (last known good: {release_link(name, baseline)})"
            for name, (latest, baseline) in sorted(missing.items())
        ]]
    if prerelease_heals:
        lines += ["", "### Windows ARM64 restored in a prerelease", *[
            f"- `{name}`: {release_link(name, version)}"
            for name, version in sorted(prerelease_heals.items())
        ]]
    if excluded:
        lines += ["", "### Previously tracked packages with no platform-specific wheels on the latest stable release", *[
            f"- `{name}`: {release_link(name, version)} (excluded from monitoring)"
            for name, version in sorted(excluded.items())
        ]]
    if unknown or errors:
        lines += ["", "### Incomplete observations (no issue changes for these packages)"]
        if unknown:
            lines.append("Inconclusive release evidence: " + ", ".join(unknown))
        lines.extend(f"- `{name}`: {literal(error)}" for name, error in sorted(errors.items()))
    if changes:
        lines += ["", "### Planned issue changes"]
        for action, name, number, _ in changes:
            line = f"- {action.capitalize()} `{name}`"
            if number:
                line += f" ([#{number}](https://github.com/{REPOSITORY}/issues/{number}))"
            if action == "close":
                if name in excluded:
                    line += ": no platform-specific wheels on the latest stable release; no longer monitored"
                else:
                    channel = "prerelease " if name in prerelease_heals else ""
                    line += f": {channel}{release_link(name, supported[name])} provides Windows ARM64 again"
            lines.append(line)
    text = "\n".join(lines) + "\n"
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as file:
            file.write(text)

    if args.apply:
        if state != remembered:
            write_state(state, sha)
        apply_issue_changes(api, parents, trackers, links, changes)
    return int(bool(errors) or len(ranked) < TOP_COUNT)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MonitorError as error:
        print(f"PyPI wheel monitor: {error}", file=sys.stderr)
        sys.exit(1)
