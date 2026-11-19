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
DISCOVERY_BATCH = 64
STATE_BRANCH = "wheel-monitor-pypi-state"
STATE_FILE = "state.json"
BOT = "github-actions[bot]"
MARKER = re.compile(r"<!-- pypi-arm64-wheel-monitor:package=([a-z0-9-]+) -->")


@dataclass(frozen=True)
class Releases:
    latest: Version | None
    supported: Version | None


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
    # The bulk index narrows discovery; PyPI itself verifies each candidate's release files.
    query = f"""
        WITH wheel_projects AS (
            SELECT DISTINCT lower(replaceRegexpAll(name, '[-_.]+', '-')) AS project
            FROM pypi.projects
            WHERE endsWith(filename, '.whl')
              AND has(splitByChar('.', arrayElement(splitByChar('-', filename), -1)), 'win_arm64')
        )
        SELECT project, sum(count) AS download_count
        FROM pypi.pypi_downloads_per_month
        WHERE month = '{month.isoformat()}' AND project IN (SELECT project FROM wheel_projects)
        GROUP BY project
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
    latest = supported = None
    for file in data["files"]:
        if (not isinstance(file, dict) or not isinstance(file.get("filename"), str)
                or not isinstance(file.get("yanked"), (bool, str))):
            raise MonitorError(f"Incomplete PyPI file metadata for {name}")
        if file["yanked"] is not False:
            continue
        filename = file["filename"]
        arm64 = False
        try:
            if filename.endswith(".whl"):
                project, version, _, tags = parse_wheel_filename(filename)
                arm64 = any(tag.platform == "win_arm64" for tag in tags)
            else:
                project, version = parse_sdist_filename(filename)
        except (InvalidSdistFilename, InvalidWheelFilename):
            continue  # Some projects still list legacy distribution formats.
        if project != name or version.is_prerelease:
            continue
        if latest is None or version > latest:
            latest = version
        if arm64 and (supported is None or version > supported):
            supported = version
    return Releases(latest, supported)


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
                if name not in remembered:
                    if isinstance(release, MonitorError):
                        unresolved[name] = str(release)
                        continue
                    if release.supported is None:
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
    state, missing, supported, unknown, errors = remembered.copy(), {}, {}, [], {}
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
            baseline = Version(state[name]) if name in state else None
            if release.supported is not None and (baseline is None or release.supported > baseline):
                baseline = release.supported
                state[name] = str(baseline)
            if release.latest == release.supported:
                supported[name] = release.latest
            elif baseline is not None and release.latest > baseline:
                missing[name] = (release.latest, baseline)
            elif baseline is not None:
                unknown.append(name)
    return state, missing, supported, unknown, errors


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
    trackers = {}
    for issue in issues:
        if "pull_request" in issue or (issue.get("user") or {}).get("login") != BOT:
            continue
        match = MARKER.fullmatch((issue.get("body") or "").partition("\n")[0].rstrip("\r"))
        if not match:
            continue
        name = match[1]
        if name in trackers:
            raise MonitorError(f"Multiple PyPI wheel tracker issues for {name}")
        trackers[name] = issue
    return trackers


def release_link(name, version):
    url_version = quote(str(version), safe="")
    return f"[{version}](https://pypi.org/project/{name}/{url_version}/#files)"


def issue_changes(trackers, missing, supported):
    for name, (latest, baseline) in missing.items():
        issue = trackers.get(name)
        if issue and issue["state"] == "open":
            continue
        body = (
            f"<!-- pypi-arm64-wheel-monitor:package={name} -->\n\n"
            f"The latest stable PyPI release of `{name}`, {release_link(name, latest)}, "
            "has no non-yanked `win_arm64` wheel.\n\n"
            f"The most recent release known to provide one is {release_link(name, baseline)}.\n\n"
            "The weekly PyPI wheel monitor will close this issue when the latest stable "
            "release provides a `win_arm64` wheel again."
        )
        if issue:
            yield "reopen", name, issue["number"], {"state": "open", "body": body}
        else:
            yield "open", name, None, {"title": f"{name}: PyPI Windows ARM64 wheel missing", "body": body}
    for name in supported.keys() & trackers.keys():
        issue = trackers[name]
        if issue["state"] == "open":
            yield "close", name, issue["number"], {"state": "closed", "state_reason": "completed"}


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
    state, missing, supported, unknown, errors = collect(
        ranked, remembered, observations=discovery.observations,
    )
    trackers = tracked_issues(api.pages(f"repos/{REPOSITORY}/issues", params={"state": "all"}))
    if trackers.keys() - remembered.keys():
        raise MonitorError(f"Existing tracker issues are missing from {STATE_BRANCH}; recover the monitor state")
    changes = sorted(issue_changes(trackers, missing, supported))
    additions = state.keys() - remembered.keys()
    lines = [
        "## PyPI Windows ARM64 wheel monitor",
        "",
        f"Mode: {'apply' if args.apply else 'preview (no writes)'}",
        f"- ClickPy candidates checked: {discovery.depth:,} of {len(ranking):,} ({month:%B %Y} downloads); "
        f"confirmed in the Windows ARM64 top {TOP_COUNT:,}: {len(ranked):,}",
        f"- Previously tracked outside this selection: {len(remembered.keys() - ranked):,}",
        f"- Tracked: {len(state)} ({len(additions)} new); latest with Windows ARM64: {len(supported)}",
        f"- Regressions: {len(missing)}; inconclusive: {len(unknown)}; fetch errors: {len(errors)}",
    ]
    if len(ranked) < TOP_COUNT:
        lines += ["", f"Could not confirm all {TOP_COUNT:,} places in the Windows ARM64 ranking."]
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
    if unknown or errors:
        lines += ["", "### Incomplete observations (no issue changes for these packages)"]
        if unknown:
            lines.append("Inconclusive release evidence: " + ", ".join(unknown))
        lines.extend(f"- `{name}`: {literal(error)}" for name, error in sorted(errors.items()))
    if changes:
        lines += ["", "### Planned issue changes"]
        lines.extend(
            f"- {action.capitalize()} `{name}`"
            + (f" ([#{number}](https://github.com/{REPOSITORY}/issues/{number}))" if number else "")
            + (f": {release_link(name, supported[name])} provides Windows ARM64 again" if action == "close" else "")
            for action, name, number, _ in changes
        )
    text = "\n".join(lines) + "\n"
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as file:
            file.write(text)

    if args.apply:
        if state != remembered:
            write_state(state, sha)
        for action, name, number, payload in changes:
            if number is None:
                api.write("POST", "issues", payload)
            else:
                api.write("PATCH", f"issues/{number}", payload)
            print(f"{action.capitalize()} issue for {name}")
    return int(bool(errors) or len(ranked) < TOP_COUNT)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MonitorError as error:
        print(f"PyPI wheel monitor: {error}", file=sys.stderr)
        sys.exit(1)
