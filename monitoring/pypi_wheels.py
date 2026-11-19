"""Track published Windows ARM64 wheels independently of upstream CI."""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from packaging.utils import (
    InvalidName, InvalidSdistFilename, InvalidWheelFilename, canonicalize_name,
    parse_sdist_filename, parse_wheel_filename,
)
from packaging.version import Version

from monitoring.issues import IssueAPI
from monitoring.monitor import APIError, MonitorError, REPOSITORY, literal


RANKING = "https://hugovk.github.io/top-pypi-packages/top-pypi-packages-30-days.min.json"
TOP_COUNT = 1000
STATE_BRANCH = "wheel-monitor-pypi-state"
STATE_FILE = "state.json"
BOT = "github-actions[bot]"
MARKER = re.compile(r"<!-- pypi-arm64-wheel-monitor:package=([a-z0-9-]+) -->")


@dataclass(frozen=True)
class Releases:
    latest: Version | None
    supported: Version | None


def fetch_json(url, accept="application/json"):
    request = Request(url, headers={"Accept": accept, "User-Agent": "windows-on-arm-wg-wheel-monitor"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as error:
            if attempt == 2 or (error.code != 429 and not 500 <= error.code < 600):
                raise MonitorError(f"HTTP {error.code} reading {url}") from error
        except (OSError, HTTPException, ValueError) as error:
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


def top_packages(data):
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list) or len(rows) < TOP_COUNT:
        raise MonitorError(f"Expected at least {TOP_COUNT} ranked PyPI projects")
    names = {package_name(row.get("project") if isinstance(row, dict) else None)
             for row in rows[:TOP_COUNT]}
    if len(names) != TOP_COUNT:
        raise MonitorError("Duplicate project names in the PyPI top 1000")
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
    data = fetch_json(f"https://pypi.org/simple/{name}/", "application/vnd.pypi.simple.v1+json")
    return release_status(name, data)


def collect(ranked, remembered, lookup=observe):
    names = sorted(ranked | remembered.keys())
    state, missing, supported, unknown, errors = remembered.copy(), {}, {}, [], {}

    def checked(name):
        try:
            return lookup(name), None
        except MonitorError as error:
            return None, str(error)

    with ThreadPoolExecutor(max_workers=8) as pool:
        for name, (release, error) in zip(names, pool.map(checked, names)):
            if error is not None:
                errors[name] = error
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
    ranked = top_packages(fetch_json(RANKING))
    state, missing, supported, unknown, errors = collect(ranked, remembered)
    trackers = tracked_issues(api.pages(f"repos/{REPOSITORY}/issues", params={"state": "all"}))
    if trackers.keys() - remembered.keys():
        raise MonitorError(f"Existing tracker issues are missing from {STATE_BRANCH}; recover the monitor state")
    changes = sorted(issue_changes(trackers, missing, supported))
    additions = state.keys() - remembered.keys()
    lines = [
        "## PyPI Windows ARM64 wheel monitor",
        "",
        f"Mode: {'apply' if args.apply else 'preview (no writes)'}",
        f"- Projects scanned: {len(ranked | remembered.keys())} "
        f"({len(ranked)} ranked; {len(remembered.keys() - ranked)} previously tracked outside the ranking)",
        f"- Tracked: {len(state)} ({len(additions)} new); latest with Windows ARM64: {len(supported)}",
        f"- Regressions: {len(missing)}; inconclusive: {len(unknown)}; fetch errors: {len(errors)}",
    ]
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
    return int(bool(errors))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MonitorError as error:
        print(f"PyPI wheel monitor: {error}", file=sys.stderr)
        sys.exit(1)
