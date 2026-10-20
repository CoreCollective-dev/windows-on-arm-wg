import copy
import re
import unittest
from monitoring.monitor import ROOT, evaluate, read_json, validate
from test_monitor import job


class NumbaApprovalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = validate(read_json(ROOT / "fleet.json"))
        cls.package = next(p for p in cls.config["packages"] if p["package"] == "numba")
        cls.channel = cls.package["channels"][0]

    def pair(self, target, arm="failure", amd="success", same_sha=True):
        def literal(pattern):
            value = re.sub(r"\\(.)", r"\1", pattern[1:-1])
            self.assertIsNotNone(re.fullmatch(pattern, value))
            return value

        arm_job = {**job(arm, name=literal(target["job"])), "id": 100,
                   "labels": ["windows-11-arm"],
                   "steps": [{"name": "Validate and test wheel" if target["id"].startswith("test-") else "Build wheel",
                              "conclusion": arm}]}
        amd_job = {**job(amd, name=literal(target["amd64_job"])), "id": 200, "run_id": 20,
                   "head_sha": "abc" if same_sha else "other", "labels": ["windows-2025"],
                   "steps": [{"name": arm_job["steps"][0]["name"], "conclusion": amd}]}
        context = {"arm_run_id": 10, "amd64_run_id": 20, "source_sha": "abc", "ref": "feature"}
        return evaluate([arm_job], target, [], [amd_job], context)

    def test_approval_is_scoped_and_all_four_configured_pairs_are_enabled(self):
        self.assertEqual(self.package["comparison_status"], "paired")
        self.assertEqual(len(self.channel["targets"]), 4)
        approval = self.channel["comparison_exception"]
        self.assertEqual(approval["dependency"], "numpy")
        self.assertEqual(approval["approved_on"], "2026-10-20")
        for target in self.channel["targets"]:
            self.assertNotIn("comparison_blocker", target)
        for package in self.config["packages"]:
            if package["package"] != "numba":
                self.assertTrue(all(c.get("comparison_exception", {}).get("dependency") != "numpy"
                                    for c in package["channels"]))

    def test_mismatched_numpy_does_not_block_real_arm_specific_failures(self):
        for target in self.channel["targets"]:
            with self.subTest(target=target["id"]):
                self.assertTrue(self.pair(target)["arm_specific_failure"])

    def test_amd_failure_or_skip_still_suppresses_arm_specific_failure(self):
        for target in self.channel["targets"]:
            for conclusion in ("failure", "skipped", "cancelled"):
                with self.subTest(target=target["id"], amd=conclusion):
                    self.assertFalse(self.pair(target, amd=conclusion)["arm_specific_failure"])

    def test_arm_pass_with_amd_failure_still_is_not_a_failure(self):
        for target in self.channel["targets"]:
            self.assertFalse(self.pair(target, arm="success", amd="failure")["arm_specific_failure"])

    def test_source_mismatch_remains_blocked(self):
        for target in self.channel["targets"]:
            self.assertFalse(self.pair(target, same_sha=False)["arm_specific_failure"])

    def test_other_comparison_blockers_are_not_bypassed(self):
        target = copy.deepcopy(self.channel["targets"][0])
        target["comparison_blocker"] = "Unverified non-NumPy dependency"
        self.assertFalse(self.pair(target)["arm_specific_failure"])
