"""Read explicit cibuildwheel interpreter outcomes inside an aggregate job."""

import re


ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
IDENTIFIER = r"cp[0-9]+t?-win_(?:arm64|amd64)"


def cibuildwheel_jobs(job, text):
    active = None
    records = {}
    for raw in text.splitlines():
        line = ANSI.sub("", raw)
        line = re.sub(r"^\d{4}-\d\d-\d\dT\S+Z\s?", "", line).strip()
        line = re.sub(r"^##\[(?:error|warning)\]", "", line)
        start = re.fullmatch(r"Building (" + IDENTIFIER + r") wheel", line)
        if start:
            active = start[1]
            records[active] = {"status": "pending"}
            continue
        if not active:
            continue
        record = records[active]
        if re.fullmatch("\u2713 " + re.escape(active) + r" finished in .+", line):
            record["status"] = "success"
            active = None
        elif re.match(r"(?:cibuildwheel: error:|Error: Command|subprocess\.CalledProcessError:)", line):
            record["status"] = "failure"
        if "cannot be tested" in line or "test skipped" in line.lower():
            record["tests_skipped"] = True
    output = []
    for identifier, outcome in records.items():
        # Incomplete/unsupported output is never promoted from the parent job icon.
        conclusion = outcome["status"] if outcome["status"] != "pending" else None
        output.append({
            **job,
            "id": f"{job['id']}:{identifier}",
            "physical_job_id": job["id"],
            "name": "cibuildwheel " + identifier,
            "status": "completed" if conclusion else "pending",
            "conclusion": conclusion,
            "labels": ["windows-11-arm" if identifier.endswith("arm64") else "windows-2022"],
            "steps": [{"name": "cibuildwheel build", "conclusion": conclusion}],
            "architecture_evidence": "Explicit interpreter block and completion/error marker in aggregate Windows job log",
            "test_coverage": "not claimed; ARM cross-compiled wheels cannot run on the AMD64 host",
        })
    return output
