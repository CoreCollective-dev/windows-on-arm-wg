import unittest
from monitoring.monitor import COLLECTION_POLICY, ROOT, read_json, validate


class IssueDeploymentTests(unittest.TestCase):
    def test_upstream_collector_is_read_only_and_issue_writer_cannot_bootstrap(self):
        text = (ROOT.parent / ".github" / "workflows" / "wheel-monitor.yml").read_text(encoding="utf-8")
        self.assertIn("contents: read", text)
        self.assertIn("actions: read", text)
        self.assertIn("issues: write", text)
        self.assertIn("python -m monitoring.issues --apply", text)
        self.assertIn("--mapping monitoring/tracking.json", text)
        self.assertIn("--state monitor-output/state.json", text)
        self.assertNotIn("--bootstrap", text)
        self.assertNotIn("contents: write", text)
        self.assertIn("cron: '17 15 * * *'", text)
        self.assertIn("python -m monitoring.monitor --restore", text)

    def test_default_deployment_is_the_entire_reviewed_fleet(self):
        config = validate(read_json(ROOT / "fleet.json"))
        self.assertEqual(len(config["packages"]), 84)
        self.assertEqual(config["collection_policy"], COLLECTION_POLICY)
        for package in config["packages"]:
            for channel in package["channels"]:
                self.assertEqual(channel["collection"], "rolling-24h")
                self.assertIn("pull_request", channel["events"])

    def test_live_mapping_excludes_all_eight_paused_packages(self):
        mapping = read_json(ROOT / "tracking.json")
        self.assertEqual(mapping["master"], 7)
        self.assertEqual(len(mapping["packages"]), 76)
        paused = {"bcrypt", "faiss-cpu", "google-re2", "gymnasium", "pymupdf", "pynacl", "srsly", "statsmodels"}
        self.assertFalse(paused.intersection(mapping["packages"]))


if __name__ == "__main__":
    unittest.main()
