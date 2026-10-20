import unittest
from monitoring.monitor import COLLECTION_POLICY, ROOT, read_json, validate


class ReadOnlyDeploymentTests(unittest.TestCase):
    def test_observer_workflow_cannot_write_issues(self):
        text = (ROOT.parent / ".github" / "workflows" / "wheel-monitor.yml").read_text(encoding="utf-8")
        self.assertIn("contents: read", text)
        self.assertIn("actions: read", text)
        self.assertNotRegex(text, r"(?m)^\s+[a-z-]+:\s*write\s*$")
        self.assertNotIn("monitoring.issues", text)
        self.assertNotIn("issue-status:", text)
        self.assertNotIn("--apply", text)
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


if __name__ == "__main__":
    unittest.main()
