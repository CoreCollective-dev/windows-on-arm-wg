"""Retain verified health independently from daily monitoring activity."""

import copy
import hashlib
import json
from datetime import timedelta
from urllib.parse import quote

from monitoring.monitor import APIError, HEALTH_ORDER, MonitorError, instant, stamp


BASELINE_VERSION = 2


def decisive(value):
    return bool(value.get("arm_specific_failure") or (
        value.get("health") == "Healthy" and value.get("arm64", {}).get("status") == "passed"
        and value.get("verified_platforms", {}).get("arm64")
    ))


def order(value):
    return value["created_at"], value["run_id"], value["attempt"]


def definition(channel):
    fields = ("repository", "workflow", "targets", "required_steps", "source_ref", "aggregate_job",
              "aggregate_format", "comparison_exception")
    return hashlib.sha256(json.dumps({key: channel.get(key) for key in fields}, sort_keys=True).encode()).hexdigest()


def initialization_definition(channel):
    return {key: channel.get(key) for key in ("baseline_run_id", "baseline_search_days", "baseline_search_limit")}


def incorporate(known, observations):
    for observation in observations:
        key, value = observation["key"], observation["value"]
        if decisive(value) and (key not in known or order(value) >= order(known[key])):
            known[key] = copy.deepcopy(value)


def bootstrap(api, channel, now):
    """Inspect bounded newest history without filtering for success."""
    from monitoring.recent import observe_recent
    seed_id = channel.get("baseline_run_id")
    seed, seed_warnings = None, []
    if seed_id:
        try:
            seed = api.get(f"repos/{channel['repository']}/actions/runs/{seed_id}")
        except APIError as error:
            if error.status != 404:
                raise
            seed_warnings.append(f"Reviewed seed run {seed_id} is no longer accessible; using historical discovery instead")
    if seed and seed.get("path", "").split("@")[0] != ".github/workflows/" + channel["workflow"]:
        raise MonitorError("Configured baseline run belongs to a different workflow")
    days = channel.get("baseline_search_days", 90)
    limit = channel.get("baseline_search_limit", 8)
    path = f"repos/{channel['repository']}/actions/workflows/{quote(channel['workflow'])}/runs"
    parameters = {"created": f"{stamp(now - timedelta(days=days))}..{stamp(now)}",
                  "per_page": min(limit + 1, 100), "page": 1}
    if seed:
        parameters["branch"] = seed["head_branch"]
        parameters["event"] = seed["event"]
    data = api.get(path, parameters)
    candidates = list(data["workflow_runs"])
    if seed:
        origin = seed.get("head_repository") or {}
        candidates = [run for run in candidates if (
            (run.get("head_repository") or {}).get("id") == origin.get("id")
            and run["event"] == seed["event"]
        )]
    candidates.sort(key=lambda run: (run["created_at"], run["id"]), reverse=True)
    candidates = candidates[:limit]
    if seed and not any(run["id"] == seed["id"] for run in candidates):
        candidates.append(seed)
    fresh = observe_recent(api, channel, {}, now, runs=candidates, retain=False)
    warnings = seed_warnings + ["Historical candidate contains inconclusive platform evidence: " + item["value"]["url"]
                for item in fresh["observations"] if not decisive(item["value"])]
    return fresh["observations"], {
        "version": BASELINE_VERSION, "checked_at": stamp(now),
        "candidate_runs": [run["id"] for run in candidates], "warnings": sorted(set(warnings)),
        "pending_runs": fresh["pending_runs"],
        "search_days": days, "search_limit": limit, "search_limited": data.get("total_count", 0) > limit,
    }


