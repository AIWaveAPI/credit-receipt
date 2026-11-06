import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from credit_receipt.input import InputError
from credit_receipt.workbuddy import read_workbuddy


HEADERS = ["RequestID", "积分消耗", "模型", "使用端", "时间"]


class WorkBuddyImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "request-usage.xlsx"
        self.rows = [
            ["private-id-a", "1.19", "model-a", "WorkBuddy", "2026-11-06 19:18:22"],
            ["private-id-b", "0", "model-b", "WorkBuddy", "2026-11-06 19:19:22"],
            ["private-id-c", "0.01", "model-a", "WorkBuddy", "2026-11-06 19:20:22"],
        ]
        self.write_book()

    def write_book(self, headers=HEADERS):
        book = Workbook()
        sheet = book.active
        sheet.title = "积分消耗明细"
        sheet.append(headers)
        for row in self.rows:
            sheet.append(row)
        book.save(self.source)

    def test_observed_debits_and_zero_cost_are_distinct(self):
        result = read_workbuddy(self.source, "+08:00")
        self.assertEqual(result.summary["rows"], 3)
        self.assertEqual(result.summary["charged_requests"], 2)
        self.assertEqual(result.summary["zero_credit_requests"], 1)
        self.assertEqual(result.summary["total_credits"], "1.20")
        self.assertEqual(len(result.events), 2)
        self.assertEqual(result.events[0]["occurred_at"], "2026-11-06T11:18:22Z")
        self.assertEqual(result.events[0]["delta_credits"], "-1.19")
        self.assertNotIn("private-id", json.dumps(result.summary) + str(result.events))
        self.assertIn("token usage", " ".join(result.summary["unavailable_checks"]))

    def test_duplicate_request_id_rejected_without_echoing_id(self):
        self.rows[2][0] = self.rows[0][0]
        self.write_book()
        with self.assertRaisesRegex(InputError, "duplicate RequestID") as caught:
            read_workbuddy(self.source, "+08:00")
        self.assertNotIn("private-id", str(caught.exception))

    def test_schema_drift_rejected(self):
        self.write_book(["RequestID", "积分消耗", "模型", "使用端", "日期"])
        with self.assertRaisesRegex(InputError, "header"):
            read_workbuddy(self.source, "+08:00")

    def test_negative_or_formula_credit_rejected(self):
        self.rows[0][1] = "-1"
        self.write_book()
        with self.assertRaisesRegex(InputError, "negative"):
            read_workbuddy(self.source, "+08:00")
        self.rows[0][1] = "=1+1"
        self.write_book()
        with self.assertRaisesRegex(InputError, "formula"):
            read_workbuddy(self.source, "+08:00")

    def test_explicit_offset_required_and_validated(self):
        with self.assertRaisesRegex(InputError, "UTC offset"):
            read_workbuddy(self.source, "Asia/Shanghai")

    def test_cli_exports_private_normalized_events_without_request_ids(self):
        output = self.root / "private-output"
        result = subprocess.run([sys.executable, "-m", "credit_receipt", "workbuddy-import",
                                 str(self.source), "--utc-offset", "+08:00", "--output-dir", str(output)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with (output / "events.csv").open(encoding="utf-8", newline="") as handle:
            events = list(csv.DictReader(handle))
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(len(events), 2)
        self.assertEqual(summary["total_credits"], "1.20")
        self.assertNotIn("private-id", (output / "events.csv").read_text(encoding="utf-8"))
        self.assertEqual(events[0]["kind"], "debit")


if __name__ == "__main__":
    unittest.main()
