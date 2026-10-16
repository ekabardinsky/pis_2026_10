"""Проверки порядка этапов без сетевых запросов и запуска AI."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class RunCheckTests(unittest.TestCase):
    def run_pipeline(self, failing_step=None):
        with tempfile.TemporaryDirectory(prefix="pis-pipeline-test-") as temporary:
            folder = Path(temporary)
            fake_python = folder / "fake-python"
            log = folder / "calls.jsonl"
            fake_python.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "from pathlib import Path\n"
                "with open(os.environ['PIPELINE_TEST_LOG'], 'a') as log:\n"
                "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "sys.exit(int(Path(sys.argv[2]).name == os.environ.get('PIPELINE_TEST_FAIL')))\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            env = {**os.environ, "PYTHON_BIN": str(fake_python), "PIPELINE_TEST_LOG": str(log),
                   "PIPELINE_TEST_FAIL": failing_step or ""}
            result = subprocess.run(["bash", str(BASE_DIR / "run_check.sh"), "--force"],
                                    cwd=folder, env=env, capture_output=True, text=True, timeout=10)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual([Path(call[1]).name for call in calls],
                             ["sync_repos.py", "check_labs.py", "render_summary.py"])
            self.assertEqual(calls[1][2:], ["--force"])
            self.assertTrue(all(Path(call[1]).is_absolute() for call in calls))
            return result

    def test_success(self):
        self.assertEqual(self.run_pipeline().returncode, 0)

    def test_reports_generated_after_check_failure(self):
        result = self.run_pipeline("check_labs.py")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Этапы с ошибками", result.stderr)

    def test_sync_failure_is_reported_and_remaining_steps_run(self):
        self.assertEqual(self.run_pipeline("sync_repos.py").returncode, 1)


if __name__ == "__main__":
    unittest.main()
