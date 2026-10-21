import unittest

from monitoring.monitor import evaluate, report_key, summary
from test_monitor import CHANNEL, job
from test_recent import pair


class HistoricalSuccessTests(unittest.TestCase):
    def test_successful_job_without_retained_steps_can_seed_health(self):
        records = [{**value, "steps": []} for value in pair()]
        result = evaluate(records, CHANNEL["targets"][0], CHANNEL["required_steps"], historical_success=True)
        self.assertEqual(result["health"], "Healthy")
        self.assertEqual(result["evidence_level"], "historical-job-conclusion")
        self.assertFalse(result["arm_specific_failure"])
        live = evaluate(records, CHANNEL["targets"][0], CHANNEL["required_steps"])
        self.assertEqual(live["health"], "Unknown")

    def test_failed_job_with_expired_steps_is_never_invented_failure(self):
        records = [{**value, "steps": []} for value in pair(arm="failure")]
        result = evaluate(records, CHANNEL["targets"][0], CHANNEL["required_steps"], historical_success=True)
        self.assertEqual(result["health"], "Unknown")
        self.assertFalse(result["notify"])

    def test_amd_job_icon_without_stage_evidence_cannot_qualify_live_failure(self):
        records = pair(arm="failure")
        records[1]["steps"] = []
        result = evaluate(records, CHANNEL["targets"][0], CHANNEL["required_steps"], historical_success=True)
        self.assertEqual(result["health"], "Unknown")
        self.assertFalse(result["arm_specific_failure"])

    def test_unpaired_historical_arm_success_is_health_not_alert_coverage(self):
        target = {"id": "arm", "job": CHANNEL["targets"][0]["job"]}
        result = evaluate([job()], target, CHANNEL["required_steps"], historical_success=True)
        self.assertEqual(result["health"], "Healthy")
        self.assertFalse(result["verified_platforms"]["amd64"])
        self.assertTrue(result["coverage_warning"])
        self.assertFalse(result["notify"])
        self.assertEqual(evaluate([job("failure")], target, [], historical_success=True)["health"], "Unknown")

    def test_historical_success_still_needs_verified_windows_arm_architecture(self):
        target = {"job": "^Linux aarch64 build$"}
        result = evaluate([job(name="Linux aarch64 build")], target, [], historical_success=True)
        self.assertEqual(result["health"], "Unknown")

    def test_missing_or_skipped_job_cannot_seed_success(self):
        for records in ([], [job("skipped")]):
            result = evaluate(records, CHANNEL["targets"][0], [], historical_success=True)
            self.assertNotEqual(result["health"], "Healthy")

    def test_earlier_source_sha_mismatch_protection_remains(self):
        records = [{**value, "steps": []} for value in pair()]
        records[1]["head_sha"] = "unrelated"
        result = evaluate(records, CHANNEL["targets"][0], [], historical_success=True)
        self.assertEqual(result["health"], "Unknown")


class ReportGroupingTests(unittest.TestCase):
    def test_requested_status_groups_then_alphabetical_names(self):
        packages = [{"package": name, "health": health, "results": {}, "errors": []}
                    for name, health in [("zebra", "Healthy"), ("beta", "Broken"), ("alpha", "Broken"),
                                         ("paused", "Paused"), ("unknown", "Unknown"), ("aardvark", "Healthy")]]
        expected = ["alpha", "beta", "unknown", "aardvark", "zebra", "paused"]
        self.assertEqual([p["package"] for p in sorted(packages, key=report_key)], expected)
        text = summary({"checked_at": "2026-10-20T00:00:00Z", "packages": packages,
                        "errors": [], "requests": 0, "rate_remaining": 100})
        headings = [text.index("## " + status) for status in ("Broken", "Unknown", "Healthy", "Paused")]
        self.assertEqual(headings, sorted(headings))
        positions = [text.index("| " + name + " |") for name in expected]
        self.assertEqual(positions, sorted(positions))
