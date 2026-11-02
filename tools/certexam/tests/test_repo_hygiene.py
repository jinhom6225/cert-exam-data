"""Repository settings the collector relies on (security review)."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = (ROOT / ".github" / "workflows" / "certexam_collect.yml").read_text(encoding="utf-8")


def step(name):
    match = re.search(r"- name: " + re.escape(name) + r"\n(.*?)(?=\n      - name: |\n  \S|\Z)", WORKFLOW, re.S)
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
        # The other use is the keepalive job, which only gets actions: write.
        self.assertEqual(WORKFLOW.count("github.token"), 2)
        self.assertIn("GH_TOKEN: ${{ github.token }}", step("Re-enable this workflow"))
        self.assertEqual(WORKFLOW.count("secrets.DATA_GO_KR_SERVICE_KEY"), 1)
        self.assertIn("secrets.DATA_GO_KR_SERVICE_KEY", step("Collect"))
        self.assertNotIn("set -x", WORKFLOW)

    def test_keepalive_job_reenables_only_this_workflow(self):
        self.assertRegex(WORKFLOW, r"(?m)^  keepalive:\n(?:    .*\n)*?    permissions:\n      actions: write\n    steps:$")
        self.assertNotRegex(WORKFLOW, r"(?m)^  keepalive:\n(?:    .*\n)*?    needs:")
        keepalive = step("Re-enable this workflow")
        self.assertIn("actions/workflows/certexam_collect.yml/enable", keepalive)
        self.assertIn("::warning::", keepalive)

    def test_telegram_secrets_only_in_notice_step(self):
        notice = step("Telegram notice")
        self.assertIn("if: always()", notice)
        for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            self.assertEqual(WORKFLOW.count(f"secrets.{name}"), 1)
            self.assertIn(f"secrets.{name}", notice)
        # A missing secret only warns; it never fails the run.
        self.assertIn("Telegram notices are off", notice)
        self.assertIn("exit 0", notice)

    def test_two_daily_runs_morning_and_afternoon_kst(self):
        # Plan A10: 03:10 and 15:10 KST (the afternoon run asks for fees first).
        self.assertEqual(re.findall(r'(?m)^    - cron: "([^"]+)"', WORKFLOW), ["10 18 * * *", "10 6 * * *"])

    def test_fees_first_only_for_the_1510_kst_schedule(self):
        # Plan A11: the order follows the schedule that started the run.
        collect = step("Collect")
        self.assertIn("FEES_FIRST: ${{ github.event.schedule == '10 6 * * *' }}", collect)
        self.assertEqual(WORKFLOW.count("args+=(--fees-first)"), 1)
        self.assertIn('if [ "${FEES_FIRST:-false}" = "true" ]; then args+=(--fees-first); fi', collect)
        self.assertIn('if [ "${FULL_RUN:-false}" = "true" ]; then args+=(--full); fi', collect)

    def test_forced_failure_is_manual_only(self):
        self.assertIn("if: inputs.force_fail", step("Forced failure"))
        self.assertRegex(WORKFLOW, r"force_fail:\n(?:        .*\n)*?        default: false")

    def test_rebase_and_push_retry(self):
        commit = step("Commit changes")
        self.assertIn('pull --rebase origin "${GITHUB_REF_NAME}"', commit)
        self.assertIn("for attempt in 1 2 3", commit)
        self.assertNotIn("echo \"${GH_TOKEN}", commit)


if __name__ == "__main__":
    unittest.main()
