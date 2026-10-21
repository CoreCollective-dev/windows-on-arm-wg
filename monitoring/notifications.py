"""Durable, participant-aware incident delivery for existing tracker issues."""

import copy
import hashlib
import re

from monitoring.monitor import MonitorError, literal


EVENT = "<!-- wheel-monitor:event="
TRUSTED = {"khmyznikov", "github-actions[bot]"}


def human(user):
    return bool(user and user.get("login") and user.get("type") != "Bot"
                and not user["login"].endswith("[bot]") and user["login"] not in ("ghost", "github-actions"))


def participants(issue, comments, preferences, owner):
    preferences = copy.deepcopy(preferences)
    people = {}
    for user in [issue.get("user"), *issue.get("assignees", []), *[c.get("user") for c in comments]]:
        if human(user):
            people[str(user.get("id") or user["login"].lower())] = user["login"]
    for comment in sorted(comments, key=lambda value: value["id"]):
        user = comment.get("user")
        if not human(user):
            continue
        command = re.fullmatch(r"/monitor (unsubscribe|subscribe)", (comment.get("body") or "").strip())
        if not command:
            continue
        identifier = str(user.get("id") or user["login"].lower())
        if comment["id"] > preferences.get(identifier, {}).get("comment_id", -1):
            preferences[identifier] = {
                "login": user["login"], "comment_id": comment["id"], "opted_out": command[1] == "unsubscribe",
            }
    audience = sorted({login for identifier, login in people.items()
                       if not preferences.get(identifier, {}).get("opted_out")}, key=str.lower)
    opted_out = {value["login"].lower() for value in preferences.values() if value["opted_out"]}
    if not audience and not people and owner.lower() not in opted_out:
        audience = [owner]
    return audience, preferences


def event_pages(package, event, audience):
    changes = event.get("changes", [])
    if not changes:
        raise MonitorError("Cannot deliver an empty incident event")
    kinds = {change["kind"] for change in changes}
    title = "Recovery" if kinds == {"recovery"} else "Initial tracked regression" if event.get("initial") else "Regression update"
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
    mention_pages = [audience[i:i + 40] for i in range(0, len(audience), 40)] or [[]]
    count = max(len(content), len(mention_pages))
    pages = []
    for index in range(count):
        digest = hashlib.sha256((event["key"] + "/" + str(index)).encode()).hexdigest()
        marker = EVENT + digest + " -->"
        mentions = " ".join("@" + login for login in mention_pages[index]) if index < len(mention_pages) else ""
        body = (f"## {title}: {package}\n\n"
                + (content[index] if index < len(content) else "Participant notification continuation.\n")
                + "\n" + ("Participants: " + mentions + "\n\n" if mentions else "")
                + "A new failure is reported only when Windows Arm64 fails and equivalent Windows AMD64 succeeds.\n"
                + "The same ongoing regression is not reposted daily. Use `/monitor unsubscribe` in a new comment to opt out.\n\n"
                + marker)
        if len(body) > 60000:
            raise MonitorError("Incident notification exceeds safe comment size")
        pages.append({"marker": marker, "body": body})
    return pages


def deliver(api, issue, package, snapshot, comments, state, persist):
    """Persist frozen payloads before posting; markers reconcile ambiguous writes."""
    preference_audience, preferences = participants(
        issue, comments, state.get("participant_preferences", {}), package["owner"],
    )
    state["participant_preferences"] = preferences
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
        if "pages" not in event:
            event["audience"] = preference_audience
            event["pages"] = event_pages(package["package"], event, preference_audience)
            persist()
        # An opt-out arriving after a partial delivery still applies to unsent chunks.
        opted_out = {value["login"] for value in preferences.values() if value["opted_out"]}
        for page in event["pages"]:
            for login in opted_out:
                page["body"] = re.sub(r"(?<!\w)@" + re.escape(login) + r"(?![A-Za-z0-9-])",
                                      login, page["body"], flags=re.I)
        persist()
        existing = [c for c in comments if c.get("user", {}).get("login") in TRUSTED]
        for page in event["pages"]:
            if any(page["marker"] in (c.get("body") or "") for c in existing):
                continue
            created = api.write("POST", f"issues/{issue['number']}/comments", {"body": page["body"]})
            comments.append(created)
            existing.append(created)
        pending.remove(event)
        persist()
