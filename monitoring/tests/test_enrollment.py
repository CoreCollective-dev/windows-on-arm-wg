import unittest

from monitoring.enroll import eligible, windows_arm_wheel, workflow_sources
from test_monitor import NOW, job, run


class EnrollmentTests(unittest.TestCase):
    def test_windows_wheel_identified(self):
        self.assertTrue(windows_arm_wheel(job(name="Build cp314-win_arm64")))

    def test_macos_not_windows(self):
        self.assertFalse(windows_arm_wheel(job(name="Build cp314-macosx_arm64")))

    def test_ambiguous_arm64_not_promoted(self):
        self.assertFalse(windows_arm_wheel(job(name="3.14 arm64")))

    def test_windows_nonwheel_not_promoted(self):
        self.assertFalse(windows_arm_wheel(job(name="Windows ARM64 lint", steps=[{"name": "Lint"}])))

    def test_expired_steps_not_promoted(self):
        self.assertFalse(windows_arm_wheel(job(name="Build cp314-win_arm64", steps=[])))

    def test_reusable_template_not_treated_as_caller(self):
        package = {
            "repositories": ["https://github.com/explosion/cymem"],
            "builder_evidence": [
                "https://github.com/explosion/cymem/blob/master/.github/workflows/cibuildwheel.yml#L15",
                "https://github.com/explosion/gha-cibuildwheel/blob/abcd/.github/workflows/cibuildwheel.yml#L209",
            ],
        }
        self.assertEqual(workflow_sources(package), [("explosion/cymem", "cibuildwheel.yml")])

    def test_pr_is_eligible_for_read_only_inspection(self):
        self.assertTrue(eligible(run(event="pull_request"), "main", NOW))

    def test_feature_push_is_eligible_for_read_only_inspection(self):
        self.assertTrue(eligible(run(branch="feature"), "main", NOW))

    def test_release_ref_can_be_sampled(self):
        self.assertTrue(eligible(run(branch="v1.2.3"), "main", NOW))

    def test_explosion_release_prefix_is_not_lost(self):
        self.assertTrue(eligible(run(branch="release-v3.0.13"), "main", NOW))
        self.assertTrue(eligible(run(branch="prerelease-v3.0.14rc1"), "main", NOW))

    def test_old_run_is_not_current_evidence(self):
        self.assertFalse(eligible(run(age=2500), "main", NOW))

    def test_window_is_24_hours_not_90_days(self):
        self.assertFalse(eligible(run(age=26), "main", NOW))