def retire_closed_prs(api, channel, known, prior_retired, now):
    retired = copy.deepcopy(prior_retired)
    checks = {}
    for key, value in list(known.items()):
        parts = value.get("stream", "").split("/")
        if len(parts) < 3 or parts[0] != "pr" or not parts[2].isdigit():
            continue
        number = int(parts[2])
        if number not in checks:
            checks[number] = api.get(f"repos/{channel['repository']}/pulls/{number}")
        pull = checks[number]
        if pull.get("state") not in ("open", "closed"):
            raise MonitorError(f"Invalid PR lifecycle response for {number}")
        if pull["state"] == "closed":
            archived = copy.deepcopy(value)
            archived["retired_at"] = stamp(now)
            archived["retirement"] = "Merged PR; historical result retained" if pull.get("merged_at") else "Closed without merge; not a recovery"
            retired[key] = archived
            del known[key]
        else:
            retired.pop(key, None)
    return retired


def retain_health(api, channel, prior, fresh, now):
    signature = definition(channel)
    compatible = prior.get("baseline_definition") in (None, signature)
    known = copy.deepcopy(prior.get("last_known", {})) if compatible else {}
    if compatible and not prior.get("baseline_version"):
        incorporate(known, [{"key": key, "value": value} for key, value in prior.get("results", {}).items()
                            if decisive(value)])
    incorporate(known, fresh["observations"])
    metadata = copy.deepcopy(prior.get("baseline", {})) if compatible else {}
    errors = []
    initialized = (compatible and prior.get("baseline_version") == BASELINE_VERSION
                   and prior.get("baseline_initialization") == initialization_definition(channel))
    comparable = any((target.get("amd64_job") or target.get("amd64_jobs"))
                     and not target.get("comparison_blocker") for target in channel["targets"])
    expected = {target["id"] for target in channel["targets"]}
    established = {key.rsplit("/", 1)[-1] for key in known}
    if not initialized and comparable and channel.get("baseline_search", True):
        if not expected.issubset(established) or prior.get("refresh_baseline") or (
            prior.get("baseline_version") and not initialized
        ):
            try:
                historical, metadata = bootstrap(api, channel, now)
                incorporate(known, historical)
                incorporate(known, fresh["observations"])
                fresh["pending_runs"] = sorted(set(fresh["pending_runs"] + metadata.get("pending_runs", [])))
                initialized = True
            except MonitorError as error:
                errors.append("Baseline initialization: " + str(error))
        else:
            initialized = True
    elif not comparable or not channel.get("baseline_search", True):
        initialized = True
    retired = copy.deepcopy(prior.get("retired", {})) if compatible else {}
    retired_before = set(retired)
    try:
        retired = retire_closed_prs(api, channel, known, retired, now)
    except MonitorError as error:
        errors.append("PR lifecycle check: " + str(error))
    inconclusive = any(not decisive(item["value"]) for item in fresh["observations"])
    if errors:
        activity = "Monitoring warning"
    elif set(retired) - retired_before:
        activity = "PR closed - tracking retired, not a recovery"
    elif inconclusive:
        activity = "Comparison inconclusive"
    elif any(item["value"].get("arm_specific_failure") for item in fresh["observations"]):
        activity = "Arm-specific failure observed"
    elif fresh["observations"]:
        activity = "Passing builds observed"
    elif fresh["pending_runs"]:
        activity = "Builds pending"
    else:
        activity = "No changes - no new applicable builds"
    covered = {key.rsplit("/", 1)[-1] for key in known}
    missing = ["No validated baseline for target " + target for target in sorted(expected - covered)]
    health = min((value["health"] for value in known.values()), key=HEALTH_ORDER.index) if known else "Unknown"
    if not known and retired and expected.issubset({key.rsplit("/", 1)[-1] for key in retired}) and not any(
        item["key"] not in retired for item in fresh["observations"]
    ):
        health, missing = "Paused", []
    if missing and health != "Broken":
        health = "Unknown"
    fresh.update(
        last_known=known, retired=retired, baseline_definition=signature, baseline=metadata,
        baseline_initialization=initialization_definition(channel),
        baseline_version=BASELINE_VERSION if initialized else None,
        health=health, activity=activity, baseline_warnings=missing, errors=errors,
        last_verified_at=max((value["observed_at"] for value in known.values()), default=None),
        history_warnings=metadata.get("warnings", []),
    )
    return fresh
