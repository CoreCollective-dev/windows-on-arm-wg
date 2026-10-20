"""Conservative discovery of equivalent Windows job-name platform variants."""

import re


def is_windows(job):
    name = job["name"].lower()
    labels = " ".join(job.get("labels", [])).lower()
    steps = " ".join(step["name"] for step in job.get("steps", [])).lower()
    if re.search(r"\b(?:macos|darwin|linux|manylinux|musllinux)\b", name):
        return False
    return bool(re.search(r"windows|win[_ -](?:arm64|amd64)|pc-windows", name + " " + labels)
                or "actions/windows-wheel" in steps or "actions/windows-tests" in steps)


def is_amd64(job):
    if not is_windows(job):
        return False
    name = job["name"].lower()
    if re.search(r"arm64|aarch64|\barm\b|win32|auto32|\bi686\b|\bx86\b(?![-_]64)", name):
        return False
    if re.search(r"amd64|x86[-_]64|\bx64\b|\bwin[- ]?64\b", name):
        return True
    text = name + " " + " ".join(job.get("labels", [])).lower()
    if re.search(r"arm64|aarch64|windows-11-arm|\barm\b", text):
        return False
    return bool(re.search(r"amd64|x86[-_]64|\bx64\b|\bwin64\b|64-bit|auto64|windows-latest|windows-(?:2022|2025)", text))


def is_arm64(job):
    if not is_windows(job) or re.search(r"arm64ec|arm64x", job["name"], re.I):
        return False
    if re.search(r"amd64|x86[-_]64|\bx64\b|\bwin[- ]?64\b|win32|\bx86\b", job["name"], re.I):
        return False
    return bool(re.search(r"arm64|aarch64|windows-11-arm",
                          job["name"] + " " + " ".join(job.get("labels", [])), re.I))


def platform_key(name):
    value = name.lower()
    value = re.sub(r"(?:aarch64|x86_64)-pc-windows-msvc", "windows", value)
    value = re.sub(r"win[_ -](?:arm64|amd64|x86_64|x64)", "windows", value)
    value = re.sub(r"\bwin64\b", "windows", value)
    value = re.sub(r"windows[- ](?:11-arm|latest|2022|2025)", "windows", value)
    value = re.sub(r"\b(?:arm64|aarch64|amd64|x86[-_]64|x64|auto64|arm)\b", "", value)
    value = re.sub(r"\bwindows\b", "", value)
    value = re.sub(r"[\s,()/_-]+", " ", value)
    return value.strip()


def comparator(job, jobs):
    candidates = [other for other in jobs
                  if other["name"] != job["name"] and is_amd64(other)
                  and platform_key(other["name"]) == platform_key(job["name"])]
    if len(candidates) != 1:
        return None
    return candidates[0]
