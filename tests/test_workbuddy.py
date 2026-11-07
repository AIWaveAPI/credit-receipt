import csv
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openpyxl import Workbook

from credit_receipt.input import InputError
from credit_receipt.cli import main
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

    def test_pacific_zone_applies_daylight_saving_transition(self):
        self.rows = [
            ["before", "1", "model-a", "WorkBuddy", "2026-10-31 12:00:00"],
            ["after", "1", "model-a", "WorkBuddy", "2026-11-02 12:00:00"],
        ]
        self.write_book()
        result = read_workbuddy(self.source, time_zone="America/Los_Angeles")
        self.assertEqual([x["occurred_at"] for x in result.events],
                         ["2026-10-31T19:00:00Z", "2026-11-02T20:00:00Z"])
        self.assertEqual(result.summary["time_zone_assumed"], "America/Los_Angeles")

    def test_ambiguous_pacific_wall_time_requires_more_evidence(self):
        self.rows[0][4] = "2026-11-01 01:30:00"
        self.write_book()
        with self.assertRaisesRegex(InputError, "ambiguous"):
            read_workbuddy(self.source, time_zone="America/Los_Angeles")

    def test_nonexistent_pacific_wall_time_rejected(self):
        self.rows[0][4] = "2026-03-08 02:30:00"
        self.write_book()
        with self.assertRaisesRegex(InputError, "nonexistent"):
            read_workbuddy(self.source, time_zone="America/Los_Angeles")

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
        if os.name == "nt":
            acl = subprocess.run(["icacls", str(output)], capture_output=True, text=True, check=True)
            self.assertNotIn("(I)", acl.stdout, "output directory must not inherit broad ACLs")
            current_user = subprocess.run(["whoami"], capture_output=True, text=True, check=True).stdout.strip()
            for name in ("events.csv", "summary.json"):
                path = output / name
                file_acl = subprocess.run(["icacls", str(path)], capture_output=True,
                                          text=True, check=True).stdout
                access_lines = [line.replace(str(path), "", 1).strip()
                                for line in file_acl.splitlines() if ":(" in line]
                self.assertEqual([line.split(":(", 1)[0].casefold() for line in access_lines],
                                 [current_user.casefold()])
        else:
            self.assertEqual(stat.S_IMODE(output.stat().st_mode) & 0o077, 0,
                             "output directory must not allow group or other access")
            for name in ("events.csv", "summary.json"):
                self.assertEqual(stat.S_IMODE((output / name).stat().st_mode) & 0o077, 0,
                                 f"{name} must not allow group or other access")

    def test_permission_failure_publishes_no_usage_files(self):
        output = self.root / "refused-output"
        with mock.patch("credit_receipt.cli._secure_private_path",
                        side_effect=InputError("cannot secure private output")):
            with mock.patch("sys.stderr"):
                code = main(["workbuddy-import", str(self.source), "--utc-offset", "+08:00",
                             "--output-dir", str(output)])
        self.assertEqual(code, 2)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
