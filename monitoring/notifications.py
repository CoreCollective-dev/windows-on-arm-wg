"""Durable native GitHub incident notifications for existing tracker issues."""

import hashlib
import re

from monitoring.monitor import MonitorError, literal, plan_master


EVENT = "<!-- wheel-monitor:event="
MASTER_EVENT = "<!-- wheel-monitor:master-event="
TRUSTED = {"khmyznikov", "github-actions[bot]"}
OLD_EVENT_GUIDANCE = "The same ongoing regression is not reposted daily. Use `/monitor unsubscribe` in a new comment to opt out."
PACKAGE_GUIDANCE = ("The same ongoing regression is not reposted daily. Use GitHub's Subscribe/Unsubscribe "
                    "button on this package issue to manage notifications.")


def native_event_body(body):
    if OLD_EVENT_GUIDANCE not in body.splitlines():
        return body
    body = "\n".join(PACKAGE_GUIDANCE if line == OLD_EVENT_GUIDANCE else line for line in body.split("\n"))
    return re.sub(
        r"\nParticipants: [^\n]+\n\n(?=A new failure is reported only when Windows Arm64 fails "
        r"and equivalent Windows AMD64 succeeds\.\n)", "\n", body,
    )


def event_pages(package, event, *, tracker_url=None):
    changes = event.get("changes", [])
    if not changes:
        raise MonitorError("Cannot deliver an empty incident event")
    kinds = {change["kind"] for change in changes}
    title = "Recovery" if kinds == {"recovery"} else "Initial tracked regression" if event.get("initial") else "Regression update"
    if tracker_url:
        title = "Newly Broken"
    sections = []
    for change in changes:
        value = change.get("after") or change.get("before") or {}
        lines = [f"- **{literal(change['kind'])}:** `{literal(change['target'])[:300]}`",
                 "  " + literal(value.get("detail", "Platform health transition"))[:700]]
        for platform in ("arm64", "amd64"):
            for job in value.get(platform, {}).get("jobs", []):
                lines.append(f"  - [{platform}: {literal(job['name'])[:200]}]({job['url']})")
        sections.append("\n".join(lines))
    content = []
    page = ""
    for section in sections:
        if len(section) > 40000:
            raise MonitorError("Incident target evidence exceeds safe comment size")
        if len(page) + len(section) > 40000:
            content.append(page)
            page = ""
        page += section + "\n"
    if page:
        content.append(page)
    pages = []
    for index, section in enumerate(content):
        digest = hashlib.sha256((event["key"] + "/" + str(index)).encode()).hexdigest()
        marker = (MASTER_EVENT if tracker_url else EVENT) + digest + " -->"
        body = (f"## {title}: {package}\n\n"
                + (f"[Package tracker]({tracker_url}) - full incident history and current status.\n\n" if tracker_url else "")
                + section + "\n"
                + "A new failure is reported only when Windows Arm64 fails and equivalent Windows AMD64 succeeds.\n"
                + ("The same ongoing regression is not reposted daily. Use GitHub's Subscribe/Unsubscribe "
                   "button on this master issue to manage fleet-wide notifications.\n\n" if tracker_url else
                   PACKAGE_GUIDANCE + "\n\n")
                + marker)
        if len(body) > 60000:
            raise MonitorError("Incident notification exceeds safe comment size")
        pages.append({"marker": marker, "body": body})
    return pages


def post_event(api, issue, event, comments):
    existing = [c for c in comments if c.get("user", {}).get("login") in TRUSTED]
    for page in event["pages"]:
        if any(page["marker"] in (c.get("body") or "") for c in existing):
            continue
        created = api.write("POST", f"issues/{issue['number']}/comments", {"body": page["body"]})
        comments.append(created)
        existing.append(created)


def deliver_master(api, issue, packages, mapping, comments, state, persist):
    if "master_notifications" not in state:
        state["master_notifications"] = {"health": {}, "sequence": 0, "pending_notifications": []}
        plan_master(packages, state["master_notifications"], initial=True)
        persist()
    pending = state["master_notifications"]["pending_notifications"]
    for event in list(pending):
        if "pages" not in event:
            tracker = mapping.get("package=" + event["package"])
            if tracker is None:
                raise MonitorError(f"Master alert package is not enrolled: {event['package']}")
            event["pages"] = event_pages(event["package"], event, tracker_url=tracker["html_url"])
            persist()
        post_event(api, issue, event, comments)
        pending.remove(event)
        persist()


def deliver(api, issue, package, snapshot, comments, state, persist):
    """Persist frozen payloads before posting; markers reconcile ambiguous writes."""
    state.pop("participant_preferences", None)
    if not state.get("issue_notifications_initialized"):
        initial_key = package["package"] + "/initial"
        initial_marker = EVENT + hashlib.sha256((initial_key + "/0").encode()).hexdigest() + " -->"
        already_adopted = any(
            c.get("user", {}).get("login") in TRUSTED and initial_marker in (c.get("body") or "")
            for c in comments
        )
        initial = [
            {"kind": "incident", "target": key, "before": None, "after": value}
            for key, value in snapshot.get("last_known", {}).items()
            if value.get("arm_specific_failure")
        ]
        # Only currently open regressions are announced when adopting monitoring.
        if not already_adopted:
            state["pending_notifications"] = ([{
                "key": initial_key, "initial": True, "changes": initial,
            }] if initial else [])
        state["issue_notifications_initialized"] = True
    pending = state.setdefault("pending_notifications", [])
    persist()
    for event in list(pending):
        if "pages" not in event or "audience" in event:
            event.pop("audience", None)
            event["pages"] = event_pages(package["package"], event)
            persist()
        post_event(api, issue, event, comments)
        pending.remove(event)
        persist()
