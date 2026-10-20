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
from monitoring.pairing import is_amd64, is_arm64

# Keep exception types identical when the CLI loads the recent-run collector.
if __name__ == "__main__":
    sys.modules["monitoring.monitor"] = sys.modules[__name__]


ROOT = Path(__file__).resolve().parent
SCHEMA = 1
FAILURE_POLICY = "windows-arm64-failure-with-successful-amd64-v1"
COLLECTION_POLICY = "all-tracked-runs-last24-including-pr-v1"
REPOSITORY = "CoreCollective-dev/windows-on-arm-wg"
ARTIFACT = "wheel-monitor-report"
HEALTH_ORDER = ("Broken", "Attention", "Unknown", "Healthy", "Paused")


class MonitorError(RuntimeError):
    pass


class APIError(MonitorError):
    def __init__(self, path, status):
        super().__init__(f"GitHub GET {path}: HTTP {status or 'unavailable'}")
        self.status = status


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
        if package.get("setup_health", "Unknown") not in HEALTH_ORDER:
            raise MonitorError(f"Invalid setup health: {name}")
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
            if set(channel["events"]) - {"push", "pull_request", "schedule", "workflow_dispatch",
                                         "repository_dispatch", "release", "merge_group"}:
                raise MonitorError("Pilot excludes PR/unreviewed workflow event types")
            if channel.get("aggregate_job"):
                if channel.get("aggregate_format") != "cibuildwheel-windows-v1":
                    raise MonitorError(f"Unsupported aggregate job evidence format: {name}")
            selectors = [s["ref"] for s in channel["scopes"]]
            if channel.get("aggregate_job"):
                selectors.append(channel["aggregate_job"])
            if channel.get("source_ref"):
                rule = channel["source_ref"]
                if not isinstance(rule, dict) or not all(
                    isinstance(rule.get(field), str) and rule[field] for field in ("pattern", "group", "value")
                ):
                    raise MonitorError(f"Invalid built-source ref applicability rule: {name}")
                selectors.append(rule["pattern"])
            for target in channel["targets"]:
                selectors.append(target["job"])
                if "amd64_jobs" in target and (
                    not isinstance(target["amd64_jobs"], list) or not target["amd64_jobs"]
                    or not all(isinstance(value, str) for value in target["amd64_jobs"])
                ):
                    raise MonitorError(f"AMD64 aggregate must be a nonempty selector list: {name}")
                controls = target.get("amd64_jobs", [target["amd64_job"]] if target.get("amd64_job") else [])
                if target.get("amd64_jobs") and target.get("amd64_job") or len(controls) != len(set(controls)):
                    raise MonitorError(f"Ambiguous AMD64 comparator configuration: {name}")
                for control in controls:
                    selectors.append(control)
                    if target["job"] == control and not target.get("amd64_workflow"):
                        raise MonitorError(f"Identical ARM64/AMD64 selector: {name}")
                if target.get("amd64_workflow") and not re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", target["amd64_workflow"]):
                    raise MonitorError(f"Invalid AMD64 workflow: {name}")
                for field in ("required_steps", "amd64_steps"):
                    selectors.extend(step["pattern"] for step in target.get(field, []))
            selectors.extend(s["pattern"] for s in channel.get("required_steps", []))
            for expression in selectors:
                if not expression.startswith("^") or not expression.endswith("$"):
                    raise MonitorError(f"Unanchored selector: {name}: {expression}")
                try:
                    re.compile(expression)
                except re.error as error:
                    raise MonitorError(f"Invalid regex for {name}: {error}") from error
            if channel.get("source_ref") and channel["source_ref"]["group"] not in re.compile(
                channel["source_ref"]["pattern"]
            ).groupindex:
                raise MonitorError(f"Built-source ref capture group missing: {name}")
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
        self._cache = {}

    def get(self, path, params=None):
        if not re.fullmatch(r"repos/[A-Za-z0-9_.%/-]+", path):
            raise MonitorError(f"Refusing unexpected GitHub API path: {path}")
        if params:
            path += "?" + urlencode(params)
        if path in self._cache:
            return copy.deepcopy(self._cache[path])
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
                    data = json.loads(body)
                    self._cache[path] = data
                    return copy.deepcopy(data)
                except ValueError as error:
                    if attempt == 3:
                        raise MonitorError(f"Invalid GitHub response: {path}") from error
                    self.sleep(2 ** (attempt + 1))
                    continue
            match = re.search(r"HTTP/[\d.]+ (\d+)", head)
            status = int(match[1]) if match else 0
            retry = status in (0, 200, 429, 500, 502, 503, 504) or (
                status == 403 and "retry-after" in headers
            )
            if not retry or attempt == 3:
                raise APIError(path, status)
            delay = max(2 ** (attempt + 1), int(headers.get("retry-after", "0")))
            if delay > 120 or self.remaining == 0:
                raise MonitorError("GitHub rate limit reached; retry on next scheduled poll")
            self.sleep(delay)
        raise MonitorError("GitHub retries exhausted")

    def pages(self, path, key=None, params=None):
        results = []
        page = 1
        expected = None
        while True:
            data = self.get(path, {**(params or {}), "per_page": 100, "page": page})
            items = data[key] if key else data
            if not isinstance(items, list):
                raise MonitorError(f"Unexpected paginated API response: {path}")
            if key and isinstance(data, dict) and expected is None:
                expected = data.get("total_count")
            results.extend(items)
            if expected is not None and len(results) >= expected:
                return results
            if not items and expected is not None and len(results) < expected:
                raise MonitorError(f"Paginated API response is incomplete: {path}")
            if len(items) < 100 and expected is None:
                return results
            page += 1

    def job_log(self, repository, job_id):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not isinstance(job_id, int):
            raise MonitorError("Invalid upstream log identity")
        cache_key = f"job-log:{repository}:{job_id}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        if self.requests >= self.limit or self.package_limit is not None and self.requests >= self.package_limit:
            raise MonitorError("Read budget exhausted before required aggregate evidence")
        self.requests += 1
        with tempfile.TemporaryFile() as output:
            try:
                response = subprocess.run(
                    ["gh", "api", "--allow-escape-sequences", "--method", "GET",
                     f"repos/{repository}/actions/jobs/{job_id}/logs"],
                    stdout=output, stderr=subprocess.PIPE, timeout=90, check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise MonitorError(f"Required per-architecture log unavailable for job {job_id}") from error
            if response.returncode:
                raise MonitorError(f"Required per-architecture log unavailable for job {job_id}")
            output.seek(0)
            content = output.read(10 * 1024 * 1024 + 1)
        if len(content) > 10 * 1024 * 1024:
            raise MonitorError("Required job log exceeds the 10 MiB evidence limit")
        text = content.decode("utf-8", errors="replace")
        self._cache[cache_key] = text
        return text


def discover(api, channel, start, end, filters=None):
    """Split search windows to avoid GitHub's 1,000-result search ceiling."""
    path = f"repos/{channel['repository']}/actions/workflows/{quote(channel['workflow'])}/runs"
    params = {**(filters or {}), "created": f"{stamp(start)}..{stamp(end)}", "per_page": 100, "page": 1}
    first = api.get(path, params)
    if first["total_count"] > 1000:
        if (end - start).total_seconds() <= 1:
            raise MonitorError("Run density exceeds GitHub search ceiling in a one-second window")
        middle = start + timedelta(seconds=int((end - start).total_seconds() // 2))
        runs = discover(api, channel, start, middle, filters) + discover(api, channel, middle, end, filters)
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
    def merge(records):
        groups = {}
        for job in records:
            groups.setdefault(job["name"], []).append(job)
        for name, group in groups.items():
            # Duplicate matrix names cannot safely be correlated across reruns.
            if len(jobs.get(name, [])) > 1:
                jobs[name].extend(group)
            else:
                jobs[name] = group

    # Latest-attempt-only API results omit successful jobs not selected for rerun.
    for attempt in range(1, run["run_attempt"] + 1):
        path = f"repos/{repository}/actions/runs/{run['id']}/attempts/{attempt}/jobs"
        try:
            records = api.pages(path, "jobs")
        except APIError as error:
            if error.status not in (502, 503, 504):
                raise
            print(f"Warning: attempt-specific jobs unavailable for run {run['id']}; "
                  "trying the documented all-attempt job listing", file=sys.stderr)
            records = api.pages(f"repos/{repository}/actions/runs/{run['id']}/jobs", "jobs", {"filter": "all"})
            if any("run_attempt" not in job for job in records):
                raise MonitorError("Fallback job listing lacks attempt identity; refusing merged evidence")
            for job in records:
                if (job["head_sha"] != run["head_sha"] or job["run_attempt"] > run["run_attempt"]
                        or job.get("run_id", run["id"]) != run["id"]):
                    raise MonitorError("Fallback job listing has incompatible SHA/attempt")
            jobs.clear()
            for number in sorted({job["run_attempt"] for job in records}):
                merge([job for job in records if job["run_attempt"] == number])
            return [job for group in jobs.values() for job in group]
        for job in records:
            if job["head_sha"] != run["head_sha"]:
                raise MonitorError(f"Job SHA differs from run {run['id']}; refusing merged evidence")
            if job.get("run_id", run["id"]) != run["id"]:
                raise MonitorError("Job belongs to a different run")
            job.setdefault("run_attempt", attempt)
        merge(records)
    return [job for group in jobs.values() for job in group]


def platform_result(jobs, selector, required_steps):
    matches = [job for job in jobs if selector and re.fullmatch(selector, job["name"])]
    if len(matches) != 1:
        return {"status": "ambiguous" if matches else "missing", "jobs": [],
                "detail": "Expected one platform job, found " + str(len(matches))}
    job = matches[0]
    evidence = {key: job.get(key) for key in ("id", "name", "conclusion", "run_id", "run_attempt", "head_sha")}
    evidence["url"] = job["html_url"]
    result = {"status": "unknown", "jobs": [evidence], "detail": job["name"]}
    result["captures"] = re.fullmatch(selector, job["name"]).groupdict()
    if job.get("status") != "completed" or job.get("conclusion") in ("cancelled", "skipped", "neutral"):
        result["status"] = "skipped" if job.get("conclusion") == "skipped" else "pending"
        return result
    steps = [s for s in job.get("steps", []) if not s["name"].startswith("Post ")]
    result["failed_steps"] = [{"name": step["name"], "conclusion": step["conclusion"]} for step in steps
                              if step.get("conclusion") in ("failure", "timed_out")]
    phases = []
    for requirement in required_steps:
        phases.extend((step, requirement["stage"]) for step in steps
                      if re.fullmatch(requirement["pattern"], step["name"]))
    for step in steps:
        if any(existing is step for existing, _ in phases):
            continue
        name = step["name"]
        if re.search(r"\b(?:test|tests|testing|pytest|smoketest|nox|tox)\b", name, re.I):
            phases.append((step, "test"))
        elif re.search(r"\b(?:build|builds|compile|cibuildwheel|maturin|bdist_wheel)\b|windows-wheel", name, re.I):
            phases.append((step, "build"))
    result["successful_stages"] = sorted({
        stage for step, stage in phases if step.get("conclusion") == "success"
    })
    failed = [(step, stage) for step, stage in phases
              if stage in ("build", "test", "build-test")
              and step.get("conclusion") in ("failure", "timed_out")]
    if failed:
        result.update(status="failed", stage=failed[0][1],
                      detail="Failed build/test step: " + ", ".join(step["name"] for step, _ in failed))
    elif job.get("conclusion") == "success":
        if required_steps and any(
            not any(re.fullmatch(rule["pattern"], step["name"]) and step.get("conclusion") == "success"
                    for step in steps) for rule in required_steps
        ):
            result["detail"] = "Required platform build/test evidence absent or skipped"
        elif phases and any(step.get("conclusion") == "success" for step, _ in phases):
            result["status"] = "passed"
        else:
            result["detail"] = "No successful build/test step evidence available"
    else:
        gating = [(step, stage) for step, stage in phases if stage in ("build", "test", "build-test")]
        incomplete = [(step, stage) for step, stage in gating if step.get("conclusion") != "success"]
        if job.get("conclusion") in ("failure", "timed_out") and incomplete:
            result.update(status="failed", stage=incomplete[0][1],
                          detail="Platform build/test job failed before its required phase completed: " + job["name"])
        else:
            result["detail"] = "Platform job non-success after build/tests, or stage evidence unavailable"
    return result


def baseline_result(jobs, target, required_steps):
    selectors = target.get("amd64_jobs", [target.get("amd64_job")])
    parts = [platform_result(jobs, selector, target.get("amd64_steps", required_steps)) for selector in selectors]
    if len(parts) == 1:
        return parts[0]
    statuses = {part["status"] for part in parts}
    if statuses == {"passed"}:
        status = "passed"
    elif "failed" in statuses:
        status = "failed"
    elif statuses <= {"missing", "skipped"}:
        status = "missing" if statuses == {"missing"} else "skipped"
    else:
        status = "unknown"
    stages = set.intersection(*(set(part.get("successful_stages", [])) for part in parts))
    return {"status": status, "jobs": [job for part in parts for job in part["jobs"]],
            "successful_stages": sorted(stages), "parts": parts,
            "detail": "AMD64 aggregate requires every configured equivalent job to succeed"}


def evaluate(jobs, target, required_steps, amd64_jobs=None, comparison_context=None):
    arm = platform_result(jobs, target["job"], target.get("required_steps", required_steps))
    amd = baseline_result(jobs if amd64_jobs is None else amd64_jobs, target, required_steps)
    result = {"health": "Unknown", "condition": "comparison-unavailable",
              "arm_specific_failure": False, "arm64": arm, "amd64": amd,
              "jobs": arm["jobs"] + amd["jobs"], "notify": False, "policy": FAILURE_POLICY}
    if not target.get("amd64_job") and not target.get("amd64_jobs"):
        result["detail"] = "AMD64 comparator not configured; no Arm-specific failure verdict"
        return result
    if arm["status"] in ("missing", "skipped") and amd["status"] in ("missing", "skipped"):
        activity = [job for job in jobs
                    if (is_arm64(job) or is_amd64(job))
                    and job.get("conclusion") in ("success", "failure", "timed_out")
                    and re.search(r"\b(?:build|test|tests|wheels?|cibuildwheel|pytest|nox|tox|maturin)\b",
                                  job["name"] + " " + " ".join(step["name"] for step in job.get("steps", [])), re.I)]
        if arm["status"] == amd["status"] == "missing" and activity:
            result.update(condition="selector-drift",
                          detail="Windows build/test jobs exist but match neither configured selector; review names/matrix",
                          unmatched_jobs=[job["name"] for job in activity])
        else:
            result.update(condition="not-applicable", not_applicable=True,
                          detail="Neither configured platform built/tested in this run; ignore for regression detection")
        return result
    if target.get("comparison_blocker"):
        result["detail"] = "Comparison withheld: " + target["comparison_blocker"]
        return result
    if target.get("resolved_source_group"):
        group = target["resolved_source_group"]
        source = arm.get("captures", {}).get(group)
        controls = amd.get("parts", [amd])
        if not source or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", source) or any(
            value.get("captures", {}).get(group) != source for value in controls
        ):
            result["detail"] = "Resolved build-source commits are missing or differ"
            return result
        result["resolved_source_sha"] = source
    arm_names = {job["name"] for job in arm["jobs"]}
    amd_names = {job["name"] for job in amd["jobs"]}
    arm_verified = bool(arm_names) and all(is_arm64(job) for job in jobs if job["name"] in arm_names)
    control_data = jobs if amd64_jobs is None else amd64_jobs
    amd_verified = bool(amd_names) and all(is_amd64(job) for job in control_data if job["name"] in amd_names)
    result["verified_platforms"] = {"arm64": arm_verified, "amd64": amd_verified}
    ids = [job["id"] for job in amd["jobs"] if job.get("id") is not None]
    control_names = [job["name"] for job in amd["jobs"]]
    if len(ids) != len(set(ids)) or len(control_names) != len(set(control_names)):
        result["detail"] = "AMD64 aggregate selectors overlap the same job"
        return result
    for b in (amd["jobs"] if arm["jobs"] else []):
        a = arm["jobs"][0]
        cross = bool(target.get("amd64_workflow") and comparison_context
                     and comparison_context.get("arm_run_id") == a.get("run_id")
                     and comparison_context.get("amd64_run_id") == b.get("run_id")
                     and comparison_context.get("source_sha") == a["head_sha"] == b["head_sha"])
        if (a["name"] == b["name"] and not cross) or a.get("id") is not None and a["id"] == b.get("id"):
            result["detail"] = "ARM64 and AMD64 selectors matched the same job"
            return result
        if not a["head_sha"] or a.get("run_id") is None or (
            a["head_sha"] != b["head_sha"] or a.get("run_id") != b.get("run_id") and not cross
        ):
            result["detail"] = "ARM64 and AMD64 evidence is from different runs or SHAs"
            return result
        if cross:
            result["comparison_context"] = comparison_context
    if target.get("interpreter_evidence") and target["interpreter_evidence"] != "cibuildwheel-windows-v1":
        result["detail"] = "Unsupported interpreter-specific evidence"
        return result
    if arm["status"] == "passed" and arm_verified:
        result.update(health="Healthy", condition="pass",
                      detail=f"Monitored Windows Arm64 job passed; AMD64={amd['status']} (not an Arm regression)")
    elif arm["status"] == "failed" and amd["status"] == "passed" and arm_verified and amd_verified:
        stage = arm.get("stage", "build-test")
        successful = set(amd.get("successful_stages", []))
        comparable = stage in successful or "build-test" in successful or (
            stage == "build-test" and {"build", "test"}.issubset(successful)
        )
        if comparable:
            result.update(health="Broken", condition=stage + "-failure",
                          arm_specific_failure=True, notify=True,
                          detail=arm["detail"] + "; equivalent Windows AMD64 build/tests succeeded")
        else:
            result["detail"] = "AMD64 succeeded, but equivalent failed build/test stage is not evidenced"
    else:
        result["detail"] = (f"Windows Arm64={arm['status']}, AMD64={amd['status']}; "
                            "not a confirmed Arm-specific failure")
    return result


def control_jobs(api, channel, target, run):
    workflow = target["amd64_workflow"]
    if workflow == channel["workflow"]:
        raise MonitorError("Cross-workflow comparator must reference a distinct workflow")
    path = f"repos/{channel['repository']}/actions/workflows/{quote(workflow)}/runs"
    candidates = api.pages(path, "workflow_runs", {"head_sha": run["head_sha"], "branch": run["head_branch"]})
    matching = [candidate for candidate in candidates if candidate["head_sha"] == run["head_sha"]
                and candidate["head_branch"] == run["head_branch"]
                and candidate["event"] in target.get("amd64_events", channel["events"])]
    if not matching:
        return [], None
    control = max(matching, key=lambda value: (value["created_at"], value["id"]))
    context = {"arm_run_id": run["id"], "amd64_run_id": control["id"],
               "source_sha": run["head_sha"], "ref": run["head_branch"], "amd64_run_url": control["html_url"]}
    return attempt_jobs(api, channel["repository"], control), context


def observe_channel(api, channel, prior, now):
    if channel.get("collection") == "rolling-24h":
        from monitoring.recent import observe_recent
        return observe_recent(api, channel, prior, now)
    history_start = now - timedelta(days=90)
    refs = []
    for scope in channel["scopes"]:
        text = scope["ref"][1:-1]
        decoded = re.sub(r"\\(.)", r"\1", text)
        if re.escape(decoded) != text:
            refs = []
            break
        refs.append(decoded)
    filters = {"event": channel["events"][0]} if len(channel["events"]) == 1 else {}
    if refs:
        runs = list({run["id"]: run for ref in refs for run in discover(
            api, channel, history_start, now, {**filters, "branch": ref}
        )}.values())
    else:
        runs = discover(api, channel, history_start, now, filters)
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
        run = None
        assessed = {}
        ignored = []
        outside_ref = []
        for candidate in settled:
            jobs = attempt_jobs(api, channel["repository"], candidate)
            if channel.get("source_ref"):
                rule = channel["source_ref"]
                values = [match.group(rule["group"]) for job in jobs
                          if (match := re.fullmatch(rule["pattern"], job["name"]))]
                if values and all(value != rule["value"] for value in values):
                    outside_ref.append(candidate["html_url"])
                    continue
            evaluated = {}
            controls = {}
            for target in channel["targets"]:
                amd_jobs, context = None, None
                if target.get("amd64_workflow"):
                    key = (target["amd64_workflow"], tuple(target.get("amd64_events", channel["events"])))
                    if key not in controls:
                        controls[key] = control_jobs(api, channel, target, candidate)
                    amd_jobs, context = controls[key]
                evaluated[target["id"]] = evaluate(jobs, target, channel.get("required_steps", []), amd_jobs, context)
            if all(value.get("not_applicable") for value in evaluated.values()):
                ignored.append(candidate["html_url"])
                continue
            run, assessed = candidate, evaluated
            break
        for target in channel["targets"]:
            key = f"{scope['id']}/{target['id']}"
            old = prior.get("results", {}).get(key)
            if old and old.get("run_id") is not None and not old.get("created_at"):
                raise MonitorError("Stored run ordering evidence lacks its creation timestamp")
            if old and run and old.get("run_id") is not None and (
                run["created_at"], run["id"]
            ) < (old["created_at"], old["run_id"]):
                results[key] = copy.deepcopy(old)
                continue
            if not run:
                result = copy.deepcopy(old) if old else {
                    "health": "Unknown", "condition": "no-observation",
                    "detail": "No settled eligible run in the 90-day pilot horizon", "jobs": [],
                }
            else:
                result = assessed[target["id"]]
                result.update(
                    run_id=run["id"], attempt=run["run_attempt"], sha=run["head_sha"],
                    ref=run["head_branch"], created_at=run["created_at"],
                    observed_at=run["updated_at"], url=run["html_url"],
                )
            if ignored:
                result["ignored_runs"] = ignored
                result["warning"] = f"Ignored {len(ignored)} runs where neither platform built/tested"
            if outside_ref:
                result["out_of_scope_runs"] = outside_ref
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
            continue
        elif (
            (old or {}).get("health") != new["health"]
            or (old or {}).get("condition") != new["condition"]
            or (old or {}).get("quality") != new.get("quality")
        ):
            if new["health"] == "Healthy":
                if old and old.get("arm_specific_failure") and new.get("arm64", {}).get("status") == "passed":
                    changes.append({"target": key, "kind": "recovery", "before": old, "after": new})
            elif new.get("arm_specific_failure"):
                changes.append({"target": key, "kind": "incident", "before": old, "after": new})
    if not changes:
        return None
    return {"sequence": sequence + 1, "changes": changes}


def scan(api, config, state, now, scheduled=False, progress=False):
    validate(config)
    if state.get("schema") != SCHEMA or state.get("tracker") != config["tracker"]:
        raise MonitorError("State schema/tracker mismatch; explicit reviewed reset required")
    packages = copy.deepcopy(state.get("packages", {}))
    recent = config.get("collection_policy") == COLLECTION_POLICY
    migrated = bool(packages) and (
        state.get("failure_policy") != FAILURE_POLICY
        or recent and state.get("collection_policy") != COLLECTION_POLICY
    )
    if migrated:
        for package in packages.values():
            package.pop("results", None)
            package.pop("notification_results", None)
            for channel in package.get("channels", {}).values():
                channel.pop("results", None)
    report = {"schema": SCHEMA, "mode": "dry-run", "tracker": config["tracker"],
              "checked_at": stamp(now), "packages": [], "errors": [],
              "failure_policy": FAILURE_POLICY, "state_policy_migrated": migrated,
              "collection_policy": config.get("collection_policy"), "window_hours": 24 if recent else None}
    revision = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    report["registry_revision"] = revision
    for package in config["packages"]:
        name = package["package"]
        old = packages.get(name, {})
        entry = copy.deepcopy(old)
        entry.setdefault("channels", {})
        results = {}
        observations = []
        errors = []
        if not package.get("channels"):
            results["setup"] = {"health": package.get("setup_health", "Unknown"), "condition": "setup",
                                "detail": package["setup_reason"], "jobs": []}
        before_requests = api.requests
        package_budget = 320 if recent else 160
        api.package_limit = before_requests + package_budget
        for channel in package.get("channels", []):
            identifier = channel["id"]
            prior = old.get("channels", {}).get(identifier, {})
            try:
                if api.requests - before_requests >= package_budget:
                    raise MonitorError("Package read budget exhausted; coverage incomplete")
                fresh = observe_channel(api, channel, prior, now)
                entry["channels"][identifier] = fresh
                for key, value in fresh["results"].items():
                    results[f"{identifier}/{key}"] = value
                observations.extend({"key": f"{identifier}/{observation['key']}", "value": observation["value"]}
                                    for observation in fresh.get("observations", []))
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
            if recent:
                continue
            if key == "setup" or key.endswith("/observation-gap"):
                continue
            removed = copy.deepcopy(old["results"][key])
            if removed["health"] != "Broken":
                removed["health"] = "Attention"
            removed["condition"] = "setup-change"
            removed["warning"] = "Previously monitored target removed; reviewed retirement required"
            results[key] = removed
        entry["results"] = results
        entry["gap_polls"] = (old.get("gap_polls", 0) + int(scheduled)) if errors else 0
        announced = copy.deepcopy(old.get("notification_results", {}))
        if recent:
            changes = []
            seen = copy.deepcopy(old.get("seen_observations", {}))
            for observation in observations:
                key, value = observation["key"], observation["value"]
                identity = f"{key}/{value['run_id']}/{value['attempt']}"
                fingerprint = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
                if seen.get(identity, {}).get("fingerprint") == fingerprint:
                    continue
                previous = announced.get(key)
                older = previous and (value["created_at"], value["run_id"]) < (
                    previous["created_at"], previous["run_id"]
                )
                update = None if older else plan({key: previous} if previous else {}, {key: value}, 0)
                if update:
                    changes.extend(update["changes"])
                if not older and (value.get("arm_specific_failure") or value.get("arm64", {}).get("status") == "passed"):
                    announced[key] = copy.deepcopy(value)
                seen[identity] = {"fingerprint": fingerprint, "observed_at": value["observed_at"]}
            entry["seen_observations"] = {
                key: value for key, value in seen.items()
                if now - instant(value["observed_at"]) <= timedelta(days=90)
            }
            event = {"sequence": old.get("sequence", 0) + 1, "changes": changes} if changes else None
        else:
            event = plan(announced, results, old.get("sequence", 0))
        # An outage is operationally visible immediately, but not a participant ping.
        if errors and entry["gap_polls"] < 2:
            event = None
        if event:
            entry["sequence"] = event["sequence"]
            event["key"] = f"{name}/{event['sequence']}"
        if not errors or entry["gap_polls"] >= 2:
            if not recent:
                for key, result in results.items():
                    if result.get("arm_specific_failure") or result.get("arm64", {}).get("status") == "passed":
                        announced[key] = copy.deepcopy(result)
            if recent:
                announced = {key: value for key, value in announced.items()
                             if value.get("observed_at") and now - instant(value["observed_at"]) <= timedelta(days=90)}
            entry["notification_results"] = announced
        health = min((r["health"] for r in results.values()), key=HEALTH_ORDER.index)
        entry["health"] = health
        packages[name] = entry
        report["packages"].append({"package": name, "health": health, "owner": package["owner"],
                                   "results": results, "observations": observations,
                                   "planned_notification": event, "errors": errors})
        report["errors"].extend(f"{name}: {error}" for error in errors)
        if progress:
            print(f"{name}: {health}; requests={api.requests}; errors={len(errors)}", flush=True)
    output = {"schema": SCHEMA, "tracker": config["tracker"], "packages": packages,
              "checked_at": stamp(now), "registry_revision": revision, "failure_policy": FAILURE_POLICY,
              "collection_policy": config.get("collection_policy")}
    report["requests"] = api.requests
    api.package_limit = None
    report["rate_remaining"] = getattr(api, "remaining", None)
    return report, output


def summary(report):
    lines = ["# Windows Arm64 wheel monitor: read-only pilot", "",
             f"Checked: {report['checked_at']}. Polling: every 24 hours.",
             "Failure rule: Windows Arm64 build/tests failed AND equivalent Windows AMD64 succeeded.",
             "No issues, comments, mentions, labels or Project items were changed.", "",
             "| Package | Health | Observation | Planned transition |",
             "| --- | --- | --- | --- |"]
    for package in report["packages"]:
        event = package["planned_notification"]
        descriptions = []
        for key, result in package["results"].items():
            evidence = f"[run]({result['url']})" if result.get("url") else "no run"
            descriptions.append(f"{literal(key)}: {literal(result['detail'])} ({evidence})")
            for platform in ("arm64", "amd64"):
                for job in result.get(platform, {}).get("jobs", []):
                    descriptions.append(f"[{platform}: {literal(job['name'])}]({job['url']})")
            if result.get("warning"):
                descriptions.append("Warning: " + literal(result["warning"]))
        lines.append(f"| {package['package']} | {package['health']} | "
                     f"{'<br>'.join(descriptions)} | {len(event['changes']) if event else 0} |")
    failures = [(package["package"], observation["value"]) for package in report["packages"]
                for observation in package.get("observations", [])
                if observation["value"].get("arm_specific_failure")]
    if failures:
        lines.extend(["", "## All qualifying failures observed in the window",
                      "Includes runs later fixed by another run; latest stream health is shown above."])
        lines.extend(f"- {name}: [{literal(value['stream'])}]({value['url']})"
                     for name, value in failures)
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
    parser.add_argument("--registry", type=Path, default=ROOT / "fleet.json")
    parser.add_argument("--state", type=Path)
    parser.add_argument("--output", type=Path, default=Path("monitor-output"))
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    config = validate(read_json(args.registry))
    api = GitHub(budget=1200 if config.get("collection_policy") == COLLECTION_POLICY else 800)
    if args.restore:
        if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
            raise MonitorError("Artifact restore is only supported in the tracker repository")
        restored = restore(api, args.output, int(os.environ["GITHUB_RUN_ID"]))
        print(f"Restored monitor state: {restored or 'first execution'}")
        args.state = args.output / "state.json" if restored else None
    state = read_json(args.state) if args.state else {"schema": SCHEMA, "tracker": REPOSITORY, "packages": {}}
    report, output = scan(api, config, state, utcnow(), args.scheduled, progress=True)
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
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        sys.exit(main())
    except MonitorError as error:
        print(f"Monitor error: {error}", file=sys.stderr)
        sys.exit(1)
