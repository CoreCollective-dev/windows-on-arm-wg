"""Observe public upstream Actions and plan tracker transitions without writes."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, quote


ROOT = Path(__file__).resolve().parent
SCHEMA = 1
REPOSITORY = "CoreCollective-dev/windows-on-arm-wg"
ARTIFACT = "wheel-monitor-report"
HEALTH_ORDER = ("Broken", "Attention", "Unknown", "Healthy")


class MonitorError(RuntimeError):
    pass


def utcnow():
    return datetime.now(timezone.utc)


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def normalize(value):
    return re.sub(r"[-_.]+", "-", value).lower()


def literal(value):
    return str(value).replace("@", "@\u200b").replace("`", "'").replace("|", "\\|").replace("\n", " ")


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise MonitorError(f"Cannot read {path}: {error}") from error


def validate(config):
    if config.get("schema") != SCHEMA or config.get("tracker") != REPOSITORY:
        raise MonitorError("Unsupported registry schema or unexpected tracker repository")
    seen = set()
    if not config.get("packages"):
        raise MonitorError("Registry contains no packages")
    for package in config["packages"]:
        name = package.get("package", "")
        if not name or name != normalize(name) or name in seen:
            raise MonitorError(f"Invalid or duplicate package: {name}")
        seen.add(name)
        if not package.get("owner") or not re.fullmatch(r"[A-Za-z0-9-]+", package["owner"]):
            raise MonitorError(f"Missing/invalid fallback owner: {name}")
        if not package.get("channels") and not package.get("setup_reason"):
            raise MonitorError(f"No channels or setup explanation: {name}")
        channels = set()
        for channel in package.get("channels", []):
            if channel.get("id") in channels or not re.fullmatch(r"[a-z0-9-]+", channel.get("id", "")):
                raise MonitorError(f"Invalid/duplicate channel: {name}")
            channels.add(channel["id"])
            if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", channel.get("repository", "")):
                raise MonitorError(f"Invalid upstream repository: {name}")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", channel.get("workflow", "")):
                raise MonitorError(f"Invalid workflow filename: {name}")
            if not channel.get("events") or not channel.get("scopes") or not channel.get("targets"):
                raise MonitorError(f"Incomplete channel: {name}/{channel['id']}")
            if set(channel["events"]) - {"push", "schedule", "workflow_dispatch", "release"}:
                raise MonitorError("Pilot excludes PR/unreviewed workflow event types")
            for expression in [s["ref"] for s in channel["scopes"]] + [
                t["job"] for t in channel["targets"]
            ] + [s["pattern"] for s in channel.get("required_steps", [])]:
                if not expression.startswith("^") or not expression.endswith("$"):
                    raise MonitorError(f"Unanchored selector: {name}: {expression}")
                try:
                    re.compile(expression)
                except re.error as error:
                    raise MonitorError(f"Invalid regex for {name}: {error}") from error
            for field in ("scopes", "targets"):
                ids = [entry["id"] for entry in channel[field]]
                if len(ids) != len(set(ids)):
                    raise MonitorError(f"Duplicate {field}: {name}")
            if not channel.get("baseline"):
                raise MonitorError(f"Unverified channel: {name}")
    return config


class GitHub:
    """Only GET requests are exposed; this deployment cannot create issues."""

    def __init__(self, budget=800, sleeper=time.sleep):
        self.requests = 0
        self.limit = budget
        self.sleep = sleeper
        self.remaining = None
        self.package_limit = None

    def get(self, path, params=None):
        if not re.fullmatch(r"repos/[A-Za-z0-9_.%/-]+", path):
            raise MonitorError(f"Refusing unexpected GitHub API path: {path}")
        if params:
            path += "?" + urlencode(params)
        for attempt in range(4):
            if self.requests >= self.limit or (
                self.package_limit is not None and self.requests >= self.package_limit
            ):
                raise MonitorError(f"REST request budget ({self.limit}) exhausted; scan incomplete")
            self.requests += 1
            try:
                response = subprocess.run(
                    ["gh", "api", "--method", "GET", "--include", path],
                    capture_output=True, timeout=90, check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                if attempt == 3:
                    raise MonitorError(f"GitHub GET failed: {path}: {error}") from error
                self.sleep(2 ** (attempt + 1))
                continue
            text = response.stdout.decode("utf-8").replace("\r\n", "\n")
            head, separator, body = text.partition("\n\n")
            headers = {}
            for line in head.splitlines()[1:]:
                key, colon, value = line.partition(":")
                if colon:
                    headers[key.lower()] = value.strip()
            if headers.get("x-ratelimit-remaining"):
                self.remaining = int(headers["x-ratelimit-remaining"])
            if response.returncode == 0 and separator:
                try:
                    return json.loads(body)
                except ValueError as error:
                    raise MonitorError(f"Invalid GitHub response: {path}") from error
            match = re.search(r"HTTP/[\d.]+ (\d+)", head)
            status = int(match[1]) if match else 0
            retry = status in (429, 500, 502, 503, 504) or (
                status == 403 and "retry-after" in headers
            )
            if not retry or attempt == 3:
                raise MonitorError(f"GitHub GET {path}: HTTP {status or 'unavailable'}")
            delay = max(2 ** (attempt + 1), int(headers.get("retry-after", "0")))
            if delay > 120 or self.remaining == 0:
                raise MonitorError("GitHub rate limit reached; retry on next scheduled poll")
            self.sleep(delay)
        raise MonitorError("GitHub retries exhausted")

    def pages(self, path, key=None, params=None):
        results = []
        page = 1
        while True:
            data = self.get(path, {**(params or {}), "per_page": 100, "page": page})
            items = data[key] if key else data
            if not isinstance(items, list):
                raise MonitorError(f"Unexpected paginated API response: {path}")
            results.extend(items)
            if len(items) < 100:
                return results
            page += 1


def discover(api, channel, start, end):
    """Split search windows to avoid GitHub's 1,000-result search ceiling."""
    path = f"repos/{channel['repository']}/actions/workflows/{quote(channel['workflow'])}/runs"
    params = {"created": f"{stamp(start)}..{stamp(end)}", "per_page": 100, "page": 1}
    first = api.get(path, params)
    if first["total_count"] > 1000:
        if (end - start).total_seconds() <= 1:
            raise MonitorError("Run density exceeds GitHub search ceiling in a one-second window")
        middle = start + timedelta(seconds=int((end - start).total_seconds() // 2))
        runs = discover(api, channel, start, middle) + discover(api, channel, middle, end)
    else:
        runs = first["workflow_runs"]
        page = 2
        while len(runs) < first["total_count"]:
            data = api.get(path, {**params, "page": page})
            if not data["workflow_runs"]:
                raise MonitorError("Run-list pagination ended before total_count; scan incomplete")
            runs.extend(data["workflow_runs"])
            page += 1
    return list({run["id"]: run for run in runs}.values())


def attempt_jobs(api, repository, run):
    jobs = {}
    # Latest-attempt-only API results omit successful jobs not selected for rerun.
    for attempt in range(1, run["run_attempt"] + 1):
        path = f"repos/{repository}/actions/runs/{run['id']}/attempts/{attempt}/jobs"
        for job in api.pages(path, "jobs"):
            if job["head_sha"] != run["head_sha"]:
                raise MonitorError(f"Job SHA differs from run {run['id']}; refusing merged evidence")
            jobs[job["name"]] = job
    return list(jobs.values())


def evaluate(jobs, target, required_steps):
    matches = [j for j in jobs if re.fullmatch(target["job"], j["name"])]
    if not matches:
        return {"health": "Attention", "condition": "coverage", "detail": "Expected Windows Arm64 job missing", "jobs": []}
    observations = []
    for job in matches:
        record = {"health": "Healthy", "condition": "pass", "detail": "Required job passed"}
        steps = job.get("steps", [])
        failed = [s for s in steps if s.get("conclusion") in ("failure", "timed_out")]
        if failed:
            record = {"health": "Broken", "condition": "job-failure",
                      "detail": "Failed step: " + ", ".join(s["name"] for s in failed)}
        elif job.get("status") != "completed" or job.get("conclusion") == "cancelled":
            record = {"health": "Unknown", "condition": "pending", "detail": "Job incomplete or cancelled; not evidence of recovery"}
        elif job.get("conclusion") in ("failure", "timed_out"):
            record = {"health": "Broken", "condition": "job-failure", "detail": "Job failed; required stage not attributable"}
        elif job.get("conclusion") != "success":
            record = {"health": "Attention", "condition": "coverage", "detail": "Required job skipped/neutral"}
        else:
            for requirement in required_steps:
                selected = [s for s in steps if re.fullmatch(requirement["pattern"], s["name"])]
                if not selected or any(s.get("conclusion") != "success" for s in selected):
                    record = {"health": "Attention", "condition": "coverage",
                              "detail": f"Required {requirement['stage']} step absent or not successful"}
                    break
        for requirement in required_steps:
            if any(re.fullmatch(requirement["pattern"], s["name"]) for s in failed):
                record["condition"] = requirement["stage"] + "-failure"
                break
        record["jobs"] = [{"name": job["name"], "url": job["html_url"], "conclusion": job["conclusion"]}]
        observations.append(record)
    worst = min(observations, key=lambda o: HEALTH_ORDER.index(o["health"]))
    return {**worst, "jobs": [j for o in observations for j in o["jobs"]]}


def observe_channel(api, channel, prior, now):
    history_start = now - timedelta(days=90)
    runs = discover(api, channel, history_start, now)
    results = {}
    pending = {}
    for scope in channel["scopes"]:
        eligible = sorted(
            (r for r in runs if r["event"] in channel["events"] and re.fullmatch(scope["ref"], r["head_branch"])),
            key=lambda r: (r["created_at"], r["id"]), reverse=True,
        )
        # Explicit pending IDs survive the discovery horizon until terminal.
        for run_id in prior.get("pending", {}).get(scope["id"], []):
            if not any(r["id"] == run_id for r in eligible):
                run = api.get(f"repos/{channel['repository']}/actions/runs/{run_id}")
                if run["event"] not in channel["events"] or not re.fullmatch(scope["ref"], run["head_branch"]):
                    raise MonitorError("Pending run no longer matches configured event/ref")
                eligible.append(run)
        eligible.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
        pending[scope["id"]] = [r["id"] for r in eligible if r["status"] != "completed"]
        settled = [
            r for r in eligible if r["status"] == "completed" and r["conclusion"] != "cancelled"
            and now - instant(r["updated_at"]) >= timedelta(minutes=30)
        ]
        run = settled[0] if settled else None
        jobs = attempt_jobs(api, channel["repository"], run) if run else []
        for target in channel["targets"]:
            key = f"{scope['id']}/{target['id']}"
            old = prior.get("results", {}).get(key)
            if old and run and (run["created_at"], run["id"]) < (old["created_at"], old["run_id"]):
                results[key] = copy.deepcopy(old)
                continue
            if not run:
                result = copy.deepcopy(old) if old else {
                    "health": "Unknown", "condition": "no-observation",
                    "detail": "No settled eligible run in the 90-day pilot horizon", "jobs": [],
                }
            else:
                result = evaluate(jobs, target, channel.get("required_steps", []))
                result.update(
                    run_id=run["id"], attempt=run["run_attempt"], sha=run["head_sha"],
                    ref=run["head_branch"], created_at=run["created_at"],
                    observed_at=run["updated_at"], url=run["html_url"],
                )
                if result["condition"] == "pending" and old:
                    result = {**copy.deepcopy(old), "warning": result["detail"]}
                elif result["condition"] == "coverage" and old and old["health"] == "Broken":
                    result = {**copy.deepcopy(old), "warning": result["detail"]}
            silence = channel.get("max_silence_hours")
            if silence and result.get("observed_at") and now - instant(result["observed_at"]) > timedelta(hours=silence):
                result["warning"] = f"No valid observation within {silence} hours"
                if result["health"] != "Broken":
                    result["health"], result["condition"] = "Attention", "stale"
            if pending[scope["id"]]:
                result["pending_runs"] = pending[scope["id"]]
            results[key] = result
    return {"results": results, "pending": pending, "last_complete_scan": stamp(now)}


def plan(previous, current, sequence):
    changes = []
    for key in sorted(set(previous) | set(current)):
        old, new = previous.get(key), current.get(key)
        if new is None:
            changes.append({"target": key, "kind": "setup-change", "before": old, "after": None})
        elif (
            (old or {}).get("health") != new["health"]
            or (old or {}).get("condition") != new["condition"]
            or (old or {}).get("quality") != new.get("quality")
        ):
            if new["health"] == "Healthy":
                if old and old["health"] != "Healthy":
                    changes.append({"target": key, "kind": "recovery", "before": old, "after": new})
            else:
                changes.append({"target": key, "kind": "incident", "before": old, "after": new})
    if not changes:
        return None
    return {"sequence": sequence + 1, "changes": changes}


def scan(api, config, state, now, scheduled=False):
    validate(config)
    if state.get("schema") != SCHEMA or state.get("tracker") != config["tracker"]:
        raise MonitorError("State schema/tracker mismatch; explicit reviewed reset required")
    packages = copy.deepcopy(state.get("packages", {}))
    report = {"schema": SCHEMA, "mode": "dry-run", "tracker": config["tracker"],
              "checked_at": stamp(now), "packages": [], "errors": []}
    revision = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    for package in config["packages"]:
        name = package["package"]
        old = packages.get(name, {})
        entry = copy.deepcopy(old)
        entry.setdefault("channels", {})
        results = {}
        errors = []
        if not package.get("channels"):
            results["setup"] = {"health": "Unknown", "condition": "setup",
                                "detail": package["setup_reason"], "jobs": []}
        before_requests = api.requests
        api.package_limit = before_requests + 160
        for channel in package.get("channels", []):
            identifier = channel["id"]
            prior = old.get("channels", {}).get(identifier, {})
            try:
                if api.requests - before_requests >= 160:
                    raise MonitorError("Package read budget exhausted; coverage incomplete")
                fresh = observe_channel(api, channel, prior, now)
                entry["channels"][identifier] = fresh
                for key, value in fresh["results"].items():
                    results[f"{identifier}/{key}"] = value
            except MonitorError as error:
                errors.append(f"{identifier}: {error}")
                for key, value in prior.get("results", {}).items():
                    retained = copy.deepcopy(value)
                    retained["warning"] = "Observation gap: " + str(error)
                    retained["quality"] = "observation-gap"
                    if retained["health"] != "Broken":
                        retained["health"], retained["condition"] = "Unknown", "observation-gap"
                    results[f"{identifier}/{key}"] = retained
                if not prior.get("results"):
                    for scope in channel["scopes"]:
                        for target in channel["targets"]:
                            results[f"{identifier}/{scope['id']}/{target['id']}"] = {
                                "health": "Unknown", "condition": "observation-gap",
                                "quality": "observation-gap", "detail": str(error), "jobs": [],
                            }
        for key in old.get("results", {}).keys() - results.keys():
            removed = copy.deepcopy(old["results"][key])
            if removed["health"] != "Broken":
                removed["health"] = "Attention"
            removed["condition"] = "setup-change"
            removed["warning"] = "Previously monitored target removed; reviewed retirement required"
            results[key] = removed
        entry["results"] = results
        entry["gap_polls"] = (old.get("gap_polls", 0) + int(scheduled)) if errors else 0
        event = plan(old.get("notification_results", {}), results, old.get("sequence", 0))
        # An outage is operationally visible immediately, but not a participant ping.
        if errors and entry["gap_polls"] < 2:
            event = None
        if event:
            entry["sequence"] = event["sequence"]
            event["key"] = f"{name}/{event['sequence']}"
        if not errors or entry["gap_polls"] >= 2:
            entry["notification_results"] = copy.deepcopy(results)
        health = min((r["health"] for r in results.values()), key=HEALTH_ORDER.index)
        entry["health"] = health
        packages[name] = entry
        report["packages"].append({"package": name, "health": health, "owner": package["owner"],
                                   "results": results, "planned_notification": event, "errors": errors})
        report["errors"].extend(f"{name}: {error}" for error in errors)
    output = {"schema": SCHEMA, "tracker": config["tracker"], "packages": packages,
              "checked_at": stamp(now), "registry_revision": revision}
    report["requests"] = api.requests
    api.package_limit = None
    report["rate_remaining"] = getattr(api, "remaining", None)
    return report, output


def summary(report):
    lines = ["# Windows Arm64 wheel monitor: read-only pilot", "",
             f"Checked: {report['checked_at']}. Polling: every 24 hours.",
             "No issues, comments, mentions, labels or Project items were changed.", "",
             "| Package | Health | Observation | Planned transition |",
             "| --- | --- | --- | --- |"]
    for package in report["packages"]:
        event = package["planned_notification"]
        descriptions = []
        for key, result in package["results"].items():
            evidence = f"[run]({result['url']})" if result.get("url") else "no run"
            descriptions.append(f"{literal(key)}: {literal(result['detail'])} ({evidence})")
            if result.get("warning"):
                descriptions.append("Warning: " + literal(result["warning"]))
        lines.append(f"| {package['package']} | {package['health']} | "
                     f"{'<br>'.join(descriptions)} | {len(event['changes']) if event else 0} |")
    if report["errors"]:
        lines.extend(["", "## Incomplete observations", *["- " + literal(e) for e in report["errors"]]])
    lines.extend(["", f"REST requests: {report['requests']}; last reported quota remaining: {report['rate_remaining']}.",
                  "These are build/test observations, not proof of published PyPI wheels."])
    return "\n".join(lines) + "\n"


def restore(api, directory, run_id):
    """Restore only artifacts from this workflow's trusted main-branch executions."""
    runs = api.pages(f"repos/{REPOSITORY}/actions/workflows/wheel-monitor.yml/runs",
                     "workflow_runs", {"branch": "main", "status": "completed"})
    previous = [r for r in runs if r["id"] < run_id and r["event"] in ("schedule", "workflow_dispatch")]
    for run in sorted(previous, key=lambda r: r["id"], reverse=True):
        artifacts = api.pages(f"repos/{REPOSITORY}/actions/runs/{run['id']}/artifacts", "artifacts")
        if not any(a["name"] == ARTIFACT and not a["expired"] for a in artifacts):
            print(f"Warning: monitor run {run['id']} has no retained report; checking an earlier execution",
                  file=sys.stderr)
            continue
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run(
                ["gh", "run", "download", str(run["id"]), "--repo", REPOSITORY,
                 "--name", ARTIFACT, "--dir", temp],
                capture_output=True, text=True, timeout=120, check=False,
            )
            if result.returncode:
                raise MonitorError(f"Cannot restore state from monitor run {run['id']}")
            state = read_json(Path(temp) / "state.json")
            if state.get("schema") != SCHEMA or state.get("tracker") != REPOSITORY:
                raise MonitorError("Untrusted or incompatible restored state")
            Path(directory).mkdir(parents=True, exist_ok=True)
            (Path(directory) / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        return run["id"]
    if previous:
        raise MonitorError("Previous monitor executions exist but no retained state; reviewed reset required")
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=ROOT / "pilot.json")
    parser.add_argument("--state", type=Path)
    parser.add_argument("--output", type=Path, default=Path("monitor-output"))
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    config = validate(read_json(args.registry))
    api = GitHub()
    if args.restore:
        if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
            raise MonitorError("Artifact restore is only supported in the tracker repository")
        restored = restore(api, args.output, int(os.environ["GITHUB_RUN_ID"]))
        print(f"Restored monitor state: {restored or 'first execution'}")
        args.state = args.output / "state.json" if restored else None
    state = read_json(args.state) if args.state else {"schema": SCHEMA, "tracker": REPOSITORY, "packages": {}}
    report, output = scan(api, config, state, utcnow(), args.scheduled)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "state.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    text = summary(report)
    (args.output / "summary.md").write_text(text, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as file:
            file.write(text)
    print(text)
    return int(bool(report["errors"]))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MonitorError as error:
        print(f"Monitor error: {error}", file=sys.stderr)
        sys.exit(1)
