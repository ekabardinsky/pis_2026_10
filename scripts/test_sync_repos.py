"""Проверки синхронизации на локальных репозиториях Git."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sync_repos as sync


class SyncTests(unittest.TestCase):
    def test_empty_clone_recovers_after_first_push_to_main(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            remote = base / "remote.git"
            source = base / "source"
            repos = base / "repos"
            repos.mkdir()
            sync.run_git("init", "--bare", str(remote))
            sync.run_git("-C", str(remote), "symbolic-ref", "HEAD", "refs/heads/master")
            group = {"id": "group03", "repository": str(remote)}
            with patch.object(sync, "REPOS_DIR", repos):
                sync.sync_repository(group)
                sync.run_git("init", str(source))
                sync.run_git("-C", str(source), "symbolic-ref", "HEAD", "refs/heads/main")
                (source / "lab.md").write_text("Solution", encoding="utf-8")
                sync.run_git("-C", str(source), "add", ".")
                sync.run_git("-C", str(source), "-c", "user.name=Test",
                             "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false",
                             "commit", "-m", "First lab")
                sync.run_git("-C", str(source), "push", str(remote), "main")
                # HEAD сервера всё ещё указывает на отсутствующую master.
                sync.sync_repository(group)
                target = repos / "group03"
                self.assertEqual(sync.run_git("-C", str(target), "symbolic-ref", "--short", "HEAD"), "main")
                self.assertEqual((target / "lab.md").read_text(), "Solution")
                self.assertEqual(sync.sync_repository(group), "обновлён")
                # Новый клон также восстанавливается при неверном HEAD сервера.
                sync.sync_repository({**group, "id": "fresh"})
                self.assertTrue((repos / "fresh" / "lab.md").is_file())


if __name__ == "__main__":
    unittest.main()
