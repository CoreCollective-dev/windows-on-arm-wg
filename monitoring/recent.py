"""Evaluate every relevant recent run while isolating PR, fork, and ref streams."""

import copy
import re
from datetime import timedelta
from urllib.parse import quote

from monitoring.monitor import MonitorError, attempt_jobs, discover, evaluate, instant, stamp
from monitoring.wheel_log import cibuildwheel_jobs


EVENTS = ["push", "pull_request", "schedule", "workflow_dispatch", "repository_dispatch", "release", "merge_group"]


def stream_identity(api, channel, run):
    repository = run.get("head_repository") or {}
    origin = str(repository.get("id") or repository.get("full_name") or "")
    if not origin:
        raise MonitorError(f"Run {run['id']} has no head repository identity")
    if run["event"] == "pull_request":
        pulls = run.get("pull_requests", [])
        numbers = sorted({pr["number"] for pr in pulls
                          if pr.get("head", {}).get("sha") == run["head_sha"]
                          and str((pr.get("head", {}).get("repo") or {}).get("id")) == origin})
        # Do not guess identity or spend a request per commit when PRs are omitted.
        suffix = ",".join(map(str, numbers)) if numbers else "commit-" + run["head_sha"]
        return "pr/" + origin + "/" + suffix
    return "ref/" + origin + "/" + quote(run["head_branch"], safe="") + "/" + run["event"]


def same_request(candidate, run):
    def identity(value):
        repo = value.get("head_repository") or {}
        return repo.get("id") or repo.get("full_name")
    if not identity(run) or identity(candidate) != identity(run):
        return False
    if (candidate["head_sha"], candidate["head_branch"], candidate["event"]) != (
        run["head_sha"], run["head_branch"], run["event"]
    ):
        return False
    if run["event"] == "pull_request":
        a = {pr["number"] for pr in run.get("pull_requests", [])}
        b = {pr["number"] for pr in candidate.get("pull_requests", [])}
        if a and b and a != b:
            return False
    return True


def observe_recent(api, channel, prior, now):
    runs = discover(api, channel, now - timedelta(hours=24), now)
    known = {run["id"]: run for run in runs}
    for identifier in prior.get("pending_runs", []):
        if identifier not in known:
            candidate = api.get(f"repos/{channel['repository']}/actions/runs/{identifier}")
            known[identifier] = candidate
    eligible = [run for run in known.values() if run["event"] in EVENTS]
    results, observations, pending = {}, [], []
    for run in sorted(eligible, key=lambda value: (value["created_at"], value["id"])):
        if run["status"] != "completed" or now - instant(run["updated_at"]) < timedelta(minutes=30):
            pending.append(run["id"])
            continue
        if run["conclusion"] == "cancelled":
            continue
        stream = stream_identity(api, channel, run)
        comparable = any(
            (target.get("amd64_job") or target.get("amd64_jobs")) and not target.get("comparison_blocker")
            for target in channel["targets"]
        )
        # Run metadata is still collected, but unconfigured comparisons cannot
        # benefit from downloading hundreds of job graphs.
        jobs = attempt_jobs(api, channel["repository"], run) if comparable else []
        if channel.get("source_ref"):
            rule = channel["source_ref"]
            refs = [match.group(rule["group"]) for job in jobs
                    if (match := re.fullmatch(rule["pattern"], job["name"]))]
            if refs and all(value != rule["value"] for value in refs):
                continue
            stream += "/source-" + quote(rule["value"], safe="")
        if channel.get("aggregate_job"):
            matches = [job for job in jobs if re.fullmatch(channel["aggregate_job"], job["name"])]
            if len(matches) == 1 and matches[0]["conclusion"] not in ("skipped", "cancelled"):
                derived = cibuildwheel_jobs(matches[0], api.job_log(channel["repository"], matches[0]["id"]))
                if not derived:
                    raise MonitorError("No supported cibuildwheel interpreter blocks found in aggregate job log")
                jobs = derived
        controls = {}
        for target in channel["targets"]:
            amd_jobs, context = None, None
            if comparable and target.get("amd64_workflow") and not target.get("comparison_blocker"):
                key = target["amd64_workflow"]
                if key not in controls:
                    path = f"repos/{channel['repository']}/actions/workflows/{quote(key)}/runs"
                    matches = api.pages(path, "workflow_runs", {"head_sha": run["head_sha"], "branch": run["head_branch"]})
                    matching = [candidate for candidate in matches if same_request(candidate, run)]
                    if matching:
                        control = max(matching, key=lambda value: (value["created_at"], value["id"]))
                        controls[key] = (attempt_jobs(api, channel["repository"], control), {
                            "arm_run_id": run["id"], "amd64_run_id": control["id"],
                            "source_sha": run["head_sha"], "ref": run["head_branch"],
                            "amd64_run_url": control["html_url"],
                        })
                    else:
                        controls[key] = ([], None)
                amd_jobs, context = controls[key]
            observed_target = copy.deepcopy(target)
            if channel.get("aggregate_job"):
                observed_target["interpreter_evidence"] = "cibuildwheel-windows-v1"
            value = evaluate(jobs, observed_target, channel.get("required_steps", []), amd_jobs, context)
            if not comparable:
                value.update(condition="comparison-withheld", not_applicable=False,
                             detail=target.get("comparison_blocker") or "AMD64 comparison mapping is not configured; run discovered but not eligible for a failure verdict")
            value.update(run_id=run["id"], attempt=run["run_attempt"], sha=run["head_sha"],
                         ref=run["head_branch"], created_at=run["created_at"], observed_at=run["updated_at"],
                         url=run["html_url"], event=run["event"], stream=stream)
            if value.get("not_applicable"):
                continue
            key = stream + "/" + target["id"]
            results[key] = value
            observations.append({"key": key, "value": copy.deepcopy(value)})
    if not results:
        results["window"] = {
            "health": "Unknown", "condition": "no-recent-comparison", "notify": False, "jobs": [],
            "detail": "No applicable settled comparison in the past 24 hours; not a passing or failing build",
        }
    return {"results": results, "observations": observations, "pending_runs": pending,
            "last_complete_scan": stamp(now), "window_hours": 24}
