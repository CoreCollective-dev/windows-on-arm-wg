"""Inspect all audited packages and propose exact, evidence-backed job selectors."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote, unquote

from monitoring.monitor import (
    APIError, GitHub, MonitorError, REPOSITORY, ROOT, SCHEMA,
    attempt_jobs, instant, literal, read_json, stamp, utcnow, validate,
)
from monitoring.pairing import comparator, is_windows
from monitoring.recent import EVENTS


def workflow_sources(package):
    result = []
    for url in package["builder_evidence"]:
        match = re.match(r"https://github\.com/([^/]+/[^/]+)/blob/.+?/\.github/workflows/([^/#]+)", url)
        if match and match.groups() not in result:
            result.append(match.groups())
    # Shared templates cannot be queried as if they were the caller's workflow.
    repositories = {url.removeprefix("https://github.com/").lower() for url in package["repositories"]}
    return [(repo, filename) for repo, filename in result if repo.lower() in repositories]


def windows_arm_wheel(job):
    name = job["name"]
    windows_arm = is_windows(job) and re.search(
        r"arm64|aarch64|windows-11-arm", name + " " + " ".join(job.get("labels", [])), re.I
    )
    if not windows_arm:
        return False
    wheel = re.search(r"wheel|cibuildwheel|maturin", name, re.I) or any(
        re.search(r"wheel|cibuildwheel|maturin", step["name"], re.I)
        for step in job.get("steps", [])
    )
    return bool(wheel and job.get("steps"))


def eligible(run, default_branch, now):
    if run["event"] not in EVENTS:
        return False
    if run["status"] != "completed" or run["conclusion"] == "cancelled":
        return False
    if instant(run["created_at"]) < now - timedelta(hours=24):
        return False
    return True


def inspect(api, package, now):
    channels = []
    examined = []
    sources = workflow_sources(package)
    cache = {}
    queue = list(sources)
    seen = set()
    while queue and len(seen) < 6:
        repository, workflow = queue.pop(0)
        key = (repository.lower(), workflow)
        if key in seen:
            continue
        seen.add(key)
        if repository.lower() not in cache:
            cache[repository.lower()] = api.get(f"repos/{repository}")
        metadata = cache[repository.lower()]
        if metadata["archived"]:
            continue
        repository = metadata["full_name"]
        path = f"repos/{repository}/actions/workflows/{quote(workflow)}/runs"
        try:
            recent = api.get(path, {"branch": metadata["default_branch"], "status": "completed", "per_page": 20})
            runs = recent["workflow_runs"]
            if not any(eligible(run, metadata["default_branch"], now) for run in runs):
                runs = api.get(path, {"status": "completed", "per_page": 20})["workflow_runs"]
        except APIError as error:
            if error.status != 404:
                raise
            examined.append(f"{repository}/{workflow}: workflow not found")
            runs = []
        found = False
        for run in [r for r in runs if eligible(r, metadata["default_branch"], now)][:3]:
            jobs = attempt_jobs(api, repository, run)
            selected = [job for job in jobs if windows_arm_wheel(job)]
            examined.append(f"{run['html_url']}: {len(jobs)} jobs, {len(selected)} explicit Windows Arm64 wheel jobs")
            if not selected:
                continue
            channel_id = "observed-" + hashlib.sha256(f"{repository}/{workflow}".encode()).hexdigest()[:10]
            targets = []
            for name in sorted({job["name"] for job in selected}):
                arm = next(job for job in selected if job["name"] == name)
                control = comparator(arm, jobs)
                target = {"id": "job-" + hashlib.sha256(name.encode()).hexdigest()[:10],
                          "job": "^" + re.escape(name) + "$",
                          "comparison_blocker": "Automatic candidate: verify named distribution, Python/ABI and build/test-stage equivalence before enabling a failure verdict."}
                if control:
                    target["amd64_job"] = "^" + re.escape(control["name"]) + "$"
                    target["comparison_evidence"] = {"arm64": arm["html_url"], "amd64": control["html_url"]}
                targets.append(target)
            channels.append({
                "id": channel_id,
                "repository": repository,
                "workflow": workflow,
                "events": [run["event"]],
                "scopes": [{"id": "observed-ref", "ref": "^" + re.escape(run["head_branch"]) + "$"}],
                "targets": targets,
                "baseline": f"{stamp(now)}: observed job/step records at {run['html_url']}; "
                            "exact sampled ref and names only; no inferred future release/target coverage.",
            })
            found = True
            break
        if found:
            break
        # Look for actual callers when the audit cited only a reusable template.
        if len(queue) == 0:
            workflows = api.pages(f"repos/{repository}/actions/workflows", "workflows")
            for candidate in workflows:
                filename = candidate["path"].rsplit("/", 1)[-1]
                if candidate["state"] == "active" and filename.endswith((".yml", ".yaml")) and re.search(
                    r"wheel|build|release|publish|ci-cd", filename, re.I
                ) and (repository.lower(), filename) not in seen:
                    queue.append((repository, filename))
    return channels, examined


def enroll(api, inventory, pilot, now, previous=None):
    pilots = {entry["package"]: entry for entry in validate(pilot)["packages"]}
    packages = []
    observations = []
    for candidate in inventory["packages"]:
        name = candidate["package"]
        entry = {"package": name, "owner": "khmyznikov",
                 "pypi_url": f"https://pypi.org/project/{name}/",
                 "source_repositories": candidate["repositories"]}
        notes = []
        error = None
        if name in pilots:
            entry.update(copy.deepcopy(pilots[name]))
            outcome = "verified-pilot"
        elif previous and (previous[name]["config"].get("channels") or previous[name]["config"].get("reviewed")):
            entry = copy.deepcopy(previous[name]["config"])
            outcome = previous[name]["report"]["outcome"]
            notes = previous[name]["report"]["examined"]
        elif candidate["onboarding"] == "paused-archived":
            outcome = "paused-archived"
            entry.update(channels=[], setup_health="Paused",
                         setup_reason="Audited wheel-packaging repository is archived; newest-release provenance unverified.")
        else:
            try:
                api.package_limit = api.requests + 30
                channels, notes = inspect(api, candidate, now)
                outcome = "observed-targets" if channels else "needs-verification"
                entry["channels"] = channels
                if not channels:
                    entry.update(setup_health="Attention",
                                 setup_reason="No explicit Windows Arm64 wheel job identified in sampled eligible runs. "
                                              "This is not proof that support is absent; selectors/callers need review.")
            except MonitorError as failure:
                outcome = "access-or-discovery-error"
                error = str(failure)
                entry.update(channels=[], setup_health="Unknown", setup_reason=error)
            finally:
                api.package_limit = None
        packages.append(entry)
        observations.append({"package": name, "outcome": outcome, "examined": notes,
                             "channels": len(entry["channels"]), "error": error})
        print(f"{name}: {outcome}", flush=True)
    config = {"schema": SCHEMA, "tracker": REPOSITORY, "strategy": "master-and-sub-issues",
              "discovered_at": stamp(now), "packages": packages}
    return validate(config), {"checked_at": stamp(now), "packages": observations, "requests": api.requests}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT)
    parser.add_argument("--refresh-unverified", action="store_true")
    args = parser.parse_args()
    api = GitHub(budget=1400)
    previous = None
    if args.refresh_unverified:
        configs = {p["package"]: p for p in read_json(args.output / "fleet.json")["packages"]}
        reports = {p["package"]: p for p in read_json(args.output / "enrollment.json")["packages"]}
        previous = {name: {"config": config, "report": reports[name]} for name, config in configs.items()}
    config, report = enroll(api, read_json(ROOT / "inventory.json"), read_json(ROOT / "pilot.json"), utcnow(), previous)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "fleet.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    (args.output / "enrollment.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    counts = Counter(p["outcome"] for p in report["packages"])
    rows = ["# Full-fleet enrollment inspection", "", f"Checked: {report['checked_at']}.",
            "Automatic observations are exact sampled selectors, not a claim of complete Python/release coverage.",
            "", "| Package | Outcome | Channels | Evidence / next action |", "| --- | --- | --- |"]
    for package in report["packages"]:
        note = package["error"] or "; ".join(package["examined"]) or package["outcome"]
        rows.append(f"| {package['package']} | {package['outcome']} | {package['channels']} | {literal(note)} |")
    (args.output / "ENROLLMENT.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(json.dumps(dict(counts), indent=2))
    print(f"REST requests: {api.requests}")
    return int(any(p["error"] for p in report["packages"]))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MonitorError as error:
        print(f"Enrollment error: {error}", file=sys.stderr)
        sys.exit(1)
