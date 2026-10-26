#!/usr/bin/env python3
"""Скачивает или обновляет repos/<group_id> по списку из groups.json.

Запуск из любой папки: python3 /path/to/pis_2026_10/scripts/sync_repos.py
Используется полная история Git, необходимая для определения дат сдачи.
Локальные изменения и расходящаяся история не перезаписываются.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
GROUPS_FILE = BASE_DIR / "groups.json"
REPOS_DIR = BASE_DIR / "repos"
TIMEOUT = 180


def run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def load_groups() -> list[dict]:
    groups = json.loads(GROUPS_FILE.read_text(encoding="utf-8"))["groups"]
    if not isinstance(groups, list):
        raise ValueError("groups должен быть массивом")
    seen = set()
    for group in groups:
        group_id = group["id"]
        if not isinstance(group_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", group_id):
            raise ValueError(f"Недопустимый ID команды: {group_id!r}")
        if group_id in seen:
            raise ValueError(f"Повторяющийся ID команды: {group_id}")
        seen.add(group_id)
        if not isinstance(group["name"], str):
            raise ValueError(f"{group_id}: name должен быть строкой")
        repository = group["repository"]
        if repository is not None and (
            not isinstance(repository, str) or not repository.strip() or repository.startswith("-")
        ):
            raise ValueError(f"{group_id}: repository должен быть ссылкой или null")
    return groups


def restore_unborn_branch(target: Path) -> None:
    """Восстанавливает checkout клона, созданного до первого push в репозиторий."""
    try:
        run_git("-C", str(target), "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    except RuntimeError:
        branch = run_git("-C", str(target), "symbolic-ref", "--quiet", "HEAD")
        if run_git("-C", str(target), "for-each-ref", "--format=%(refname)", branch):
            raise RuntimeError("HEAD не указывает на коммит; проверьте локальный клон")
    else:
        return
    run_git("-C", str(target), "fetch", "origin")
    advertised = run_git("-C", str(target), "ls-remote", "--symref", "origin")
    branches = set()
    default = None
    for line in advertised.splitlines():
        value, ref = line.split("\t", 1)
        if ref == "HEAD" and value.startswith("ref: refs/heads/"):
            default = value.removeprefix("ref: refs/heads/")
        elif ref.startswith("refs/heads/"):
            branches.add(ref.removeprefix("refs/heads/"))
    if not branches:
        return  # Удалённый репозиторий пока пуст.
    if default not in branches:
        default = next((name for name in ("main", "master") if name in branches), None)
        if default is None:
            if len(branches) != 1:
                raise RuntimeError("Не удалось определить основную ветку origin")
            default = next(iter(branches))
    run_git("-C", str(target), "checkout", "-b", default, "--track", f"origin/{default}")


def sync_repository(group: dict) -> str:
    target = REPOS_DIR / group["id"]
    repository = group["repository"]
    if target.is_symlink():
        raise RuntimeError(f"{target} является символической ссылкой")
    if not target.exists():
        run_git("clone", "--", repository, str(target))
        restore_unborn_branch(target)
        return "склонирован"
    if not (target / ".git").is_dir():
        raise RuntimeError(f"{target} существует, но не является клоном Git")
    origin = run_git("-C", str(target), "remote", "get-url", "origin")
    if origin != repository:
        raise RuntimeError("origin отличается от groups.json; проверьте ссылку и локальный клон")
    if run_git("-C", str(target), "status", "--porcelain"):
        raise RuntimeError("есть локальные изменения; обновление пропущено")
    restore_unborn_branch(target)
    if not run_git("-C", str(target), "for-each-ref", "refs/remotes/origin"):
        return "обновлён (репозиторий пока пуст)"
    run_git("-C", str(target), "pull", "--ff-only")
    return "обновлён"


def main() -> int:
    try:
        groups = load_groups()
        REPOS_DIR.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Ошибка конфигурации: {error}", file=sys.stderr)
        return 1

    synced = skipped = failed = 0
    for group in groups:
        label = f"{group['id']} — {group['name']}"
        if group["repository"] is None:
            print(f"{label}: пропуск, репозиторий не указан", flush=True)
            skipped += 1
            continue
        print(f"{label}: синхронизация...", flush=True)
        try:
            action = sync_repository(group)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            print(f"  Ошибка: {error}", file=sys.stderr, flush=True)
            failed += 1
        else:
            print(f"  Репозиторий {action}: repos/{group['id']}", flush=True)
            synced += 1

    print(f"Готово: успешно — {synced}, пропущено — {skipped}, ошибок — {failed}.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
