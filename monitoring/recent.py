"""Evaluate every relevant recent run while isolating PR, fork, and ref streams."""

import copy
import re
from datetime import timedelta
from urllib.parse import quote

from monitoring.monitor import MonitorError, attempt_jobs, discover, evaluate, instant, stamp
from monitoring.wheel_log import cibuildwheel_jobs


EVENTS = ["push", "pull_request", "schedule", "workflow_dispatch", "repository_dispatch", "release", "merge_group"]


def resolve_pull_requests(api, channel, run):
    repository = run.get("head_repository") or {}
    origin = repository.get("id")
    direct = [pull for pull in run.get("pull_requests", [])
              if pull.get("head", {}).get("sha") == run["head_sha"]
              and (pull.get("head", {}).get("repo") or {}).get("id") == origin]
    if len(direct) == 1:
        return direct
    if not repository.get("full_name") or not origin:
        return []
    # GitHub commonly omits pull_requests in run records, even for open PRs.
    # Branch matching alone is insufficient: validate commit membership as well.
    owner = repository["full_name"].split("/", 1)[0]
    candidates = api.pages(f"repos/{channel['repository']}/pulls", params={
        "state": "all", "head": owner + ":" + run["head_branch"],
    })
    verified = []
    for pull in candidates:
        head = pull.get("head") or {}
        if (head.get("repo") or {}).get("id") != origin or head.get("ref") != run["head_branch"]:
            continue
        member = head.get("sha") == run["head_sha"]
        if not member:
            commits = api.pages(f"repos/{channel['repository']}/pulls/{pull['number']}/commits")
            member = any(commit["sha"] == run["head_sha"] for commit in commits)
        if member:
            verified.append(pull)
    return verified if len(verified) == 1 else []


def stream_identity(api, channel, run):
    repository = run.get("head_repository") or {}
    origin = str(repository.get("id") or repository.get("full_name") or "")
    if not origin:
        raise MonitorError(f"Run {run['id']} has no head repository identity")
    if run["event"] == "pull_request":
        pulls = resolve_pull_requests(api, channel, run)
        numbers = sorted({pr["number"] for pr in pulls})
        # Keep an immutable fallback when identity cannot be proven.
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


def observe_recent(api, channel, prior, now, *, runs=None, retain=True, historical_success=False):
    if runs is None:
        runs = discover(api, channel, now - timedelta(hours=24), now)
    daily_ids = {run["id"] for run in runs}
    known = {run["id"]: run for run in runs}
    prior = copy.deepcopy(prior)
    aliases = {}
    failed_runs = {}
    stored = list(prior.get("last_known", {}).items()) if channel.get("recovery_catchup") else []
    for key, value in stored:
        if not value.get("arm_specific_failure"):
            continue
        run = api.get(f"repos/{channel['repository']}/actions/runs/{value['run_id']}")
        current_stream = stream_identity(api, channel, run)
        if value["stream"].startswith("pr/") and "/commit-" not in value["stream"]:
            if "/commit-" in current_stream:
                # A previously proven PR identity is stronger than a transient
                # association omission; never downgrade it to an isolated commit.
                current_stream = value["stream"].split("/source-", 1)[0]
        if channel.get("source_ref"):
            current_stream += "/source-" + quote(channel["source_ref"]["value"], safe="")
        failed_runs[key] = run
        if current_stream != value["stream"]:
            canonical = current_stream + "/" + key.rsplit("/", 1)[-1]
            migrated = {**value, "stream": current_stream}
            existing = prior["last_known"].get(canonical)
            if not existing or (value["created_at"], value["run_id"], value["attempt"]) >= (
                existing["created_at"], existing["run_id"], existing["attempt"]
            ):
                prior["last_known"][canonical] = migrated
            del prior["last_known"][key]
            aliases[key] = canonical
    catchup_streams = set()
    if retain and channel.get("recovery_catchup"):
        for key, failed_run in failed_runs.items():
            stream = aliases.get(key, key).rsplit("/", 1)[0]
            if stream in catchup_streams:
                continue
            catchup_streams.add(stream)
            # Recovery must not depend on the next poll landing inside 24 hours.
            candidates = discover(api, channel, instant(failed_run["created_at"]), now,
                                  {"branch": failed_run["head_branch"], "event": failed_run["event"]})
            candidates.append(failed_run)
            for candidate in candidates:
                candidate_stream = stream_identity(api, channel, candidate)
                if channel.get("source_ref"):
                    candidate_stream += "/source-" + quote(channel["source_ref"]["value"], safe="")
                if candidate_stream == stream:
                    known[candidate["id"]] = candidate
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
        for previous in prior.get("last_known", {}).values():
            if (previous.get("run_id") == run["id"] and previous["stream"].startswith("pr/")
                    and "/commit-" not in previous["stream"] and "/commit-" in stream):
                stream = previous["stream"].split("/source-", 1)[0]
                break
        comparable = any(
            (target.get("amd64_job") or target.get("amd64_jobs")) and not target.get("comparison_blocker")
            for target in channel["targets"]
        )
        # Run metadata is still collected, but unconfigured comparisons cannot
        # benefit from downloading hundreds of job graphs.
        jobs = attempt_jobs(api, channel["repository"], run) if comparable or historical_success else []
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
            value = evaluate(jobs, observed_target, channel.get("required_steps", []), amd_jobs, context,
                             historical_success=historical_success)
            if not comparable and not historical_success:
                value.update(condition="comparison-withheld", not_applicable=False,
                             detail=target.get("comparison_blocker") or "AMD64 comparison mapping is not configured; run discovered but not eligible for a failure verdict")
            value.update(run_id=run["id"], attempt=run["run_attempt"], sha=run["head_sha"],
                         ref=run["head_branch"], created_at=run["created_at"], observed_at=run["updated_at"],
                         url=run["html_url"], event=run["event"], stream=stream,
                         collection_reason="daily-window" if run["id"] in daily_ids
                         else "active-regression-or-pending-follow-up")
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
    fresh = {"results": results, "observations": observations, "pending_runs": pending,
             "last_complete_scan": stamp(now), "window_hours": 24, "runs_in_window": len(runs),
             "stream_aliases": aliases, "recovery_catchup_streams": sorted(catchup_streams)}
    if retain:
        from monitoring.baselines import retain_health
        return retain_health(api, channel, prior, fresh, now)
    return fresh
