"""Проверки конвейера на временных Git-репозиториях, без обращения к AI."""

import argparse
import copy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_labs as checker


def response(errors=0, style=True):
    return {"status": "checked", "major_errors": [f"Ошибка {n}" for n in range(errors)],
            "minor_comments": [], "summary": "Проверка завершена.",
            "application_source_present": True, "video_report_present": True,
            "code_style_consistent": style}


class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pis-check-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.repo = self.base / "repos" / "group01"
        self.repo.mkdir(parents=True)
        checker.git(self.repo, "init")
        self.group = {"id": "group01", "name": "Тест", "assignment_number": 1,
                      "repository": "https://example.com/repo"}
        self.labs = checker.read_json(checker.BASE_DIR / "labs.json")["labs"]
        self.lab = copy.deepcopy(self.labs[0])
        self.lab["deadline"] = "2026-12-31T23:59:59+05:00"
        self.args = argparse.Namespace(base_dir=self.base, codex_bin="fake-codex", model=None,
                                       timeout=10, force=False)
        self.calls = 0
        self.answer = response(1)
        self.fail = False
        self.original_run = subprocess.run

    def commit(self, path="use_case.md", content="```mermaid\nusecase-beta\nactor User\nusecase Apply(Подать заявление)\nUser --> Apply\n```\nПояснения"):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        checker.git(self.repo, "add", "--", path)
        checker.git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "-c", "commit.gpgsign=false", "commit", "-m", "test")

    def fake_run(self, command, **kwargs):
        if command[0] != "fake-codex":
            return self.original_run(command, **kwargs)
        self.calls += 1
        workspace = Path(kwargs["cwd"])
        context = checker.read_json(workspace / "context.json")
        self.assertIn("manifest", context)
        self.assertIn("read-only", command)
        self.assertFalse((workspace / "student" / "AGENTS.md").exists())
        if self.fail:
            return subprocess.CompletedProcess(command, 1, "", "test failure")
        output = Path(command[command.index("--output-last-message") + 1])
        checker.write_json(output, self.answer)
        return subprocess.CompletedProcess(command, 0, "", "")

    def check(self, previous=None, lab=None):
        with patch.object(checker.subprocess, "run", side_effect=self.fake_run):
            return checker.review_lab(self.base, self.group, lab or self.lab, previous,
                                      "Тема", "Промпт", {}, self.args)

    def test_cache_and_committed_snapshot(self):
        self.commit()
        first = self.check()
        self.assertEqual(first["grade"], 4)
        self.assertIsNotNone(first["submitted_at"])
        self.assertEqual(self.check(first)["skip_reason"], "unchanged")
        self.assertEqual(self.calls, 1)
        # Незакоммиченная правка не входит в проверяемый снимок.
        (self.repo / "use_case.md").write_text("local edit", encoding="utf-8")
        self.check(first)
        self.assertEqual(self.calls, 1)
        self.commit(content="новая диаграмма")
        changed = self.check(first)
        self.assertEqual(self.calls, 2)
        self.assertNotEqual(first["fingerprint"], changed["fingerprint"])

    def test_empty_repository_then_first_commit(self):
        empty = self.check()
        self.assertEqual(empty["status"], "empty_repository")
        self.assertIsNone(empty["grade"])
        self.assertIsNone(empty["submitted_at"])
        self.assertNotIn("technical_error", empty)
        self.assertEqual(self.calls, 0)
        self.commit()
        self.assertEqual(self.check(empty)["status"], "checked")
        self.assertEqual(self.calls, 1)

    def test_missing_repository(self):
        self.repo.rename(self.repo.with_name("saved_repo"))
        missing = self.check()
        self.assertEqual(missing["status"], "repository_unavailable")
        self.assertIsNone(missing["grade"])
        self.assertNotIn("technical_error", missing)
        self.assertEqual(self.calls, 0)

    def test_no_repository_url(self):
        self.group["repository"] = None
        self.assertEqual(self.check()["status"], "no_repository")
        self.assertEqual(self.calls, 0)

    def test_five_frozen_and_force(self):
        self.commit()
        self.answer = response(0)
        first = self.check()
        self.assertEqual(first["grade"], 5)
        self.commit(content="новая версия")
        self.assertEqual(self.check(first)["skip_reason"], "grade_5")
        self.assertEqual(self.calls, 1)
        self.args.force = True
        self.check(first)
        self.assertEqual(self.calls, 2)

    def test_error_retried_and_previous_preserved(self):
        self.commit()
        first = self.check()
        self.commit(content="исправление")
        self.fail = True
        failed = self.check(first)
        self.assertEqual(failed["status"], "error")
        self.assertIsNone(failed["grade"])
        self.assertEqual(failed["previous_result"]["grade"], 4)
        self.fail = False
        self.assertEqual(self.check(failed)["grade"], 4)
        self.assertEqual(self.calls, 3)

    def test_missing_video_and_lfs_pointer(self):
        self.commit("solution/main.py", "print('app')")
        self.commit("report/demo.mp4", "version https://git-lfs.github.com/spec/v1\noid sha256:123\nsize 999\n")
        missing = self.check(lab=self.labs[5])
        self.assertEqual(missing["status"], "not_submitted")
        self.assertIsNone(missing["grade"])
        self.commit("report/demo.mp4", "nonempty video fixture")
        self.answer = response(0, style=False)
        self.assertEqual(self.check(lab=self.labs[5])["base_grade"], 4)

    def test_codex_error_or_malformed_response_is_not_grade(self):
        self.commit()
        self.answer = response(0)
        self.answer["status"] = "error"
        self.assertEqual(self.check()["status"], "error")
        self.answer = {"summary": "Ответ без схемы"}
        self.assertIsNone(self.check()["grade"])

    def test_deleted_artifact(self):
        self.commit()
        first = self.check()
        checker.git(self.repo, "rm", "use_case.md")
        checker.git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "-c", "commit.gpgsign=false", "commit", "-m", "delete")
        deleted = self.check(first)
        self.assertEqual(deleted["status"], "not_submitted")
        self.assertIsNone(deleted["submitted_at"])

    def test_grade_and_penalty_boundary(self):
        lab = {"id": "lab01", "deadline": "2026-10-30T23:59:59+05:00"}
        for errors, score in [(0, 5), (1, 4), (2, 3)]:
            self.assertEqual(checker.calculate_grade(lab, response(errors),
                             "2026-11-02T23:59:59+05:00"), ("checked", score, 0, score))
        self.assertEqual(checker.calculate_grade(lab, response(0),
                         "2026-11-03T00:00:00+05:00"), ("checked", 5, 1, 4))
        self.assertEqual(checker.calculate_grade(lab, response(3),
                         "2026-10-30T23:59:59+05:00")[0], "needs_teacher")
        for errors, style, score in [(1, True, 3), (0, False, 4), (0, True, 5)]:
            self.assertEqual(checker.calculate_grade(self.labs[5], response(errors, style),
                             "2026-10-30T23:59:59+05:00")[3], score)

    def test_aggregation_and_cache_on_second_run(self):
        self.commit()
        checker.write_json(self.base / "groups.json", {"groups": [self.group]})
        checker.write_json(self.base / "labs.json", {"labs": [self.lab]})
        (self.base / "domains.md").write_text("Тема", encoding="utf-8")
        with patch.object(checker.subprocess, "run", side_effect=self.fake_run):
            self.assertEqual(checker.run(self.args), 0)
            self.assertEqual(checker.run(self.args), 0)
        aggregate = checker.read_json(self.base / "aggregated.json")
        result = checker.read_json(self.base / "results/group01/lab01.json")
        self.assertEqual(aggregate["groups"][0]["labs"][0], result)
        self.assertEqual(result["grade"], 4)
        self.assertEqual(self.calls, 1)

    def test_results_updated_before_next_lab_and_after_interruption(self):
        checker.write_json(self.base / "groups.json", {"groups": [self.group]})
        checker.write_json(self.base / "labs.json", {"labs": self.labs[:2]})
        (self.base / "domains.md").write_text("Тема", encoding="utf-8")
        old = {"lab_id": "lab02", "status": "checked", "grade": 3,
               "comments": ["Предыдущий результат"], "submitted_at": None}
        checker.write_json(self.base / "aggregated.json", {
            "groups": [{**self.group, "labs": [old]}]})
        first = {"lab_id": "lab01", "status": "checked", "grade": 4,
                 "comments": ["Новый результат"], "submitted_at": "2026-10-16T12:00:00+05:00"}

        def check_next(*args):
            lab = args[2]
            if lab["id"] == "lab01":
                return first
            aggregate = checker.read_json(self.base / "aggregated.json")
            results = aggregate["groups"][0]["labs"]
            self.assertEqual(results, [first, old])
            self.assertEqual(checker.read_json(self.base / "results/group01/lab01.json"), first)
            raise KeyboardInterrupt

        with patch.object(checker, "review_lab", side_effect=check_next):
            with self.assertRaises(KeyboardInterrupt):
                checker.run(self.args)
        self.assertEqual(checker.read_json(self.base / "aggregated.json")["groups"][0]["labs"],
                         [first, old])


if __name__ == "__main__":
    unittest.main()
