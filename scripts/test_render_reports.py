import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from check_labs import write_json
from render_reports import generate_reports, render_report


class ReportTests(unittest.TestCase):
    def test_review_sections_are_separate_and_not_duplicated(self):
        result = {"lab_id": "lab01", "status": "checked", "grade": 4,
                  "review": {"summary": "Краткий итог", "major_errors": ["Исправить связь"],
                             "minor_comments": ["Уточнить название"]},
                  "comments": ["Краткий итог", "Исправить связь", "Уточнить название", "Штраф за просрочку"]}
        text = render_report(result, {}, "lab01.json", ZoneInfo("Asia/Yekaterinburg"))
        self.assertLess(text.index("## Резюме"), text.index("## Обязательно исправить"))
        self.assertLess(text.index("## Обязательно исправить"), text.index("## Желательные улучшения"))
        for comment in result["comments"]:
            self.assertEqual(text.count(comment), 1)
        self.assertIn("## Дополнительная информация\n\n- Штраф за просрочку", text)

    def test_dates_grade_and_comments(self):
        result = {"lab_id": "lab01", "title": "Прецеденты", "status": "checked", "grade": 4,
                  "base_grade": 5, "late_penalty": 1, "comments": ["Добавить пояснения"],
                  "submitted_at": "2026-10-16T08:00:00+00:00"}
        text = render_report(result, {"name": "Команда"}, "lab01.json", ZoneInfo("Asia/Yekaterinburg"))
        self.assertIn("16.10.2026 13:00:00", text)
        self.assertIn("**4/5**", text)
        self.assertIn("Штраф за просрочку: 1", text)
        self.assertIn("- Добавить пояснения", text)

    def test_missing_result_is_not_a_pass(self):
        text = render_report({"lab_id": "lab01", "status": "empty_repository"}, {},
                             "lab01.json", ZoneInfo("Asia/Yekaterinburg"))
        self.assertIn("не выставлена", text)
        self.assertNotIn("Замечаний нет.", text)

    def test_report_is_overwritten_after_json_change(self):
        with tempfile.TemporaryDirectory(prefix="pis-report-test-") as temporary:
            base = Path(temporary)
            write_json(base / "groups.json", {"groups": [{"id": "group01", "name": "Команда"}]})
            write_json(base / "labs.json", {"timezone": "Asia/Yekaterinburg"})
            path = base / "results/group01/lab01.json"
            result = {"lab_id": "lab01", "status": "checked", "grade": 3, "comments": ["Исправить связь"]}
            write_json(path, result)
            self.assertEqual(generate_reports(base), 0)
            self.assertIn("Исправить связь", path.with_suffix(".md").read_text())
            result.update(grade=5, comments=[])
            write_json(path, result)
            self.assertEqual(generate_reports(base), 0)
            text = path.with_suffix(".md").read_text()
            self.assertIn("**5/5**", text)
            self.assertNotIn("Исправить связь", text)


if __name__ == "__main__":
    unittest.main()
