#!/usr/bin/env python3
"""Проверка локальных repos/<group_id> через Codex CLI (Python 3.10+).

Сначала запустите sync_repos.py. Проверяется зафиксированный HEAD, не рабочее дерево.
Результаты: results/<group_id>/<lab_id>.json и aggregated.json в корне проекта.
Кеш читается из aggregated.json: итоговая 5 замораживается, иначе сравниваются входы.
Технические ошибки повторяются. --force отменяет оба условия пропуска.
Дата сдачи — committer date последнего изменения артефактов этой лабораторной.
Штраф — 1 при просрочке строго больше 72 часов. Для 3+ грубых ошибок диаграммы
оценка остаётся null (needs_teacher), пока преподаватель не определит шкалу.
"""

import argparse
import copy
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
DIAGRAMS = {"use_case.md", "er.md", "system_sequence.md", "class_sequence.md", "classes.md"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}


class RepositoryStateError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=f".{path.name}.", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.replace(temporary, path)


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def relevant(path, lab):
    return any(path == artifact["path"].rstrip("/") or
               (artifact["path"].endswith("/") and path.startswith(artifact["path"]))
               for artifact in lab["artifacts"])


def inspect_repo(repo, lab):
    if repo.is_symlink() or not (repo / ".git").is_dir():
        raise RepositoryStateError("repository_unavailable",
                                   "Локальный репозиторий отсутствует или недоступен; запустите sync_repos.py.")
    # У нового пустого клона HEAD указывает на ещё не существующую ветку.
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", "HEAD^{commit}"],
                          capture_output=True, timeout=60)
    if head.returncode:
        branch = git(repo, "symbolic-ref", "--quiet", "HEAD").decode().strip()
        reference = subprocess.run(["git", "-C", str(repo), "show-ref", "--verify", "--quiet", branch],
                                   capture_output=True, timeout=60)
        if reference.returncode == 1:
            raise RepositoryStateError("empty_repository", "В репозитории пока нет коммитов; работа не сдана.")
        raise RuntimeError(head.stderr.decode(errors="replace").strip() or "Не удалось прочитать HEAD репозитория")
    commit = head.stdout.decode().strip()
    entries = []
    for record in git(repo, "ls-tree", "-rz", "--full-tree", commit).split(b"\0"):
        if not record:
            continue
        metadata, name = record.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        path = name.decode("utf-8")
        if relevant(path, lab) or path in DIAGRAMS or path == "README.md":
            entries.append({"path": path, "mode": mode, "kind": kind, "oid": oid})
    primary = [entry for entry in entries if relevant(entry["path"], lab)]
    paths = [artifact["path"] for artifact in lab["artifacts"]]
    submitted_at = git(repo, "log", "-1", "--format=%cI", commit, "--", *paths).decode().strip()
    return commit, entries, primary, submitted_at or None


def fingerprint(group, lab, entries, submitted_at, domains, prompt, schema, model):
    value = {"repository": group["repository"], "assignment_number": group["assignment_number"],
             "lab": lab, "entries": entries, "submitted_at": submitted_at,
             "domains": domains, "prompt": prompt, "schema": schema, "model": model,
             "checker_version": 1}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def should_skip(previous, current_fingerprint, force=False):
    if force or not previous or previous.get("status") in {"error", "repository_unavailable", "empty_repository", "no_repository"}:
        return False
    return previous.get("grade") == 5 or previous.get("fingerprint") == current_fingerprint


def validate_review(review):
    keys = {"status", "major_errors", "minor_comments", "summary", "application_source_present",
            "video_report_present", "code_style_consistent"}
    if not isinstance(review, dict) or set(review) != keys:
        raise ValueError("Ответ Codex не соответствует схеме")
    for key in ("major_errors", "minor_comments"):
        if not isinstance(review[key], list) or any(not isinstance(item, str) or not item.strip()
                                                  for item in review[key]):
            raise ValueError(f"Некорректное поле Codex: {key}")
        review[key] = list(dict.fromkeys(item.strip() for item in review[key]))
    if not isinstance(review["summary"], str) or not review["summary"].strip():
        raise ValueError("Пустое резюме Codex")
    if review["status"] not in {"checked", "error"}:
        raise ValueError("Некорректный статус Codex")
    if review["status"] == "error":
        raise RuntimeError(f"Codex не смог проверить работу: {review['summary']}")
    for key in ("application_source_present", "video_report_present", "code_style_consistent"):
        if type(review[key]) is not bool:
            raise ValueError(f"Некорректное поле Codex: {key}")
    return review


