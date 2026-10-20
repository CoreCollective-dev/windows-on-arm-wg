"""Preview/bootstrap permanent master and package issues; sync status without pings."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path

from monitoring.monitor import (
    APIError, GitHub, MonitorError, REPOSITORY, ROOT, instant, literal,
    read_json, utcnow, validate,
)


MASTER = "<!-- wheel-monitor:master -->"
STATUS = "<!-- wheel-monitor:status -->"
AUTHORS = {"khmyznikov", "github-actions[bot]"}
HEALTH = {
    "Healthy": "2da44e", "Broken": "cf222e", "Attention": "bf8700",
    "Unknown": "6e7781", "Paused": "8250df",
}


def marker(package):
    return f"<!-- wheel-monitor:package={package} -->"


class IssueAPI(GitHub):
    """Writes are restricted to the tracker repository's issues and labels."""

    def __init__(self, apply=False, interval=1.5):
        super().__init__(budget=2000)
        self.apply = apply
        self.interval = interval
        self.last_write = 0.0
        self.writes = 0

    def write(self, method, resource, payload):
        if not self.apply:
            raise MonitorError("Write attempted without --apply")
        if method not in ("POST", "PATCH") or not re.fullmatch(
            r"issues(?:/\d+(?:/comments|/sub_issues)?)?|issues/comments/\d+|labels", resource
        ):
            raise MonitorError(f"Refusing unsupported issue mutation: {method} {resource}")
        time.sleep(max(0, self.interval - (time.monotonic() - self.last_write)))
        try:
            response = subprocess.run(
                ["gh", "api", "--method", method, f"repos/{REPOSITORY}/{resource}", "--input", "-"],
                input=json.dumps(payload), text=True, capture_output=True, timeout=90, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise MonitorError(f"Ambiguous {method} {resource}; rerun to reconcile before retrying") from error
        self.last_write = time.monotonic()
        self.writes += 1
        if response.returncode:
            raise MonitorError(f"Failed/ambiguous {method} {resource}; stop and reconcile on rerun")
        try:
            return json.loads(response.stdout)
        except ValueError as error:
            raise MonitorError(f"Ambiguous response for {resource}; reconcile on rerun") from error


def identities(issues):
    mapping = {}
    for issue in issues:
        if "pull_request" in issue:
            continue
        body = issue.get("body") or ""
        markers = re.findall(r"<!-- wheel-monitor:(master|package=[a-z0-9-]+) -->", body)
        if not markers:
            continue
        if issue["user"]["login"] not in AUTHORS:
            raise MonitorError(f"Untrusted tracking marker on issue {issue['number']}")
        if len(markers) != 1 or markers[0] in mapping:
            raise MonitorError("Duplicate/ambiguous tracker issue markers; resolve manually")
        mapping[markers[0]] = issue
    return mapping


def checked_report(config, report, now):
    validate(config)
    if report.get("tracker") != REPOSITORY or report.get("mode") != "dry-run":
        raise MonitorError("Unexpected report origin/mode")
    revision = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    if report.get("registry_revision") != revision:
        raise MonitorError("Report does not match the current registry revision")
    expected = {p["package"] for p in config["packages"]}
    names = [p["package"] for p in report["packages"]]
    if len(names) != len(set(names)) or set(names) != expected:
        raise MonitorError("Registry/report package coverage mismatch")
    if now - instant(report["checked_at"]) > timedelta(hours=72) or instant(report["checked_at"]) > now:
        raise MonitorError("Report must be from the last 72 hours, not the future")
    if len(names) > 100:
        raise MonitorError("A master issue supports at most 100 direct sub-issues")
    return {p["package"]: p for p in report["packages"]}


def package_body(package):
    lines = [marker(package["package"]), "",
             "Permanent Windows Arm64 wheel-build and related-test tracker.", "",
             f"PyPI: {package.get('pypi_url', 'https://pypi.org/project/' + package['package'] + '/')}",
             "", "## Upstream"]
    lines.extend("- " + url for url in package.get("source_repositories", []))
    lines.extend(["", "## Monitoring coverage"])
    for channel in package.get("channels", []):
        lines.append(f"- `{channel['id']}`: {channel['repository']} / `{channel['workflow']}`")
        lines.append("  - " + literal(channel["baseline"]))
    if not package.get("channels"):
        lines.append(literal(package["setup_reason"]))
    lines.extend(["", "## Lifecycle",
                  "- Remains open when healthy; close deliberately to pause monitoring.",
                  "- The bot-owned status comment is refreshed daily; this body is not rewritten.",
                  "- Incident comments and participant mentions are not enabled during observation rollout.",
                  "- PyPI publication is not inferred from build success.",
                  f"- Monitoring owner: {package['owner']} (not automatically assigned during bootstrap)."])
    return "\n".join(lines)


def status_body(package, checked_at):
    lines = [STATUS, "", f"## Current health: {package['health']}", "",
             f"Last checked: {checked_at}. Daily observation; no incident pings enabled.", "",
             "Daily activity: " + package.get("activity", "Not recorded") + ".",
             "Health is last verified evidence, not an assertion that a new build ran today.", "",
             "| Target | Health | Evidence |", "| --- | --- | --- |"]
    for key, result in (package.get("last_known") or package["results"]).items():
        detail = literal(result["detail"])
        if result.get("url"):
            detail += f" - [run]({result['url']})"
        for platform in ("arm64", "amd64"):
            for job in result.get(platform, {}).get("jobs", []):
                detail += f" - [{platform}]({job['url']})"
        if result.get("warning"):
            detail += " - " + literal(result["warning"])
        lines.append(f"| {literal(key)} | {result['health']} | {detail} |")
    if package["errors"]:
        lines.extend(["", "### Monitoring errors", *["- " + literal(e) for e in package["errors"]]])
    body = "\n".join(lines)
    if len(body) > 60000:
        raise MonitorError(f"Status for {package['package']} exceeds safe comment size")
    return body


def master_status(report, mapping):
    counts = Counter(p["health"] for p in report["packages"])
    lines = [STATUS, "", "## Fleet health", "", f"Last checked: {report['checked_at']}.",
             "Permanent tracking issues stay open when healthy; sub-issue completion is not build health.",
             "", "; ".join(f"**{health}: {counts[health]}**" for health in HEALTH), "",
             "| Package | Health | Tracker |", "| --- | --- | --- |"]
    for package in report["packages"]:
        issue = mapping["package=" + package["package"]]
        lines.append(f"| {package['package']} | {package['health']} | [tracking issue]({issue['html_url']}) |")
    return "\n".join(lines)


def ensure_labels(api):
    labels = {entry["name"] for entry in api.pages(f"repos/{REPOSITORY}/labels")}
    required = {"wheel-monitor": "0969da", **{"health:" + h.lower(): c for h, c in HEALTH.items()}}
    for name, color in required.items():
        if name not in labels:
            api.write("POST", "labels", {"name": name, "color": color, "description": "Wheel monitoring automation"})


def update_status(api, issue, body):
    comments = api.pages(f"repos/{REPOSITORY}/issues/{issue['number']}/comments")
    marked = [c for c in comments if (c.get("body") or "").startswith(STATUS)]
    if any(c["user"]["login"] not in AUTHORS for c in marked) or len(marked) > 1:
        raise MonitorError(f"Conflicting status markers on issue {issue['number']}")
    if not marked:
        api.write("POST", f"issues/{issue['number']}/comments", {"body": body})
    elif marked[0]["body"] != body:
        api.write("PATCH", f"issues/comments/{marked[0]['id']}", {"body": body})


def sync_health(api, issue, health):
    owned = {"health:" + value.lower() for value in HEALTH}
    labels = {label["name"] for label in issue["labels"]}
    desired = (labels - owned) | {"wheel-monitor", "health:" + health.lower()}
    if labels != desired:
        api.write("PATCH", f"issues/{issue['number']}", {"labels": sorted(desired)})


def reconcile(api, config, report, bootstrap=False):
    results = checked_report(config, report, utcnow())
    mapping = identities(api.pages(f"repos/{REPOSITORY}/issues", params={"state": "all"}))
    missing = ["master"] if "master" not in mapping else []
    missing += ["package=" + p["package"] for p in config["packages"] if "package=" + p["package"] not in mapping]
    preview = {"tracker": REPOSITORY, "new_issues": len(missing), "packages": len(results),
               "missing": missing, "apply": api.apply, "bootstrap": bootstrap}
    print(json.dumps(preview, indent=2), flush=True)
    if not api.apply:
        return preview
    if missing and not bootstrap:
        raise MonitorError("Mapped tracker issues missing; ordinary synchronization never creates replacements")
    if "master" in mapping and mapping["master"]["state"] != "open":
        raise MonitorError("Master tracker is closed; manual review required")
    ensure_labels(api)
    if "master" not in mapping:
        mapping["master"] = api.write("POST", "issues", {
            "title": "Windows Arm64 Python wheel monitoring",
            "body": MASTER + "\n\nPermanent package trackers are linked as sub-issues.\n"
                    "The status comment summarizes daily build/test health, not completion percentage.\n"
                    "Projects is deferred. Incident pings are not enabled during observation rollout.",
            "labels": ["wheel-monitor"],
        })
    parent = mapping["master"]
    children = {issue["id"] for issue in api.pages(
        f"repos/{REPOSITORY}/issues/{parent['number']}/sub_issues"
    )}
    for package in config["packages"]:
        name = package["package"]
        key = "package=" + name
        if key not in mapping:
            mapping[key] = api.write("POST", "issues", {
                "title": f"[Wheel monitor] {name} - Windows Arm64",
                "body": package_body(package), "labels": ["wheel-monitor", "health:" + results[name]["health"].lower()],
            })
        issue = mapping[key]
        if issue["id"] not in children:
            # Do not replace an existing parent. GitHub rejects conflicts.
            api.write("POST", f"issues/{parent['number']}/sub_issues", {"sub_issue_id": issue["id"]})
            children.add(issue["id"])
        if issue["state"] == "closed":
            sync_health(api, issue, "Paused")
            continue
        update_status(api, issue, status_body(results[name], report["checked_at"]))
        sync_health(api, issue, results[name]["health"])
        print(f"Reconciled {name}: {issue['html_url']}", flush=True)
    displayed = copy.deepcopy(report)
    for package in displayed["packages"]:
        if mapping["package=" + package["package"]]["state"] == "closed":
            package["health"] = "Paused"
    update_status(api, parent, master_status(displayed, mapping))
    expected = {mapping["package=" + p["package"]]["id"] for p in config["packages"]}
    actual = {issue["id"] for issue in api.pages(
        f"repos/{REPOSITORY}/issues/{parent['number']}/sub_issues"
    )}
    if not expected.issubset(actual):
        raise MonitorError("Hierarchy verification failed; some package issues are not linked")
    print(f"Verified {len(expected)} package sub-issues under {parent['html_url']}; writes={api.writes}", flush=True)
    return {**preview, "master": parent["html_url"], "issues": {
        p["package"]: mapping["package=" + p["package"]]["html_url"] for p in config["packages"]
    }, "writes": api.writes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=ROOT / "fleet.json")
    parser.add_argument("--report", type=Path, default=Path("monitor-output") / "report.json")
    parser.add_argument("--output", type=Path, default=Path("monitor-output") / "issues.json")
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = reconcile(IssueAPI(apply=args.apply), read_json(args.registry), read_json(args.report), args.bootstrap)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except MonitorError as error:
        print(f"Issue reconciliation error: {error}", file=sys.stderr)
        sys.exit(1)
