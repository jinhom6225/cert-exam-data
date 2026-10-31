"""Repository settings the collector relies on (security review)."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = (ROOT / ".github" / "workflows" / "certexam_collect.yml").read_text(encoding="utf-8")


def step(name):
    match = re.search(r"- name: " + re.escape(name) + r"\n(.*?)(?=\n      - name: |\Z)", WORKFLOW, re.S)
    assert match, name
    return match.group(1)


class GitignoreTest(unittest.TestCase):
    def test_python_caches_are_ignored(self):
        lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("__pycache__/", lines)
        self.assertIn("*.pyc", lines)


class WorkflowTest(unittest.TestCase):
    def test_least_privilege_permissions(self):
        self.assertRegex(WORKFLOW, r"(?m)^permissions: \{\}$")
        self.assertRegex(WORKFLOW, r"(?m)^    permissions:\n      contents: write$")

    def test_checkout_does_not_persist_credentials(self):
        self.assertIn("persist-credentials: false", step("Check out"))

    def test_token_and_key_only_in_their_steps(self):
        commit = step("Commit changes")
        self.assertIn("GH_TOKEN: ${{ github.token }}", commit)
        self.assertIn("if: github.ref == 'refs/heads/main'", commit)
        self.assertEqual(WORKFLOW.count("github.token"), 1)
        self.assertEqual(WORKFLOW.count("secrets.DATA_GO_KR_SERVICE_KEY"), 1)
        self.assertIn("secrets.DATA_GO_KR_SERVICE_KEY", step("Collect"))
        self.assertNotIn("set -x", WORKFLOW)

    def test_rebase_and_push_retry(self):
        commit = step("Commit changes")
        self.assertIn('pull --rebase origin "${GITHUB_REF_NAME}"', commit)
        self.assertIn("for attempt in 1 2 3", commit)
        self.assertNotIn("echo \"${GH_TOKEN}", commit)


if __name__ == "__main__":
    unittest.main()