def calculate_grade(lab, review, submitted_at):
    if lab["id"] == "lab06":
        if not review["application_source_present"] or not review["video_report_present"]:
            return "not_submitted", None, 0, None
        base = 3 if review["major_errors"] else (5 if review["code_style_consistent"] else 4)
    else:
        errors = len(review["major_errors"])
        if errors > 2:
            return "needs_teacher", None, 0, None
        base = 5 - errors
    if submitted_at is None:
        raise ValueError("Не удалось определить дату сдачи")
    deadline = datetime.fromisoformat(lab["deadline"])
    submitted = datetime.fromisoformat(submitted_at)
    penalty = int(submitted > deadline + timedelta(days=3))
    return "checked", base, penalty, base - penalty


def run_codex(repo, commit, entries, group, lab, domains, args, prompt):
    # Нейтральная папка не загружает настройки и AGENTS.md студенческого репозитория.
    with tempfile.TemporaryDirectory(prefix="pis-review-") as temporary:
        workspace = Path(temporary)
        student = workspace / "student"
        student.mkdir()
        manifest = []
        videos = []
        for entry in entries:
            path = PurePosixPath(entry["path"])
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Недопустимый путь в репозитории")
            if entry["kind"] != "blob" or entry["mode"] not in {"100644", "100755"}:
                manifest.append({**entry, "available": False})
                continue
            size = int(git(repo, "cat-file", "-s", entry["oid"]))
            is_video = str(path).startswith("report/") and path.suffix.lower() in VIDEO_EXTENSIONS
            is_lfs = False
            if size <= 1024:
                is_lfs = git(repo, "cat-file", "blob", entry["oid"]).startswith(
                    b"version https://git-lfs.github.com/spec/v1")
            if is_video and size > 0 and not is_lfs:
                videos.append(str(path))
            available = not is_video and not is_lfs and path.name != "AGENTS.md" and ".codex" not in path.parts
            manifest.append({**entry, "size": size, "available": available, "git_lfs_pointer": is_lfs})
            if available:
                target = student / str(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(git(repo, "cat-file", "blob", entry["oid"]))
        context = {"group": group, "lab": lab, "commit": commit, "domains": domains,
                   "manifest": manifest, "video_files": videos}
        write_json(workspace / "context.json", context)
        output = workspace / "review.json"
        command = [args.codex_bin, "-a", "never", "exec", "--sandbox", "read-only",
                   "--skip-git-repo-check", "--output-schema", str(SCRIPTS_DIR / "review.schema.json"),
                   "--output-last-message", str(output)]
        if args.model:
            command.extend(["--model", args.model])
        command.append("-")
        process = subprocess.run(command, input=prompt, text=True, cwd=workspace,
                                 capture_output=True, timeout=args.timeout)
        if process.returncode:
            raise RuntimeError(f"Codex завершился с кодом {process.returncode}: {process.stderr[-1500:]}")
        review = validate_review(read_json(output))
        if lab["id"] == "lab06":
            review["video_report_present"] = bool(videos)
            if not any(item["path"].startswith("solution/") and item.get("available") and item.get("size", 0)
                       for item in manifest):
                review["application_source_present"] = False
        return review


def review_lab(base, group, lab, previous, domains, prompt, schema, args):
    result = {"group_id": group["id"], "lab_id": lab["id"], "title": lab["title"], "deadline": lab["deadline"],
              "status": "error", "base_grade": None, "late_penalty": 0, "grade": None,
              "comments": [], "submitted_at": None, "checked_at": None, "fingerprint": None}
    if not args.force and previous and previous.get("grade") == 5:
        result = copy.deepcopy(previous)
        result["skip_reason"] = "grade_5"
        return result
    if not group["repository"]:
        result.update(status="no_repository", comments=["Репозиторий команды не указан."])
        return result
    try:
        repo = base / "repos" / group["id"]
        commit, entries, primary, submitted_at = inspect_repo(repo, lab)
        current = fingerprint(group, lab, entries, submitted_at, domains, prompt, schema, args.model)
        if should_skip(previous, current, args.force):
            result = copy.deepcopy(previous)
            result["skip_reason"] = "grade_5" if previous.get("grade") == 5 else "unchanged"
            return result
        result.update(commit=commit, submitted_at=submitted_at, fingerprint=current)
        if not primary:
            result.update(status="not_submitted", submitted_at=None,
                          comments=["Файлы лабораторной отсутствуют."])
            return result
        review = run_codex(repo, commit, entries, group, lab, domains, args, prompt)
        status, base_grade, penalty, grade = calculate_grade(lab, review, submitted_at)
        comments = [review["summary"], *review["major_errors"], *review["minor_comments"]]
        if status == "needs_teacher":
            comments.append("Нужна оценка преподавателя: шкала для трёх и более грубых ошибок не определена.")
        if status == "not_submitted":
            comments.append("Для сдачи обязательны и код приложения, и видеоотчёт.")
        if penalty:
            comments.append("−1 балл: просрочка больше трёх дней.")
        result.update(status=status, base_grade=base_grade, late_penalty=penalty, grade=grade,
                      comments=list(dict.fromkeys(comments)), review=review, checked_at=now())
    except RepositoryStateError as error:
        result.update(status=error.status, comments=[str(error)])
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        result.update(status="error", comments=["Техническая ошибка проверки; требуется повторный запуск."],
                      technical_error=str(error), checked_at=now())
        if previous and previous.get("grade") is not None:
            result["previous_result"] = {key: previous.get(key) for key in
                                         ("grade", "comments", "submitted_at", "checked_at", "commit")}
    return result


def validate_config(groups, labs):
    for collection in (groups, labs):
        seen = set()
        for item in collection:
            identifier = item["id"]
            if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", identifier) or identifier in seen:
                raise ValueError(f"Недопустимый/повторяющийся ID: {identifier!r}")
            seen.add(identifier)
    for lab in labs:
        if datetime.fromisoformat(lab["deadline"]).utcoffset() is None:
            raise ValueError("В дедлайне должен быть указан часовой пояс")
        for artifact in lab["artifacts"]:
            path = PurePosixPath(artifact["path"])
            if path.is_absolute() or ".." in path.parts or str(path) == ".":
                raise ValueError("Недопустимый путь артефакта")
    for group in groups:
        if not isinstance(group["name"], str) or type(group["assignment_number"]) is not int:
            raise ValueError("Некорректное описание команды")
        if group["repository"] is not None and not isinstance(group["repository"], str):
            raise ValueError("Некорректная ссылка на репозиторий")


def run(args):
    base = args.base_dir.resolve()
    groups = read_json(base / "groups.json")["groups"]
    labs = read_json(base / "labs.json")["labs"]
    validate_config(groups, labs)
    domains = (base / "domains.md").read_text(encoding="utf-8")
    prompt = (SCRIPTS_DIR / "review_prompt.txt").read_text(encoding="utf-8")
    schema = read_json(SCRIPTS_DIR / "review.schema.json")
    aggregate_path = base / "aggregated.json"
    previous = read_json(aggregate_path) if aggregate_path.exists() else {"groups": []}
    cache = {(group["id"], lab["lab_id"]): lab for group in previous["groups"] for lab in group["labs"]}
    aggregate = {"generated_at": now(), "groups": []}
    for group in groups:
        pending = []
        for lab in labs:
            old = cache.get((group["id"], lab["id"]))
            pending.append(copy.deepcopy(old) if old else {
                "group_id": group["id"], "lab_id": lab["id"], "title": lab["title"],
                "deadline": lab["deadline"], "status": "pending_check", "grade": None,
                "comments": [], "submitted_at": None, "checked_at": None,
            })
        aggregate["groups"].append({**group, "labs": pending})
    failures = 0
    for group, output_group in zip(groups, aggregate["groups"]):
        for index, lab in enumerate(labs):
            print(f"{group['id']} / {lab['id']}...", flush=True)
            old = cache.get((group["id"], lab["id"]))
            result = review_lab(base, group, lab, old, domains, prompt, schema, args)
            write_json(base / "results" / group["id"] / f"{lab['id']}.json", result)
            output_group["labs"][index] = result
            aggregate["generated_at"] = now()
            write_json(aggregate_path, aggregate)
            failures += result["status"] == "error"
            print(f"  {result['status']}: {result['grade']} {result.get('skip_reason', '')}", flush=True)
    aggregate["generated_at"] = now()
    write_json(aggregate_path, aggregate)
    print(f"Готово: {aggregate_path}; технических ошибок: {failures}")
    return int(failures > 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-dir", type=Path, default=BASE_DIR, help="Корень проверочного репозитория")
    parser.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", "codex"), help="Путь к Codex CLI")
    parser.add_argument("--model", help="Модель; по умолчанию из настроек Codex CLI")
    parser.add_argument("--timeout", type=int, default=600, help="Таймаут одной проверки, секунды")
    parser.add_argument("--force", action="store_true", help="Перепроверить также пятёрки и неизменившиеся работы")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout должен быть положительным")
    try:
        lock_path = args.base_dir / "results" / ".check_labs.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Другой процесс уже проверяет лабораторные")
            return run(args)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
