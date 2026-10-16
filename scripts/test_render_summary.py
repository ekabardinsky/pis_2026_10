import unittest
from zoneinfo import ZoneInfo

from render_summary import render_summary


class SummaryTests(unittest.TestCase):
    def render(self, grades):
        groups = [{"id": "group01", "name": "Команда | тест"}]
        labs = [{"id": f"lab{i:02}", "title": f"Работа {i}", "required": True,
                 "deadline": "2026-10-30T23:59:59+05:00"} for i in range(1, 4)]
        results = [{"lab_id": lab["id"], "grade": grade,
                    "status": "checked" if grade is not None else "not_submitted",
                    "review": {"major_errors": ["Ошибка"], "minor_comments": []}}
                   for lab, grade in zip(labs, grades)]
        return render_summary(groups, labs, {"groups": [{"id": "group01", "labs": results}]},
                              ZoneInfo("Asia/Yekaterinburg"))

    def test_complete_average_rounds_down(self):
        text = self.render([5, 5, 4])
        self.assertIn("| Команда &#124; тест | 4 | 4.67 | 3/3 |", text)
        self.assertIn("5<br>[Открыть отчёт и замечания](results/group01/lab01.md)", text)
        self.assertIn("30.10.2026 23:59", text)
        self.assertIn("lab01 — исправить: 1", text)

    def test_incomplete_has_current_average_only(self):
        self.assertIn("| Команда &#124; тест | — | 5.00 | 1/3 |", self.render([5, None, None]))

    def test_no_grades(self):
        self.assertIn("| Команда &#124; тест | — | — | 0/3 |", self.render([None, None, None]))


if __name__ == "__main__":
    unittest.main()
