#!/usr/bin/env python3
"""Пересоздаёт results/<group_id>/<lab_id>.md из соседних JSON-результатов.

Запуск: python3 scripts/render_reports.py
Исходные JSON не изменяются. Все существующие Markdown-отчёты перезаписываются.
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent
STATUSES = {
    "checked": "Проверено (предварительная оценка AI)",
    "not_submitted": "Не сдано",
    "needs_teacher": "Требуется оценка преподавателя",
    "error": "Техническая ошибка проверки — требуется повторный запуск",
    "no_repository": "Не указана ссылка на репозиторий",
    "repository_unavailable": "Репозиторий недоступен локально",
    "empty_repository": "В репозитории пока нет коммитов",
    "pending_check": "Ожидает проверки",
    "pending_recheck": "Ожидает повторной проверки",
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def one_line(value):
    return " ".join(str(value).splitlines())


def format_date(value, zone):
    if not value:
        return "—"
    return datetime.fromisoformat(value).astimezone(zone).strftime("%d.%m.%Y %H:%M:%S")


def render_report(result, group, filename, zone):
    grade = result.get("grade")
    lines = [
        f"# {one_line(result.get('title', result['lab_id']))}",
        "",
        f"Команда: {one_line(group.get('name', result.get('group_id', '—')))}.",
        "",
        f"Статус: {STATUSES.get(result.get('status'), one_line(result.get('status', 'Неизвестен')))}.",
        "",
        f"Оценка: **{grade}/5**." if grade is not None else "Оценка: **не выставлена**.",
        "",
        f"Дата сдачи: {format_date(result.get('submitted_at'), zone)}.",
        f"Дедлайн: {format_date(result.get('deadline'), zone)}.",
        f"Дата проверки: {format_date(result.get('checked_at'), zone)}.",
        f"Часовой пояс: {zone.key}.",
    ]
    if result.get("base_grade") is not None:
        lines.extend(["", f"Балл до штрафа: {result['base_grade']}. "
                      f"Штраф за просрочку: {result.get('late_penalty', 0)}."])
    if result.get("skip_reason") == "grade_5":
        lines.extend(["", "Сохранена ранее полученная пятёрка; новая версия работы не проверялась."])
    elif result.get("skip_reason") == "unchanged":
        lines.extend(["", "Повторная проверка не требовалась: проверяемые файлы и требования не изменились."])
    review = result.get("review")
    if review:
        lines.extend(["", "## Резюме", "", one_line(review.get("summary", "")),
                      "", "## Обязательно исправить", ""])
        major_errors = review.get("major_errors", [])
        if major_errors:
            lines.extend(f"- {one_line(comment)}" for comment in major_errors)
        else:
            lines.append("Грубых ошибок не обнаружено.")
        lines.extend(["", "## Желательные улучшения", ""])
        minor_comments = review.get("minor_comments", [])
        if minor_comments:
            lines.extend(f"- {one_line(comment)}" for comment in minor_comments)
        else:
            lines.append("Дополнительных замечаний нет.")
        rendered = {review.get("summary", ""), *major_errors, *minor_comments}
        extra_comments = [comment for comment in result.get("comments", []) if comment not in rendered]
        if extra_comments:
            lines.extend(["", "## Дополнительная информация", ""])
            lines.extend(f"- {one_line(comment)}" for comment in extra_comments)
    else:
        lines.extend(["", "## Информация о проверке", ""])
        comments = result.get("comments", [])
        if comments:
            lines.extend(f"- {one_line(comment)}" for comment in comments)
        else:
            lines.append("Замечаний нет." if result.get("status") == "checked" else "Проверка ещё не завершена.")
    previous = result.get("previous_result")
    if previous and previous.get("grade") is not None:
        lines.extend(["", f"Предыдущая оценка: {previous['grade']}/5. "
                      "Она относится к предыдущей проверке и не подтверждает текущую версию работы."])
    lines.extend(["", f"[Результат в JSON]({filename})", "",
                  "Окончательное решение об оценке принимает преподаватель.", ""])
    return "\n".join(lines)


def write_report(path, content):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=f".{path.name}.", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    os.replace(temporary, path)


def generate_reports(base):
    groups = {group["id"]: group for group in read_json(base / "groups.json")["groups"]}
    zone = ZoneInfo(read_json(base / "labs.json").get("timezone", "Asia/Yekaterinburg"))
    count = failures = 0
    for source in sorted((base / "results").glob("*/lab*.json")):
        try:
            result = read_json(source)
            group = groups.get(source.parent.name, {"name": source.parent.name})
            output = source.with_suffix(".md")
            write_report(output, render_report(result, group, source.name, zone))
            count += 1
            print(f"Обновлён: {output.relative_to(base)}")
        except (OSError, ValueError, KeyError, TypeError) as error:
            failures += 1
            print(f"Ошибка в {source}: {error}", file=sys.stderr)
    print(f"Готово: отчётов — {count}, ошибок — {failures}.")
    return int(failures > 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, default=BASE_DIR, help="Корень проверочного репозитория")
    args = parser.parse_args()
    try:
        return generate_reports(args.base_dir.resolve())
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
