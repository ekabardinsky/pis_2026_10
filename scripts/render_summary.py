#!/usr/bin/env python3
"""Обновляет Markdown-отчёты и сводную таблицу results.md.

Запуск: python3 scripts/render_summary.py
Команды и сроки берутся из groups.json/labs.json, оценки — из aggregated.json.
Автомат = среднее итоговых оценок с округлением вниз, когда оценены все
обязательные работы. До этого показывается только текущий средний по оценённым.
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from render_reports import STATUSES, generate_reports, read_json, write_report

BASE_DIR = Path(__file__).resolve().parent.parent


def cell(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(
        ">", "&gt;").replace("|", "&#124;").replace("\n", " ").replace("\r", " ")


def valid_grade(result):
    grade = result.get("grade")
    return grade if result.get("status") == "checked" and type(grade) is int and 0 <= grade <= 5 else None


def render_summary(groups, labs, aggregate, zone):
    results = {group["id"]: {lab["lab_id"]: lab for lab in group["labs"]}
               for group in aggregate["groups"]}
    headers = ["Группа", "Автомат", "Текущий средний", "Оценено"]
    for lab in labs:
        deadline = datetime.fromisoformat(lab["deadline"]).astimezone(zone).strftime("%d.%m.%Y %H:%M")
        headers.append(f"{cell(lab['id'])}: {cell(lab['title'])}<br>до {deadline}")
    headers.append("Комментарии")
    updated = aggregate.get("generated_at")
    updated = datetime.fromisoformat(updated).astimezone(zone).strftime("%d.%m.%Y %H:%M:%S") if updated else "—"
    lines = ["# Результаты лабораторных работ по ПИС", "",
             f"Данные проверок обновлены: {updated}. Часовой пояс: {zone.key}.", "",
             "Автомат — среднее итоговых оценок обязательных лабораторных с округлением вниз. "
             "Он рассчитывается после получения оценок за все обязательные работы. "
             "Текущий средний учитывает только оценённые обязательные работы; несданные работы не считаются нулями. "
             "Штрафы за просрочку уже включены в оценки. Оценки предварительные, окончательное решение принимает преподаватель.", "",
             "Чтобы узнать, что исправить, нажмите «Открыть отчёт и замечания» под оценкой или статусом лабораторной. "
             "В комментариях: «исправить» — грубые ошибки, «рекомендации» — желательные улучшения.", "",
             "| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    required = [lab for lab in labs if lab.get("required", True)]
    for group in groups:
        group_results = results.get(group["id"], {})
        grades = [grade for lab in required
                  if (grade := valid_grade(group_results.get(lab["id"], {}))) is not None]
        automatic = str(sum(grades) // len(grades)) if grades and len(grades) == len(required) else "—"
        average = f"{sum(grades) / len(grades):.2f}" if grades else "—"
        row = [cell(group["name"]), automatic, average, f"{len(grades)}/{len(required)}"]
        comments = []
        for lab in labs:
            result = group_results.get(lab["id"])
            if result is None:
                row.append("—")
                continue
            grade = valid_grade(result)
            label = str(grade) if grade is not None else STATUSES.get(result.get("status"), "Ожидает проверки")
            row.append(f"{cell(label)}<br>[Открыть отчёт и замечания](results/{group['id']}/{lab['id']}.md)")
            review = result.get("review", {})
            major = len(review.get("major_errors", []))
            minor = len(review.get("minor_comments", []))
            details = []
            if major:
                details.append(f"исправить: {major}")
            if minor:
                details.append(f"рекомендации: {minor}")
            if result.get("late_penalty"):
                details.append(f"штраф: −{result['late_penalty']}")
            if details:
                comments.append(f"{cell(lab['id'])} — {', '.join(details)}")
        row.append("<br>".join(comments) or "—")
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, default=BASE_DIR)
    args = parser.parse_args()
    base = args.base_dir.resolve()
    try:
        groups = read_json(base / "groups.json")["groups"]
        config = read_json(base / "labs.json")
        labs = config["labs"]
        # ID используются в ссылках на отчёты.
        if any(not re.fullmatch(r"[A-Za-z0-9_-]+", item["id"]) for item in [*groups, *labs]):
            raise ValueError("Недопустимый ID команды или лабораторной")
        aggregate = read_json(base / "aggregated.json")
        text = render_summary(groups, labs, aggregate, ZoneInfo(config["timezone"]))
        if generate_reports(base):
            return 1
        write_report(base / "results.md", text)
        print(f"Сводная таблица обновлена: {base / 'results.md'}")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
