import csv
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import credit_receipt.input as input_module
from credit_receipt.audit import audit
from credit_receipt.cli import main
from credit_receipt.input import InputError, load_bundle


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "opening.json").write_text(json.dumps({"provider": "example", "as_of": "2026-11-06T00:00:00Z", "balance_credits": "10"}), encoding="utf-8")
        self.events = [
            {"event_id": "buy-1", "occurred_at": "2026-11-06T01:00:00Z", "kind": "purchase", "delta_credits": "100", "source_ref": "invoice-a", "task_id": "", "lot_id": "lot-a", "paid_amount": "3.00", "paid_currency": "USD"},
            {"event_id": "task-1", "occurred_at": "2026-11-06T02:00:00Z", "kind": "debit", "delta_credits": "-60", "source_ref": "usage-a", "task_id": "task-1", "lot_id": "", "paid_amount": "", "paid_currency": ""},
        ]
        self.usage = [{"usage_id": "u1", "event_id": "task-1", "model": "video-a", "operation": "generate", "quantity": "5", "unit": "second", "source_ref": "usage-a"}]
        self.rules = [{"model": "video-a", "operation": "generate", "unit": "second", "credits_per_unit": "12", "effective_from": "2026-11-06T00:00:00Z", "rounding": "exact", "source_url": "https://example.org/rules", "verified_at": "2026-11-06"}]
        self.save()

    def save(self):
        write_csv(self.root / "events.csv", ["event_id", "occurred_at", "kind", "delta_credits", "source_ref", "task_id", "lot_id", "paid_amount", "paid_currency"], self.events)
        write_csv(self.root / "usage.csv", ["usage_id", "event_id", "model", "operation", "quantity", "unit", "source_ref"], self.usage)
        (self.root / "rules.json").write_text(json.dumps(self.rules), encoding="utf-8")

    def test_exact_balance_and_per_debit_rule(self):
        report = audit(load_bundle(self.root))
        self.assertEqual(report["computed_closing_balance_credits"], "50")
        self.assertEqual(report["debits"][0]["status"], "matched")
        self.assertEqual(report["debits"][0]["expected_credits"], "60")
        self.assertEqual(report["purchases"][0]["paid_per_credit"], "0.03")
        self.assertIn("events.csv", report["input_sha256"])

    def test_multiple_usage_lines_sum_before_comparing_debit(self):
        self.usage.append({"usage_id": "u2", "event_id": "task-1", "model": "video-a", "operation": "generate", "quantity": "1", "unit": "second", "source_ref": "usage-b"})
        self.events[1]["delta_credits"] = "-72"
        self.save()
        self.assertEqual(audit(load_bundle(self.root))["debits"][0]["status"], "matched")

    def test_wrong_debit_is_difference_not_accusation(self):
        self.events[1]["delta_credits"] = "-61"
        self.save()
        result = audit(load_bundle(self.root))["debits"][0]
        self.assertEqual((result["status"], result["difference_credits"]), ("difference", "1"))

    def test_missing_usage_is_insufficient_data(self):
        self.usage = []
        self.save()
        result = audit(load_bundle(self.root))["debits"][0]
        self.assertEqual(result["status"], "insufficient_data")
        self.assertIsNone(result["expected_credits"])

    def test_rule_version_is_selected_by_event_time(self):
        self.rules[0]["effective_to"] = "2026-11-06T01:30:00Z"
        self.rules.append({**self.rules[0], "credits_per_unit": "12", "effective_from": "2026-11-06T01:30:00Z", "effective_to": "2026-11-07T00:00:00Z"})
        self.save()
        self.assertEqual(audit(load_bundle(self.root))["debits"][0]["status"], "matched")

    def test_overlapping_rules_fail_closed(self):
        self.rules.append({**self.rules[0], "credits_per_unit": "13"})
        self.save()
        with self.assertRaisesRegex(InputError, "overlap"):
            load_bundle(self.root)

    def test_duplicate_event_id_fails_closed(self):
        self.events.append({**self.events[0]})
        self.save()
        with self.assertRaisesRegex(InputError, "duplicate event_id"):
            load_bundle(self.root)

    def test_duplicate_event_id_error_does_not_print_private_value(self):
        private = "private-customer-event-identifier"
        self.events[0]["event_id"] = private
        self.events.append({**self.events[0]})
        self.save()
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = main(["audit", str(self.root)])
        self.assertEqual(result, 2)
        self.assertIn("events.csv:4: duplicate event_id", stderr.getvalue())
        self.assertNotIn(private, stderr.getvalue())

    def test_unexpected_csv_header_does_not_print_private_value(self):
        private = "private-customer-header-value"
        path = self.root / "events.csv"
        contents = path.read_text(encoding="utf-8")
        path.write_text(contents.replace("source_ref,task_id", f"{private},task_id", 1), encoding="utf-8")
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = main(["audit", str(self.root)])
        self.assertEqual(result, 2)
        self.assertIn("events.csv", stderr.getvalue())
        self.assertNotIn(private, stderr.getvalue())

    def test_naive_datetime_fails_closed(self):
        self.events[0]["occurred_at"] = "2026-11-06T01:00:00"
        self.save()
        with self.assertRaisesRegex(InputError, "timezone"):
            load_bundle(self.root)

    def test_invalid_debit_sign_fails_closed(self):
        self.events[1]["delta_credits"] = "60"
        self.save()
        with self.assertRaisesRegex(InputError, "negative"):
            load_bundle(self.root)

    def test_snapshot_difference_and_equal_timestamp_ambiguity(self):
        write_csv(self.root / "snapshots.csv", ["snapshot_at", "balance_credits", "source_ref"], [
            {"snapshot_at": "2026-11-06T01:30:00Z", "balance_credits": "110", "source_ref": "screen-a"},
            {"snapshot_at": "2026-11-06T03:00:00Z", "balance_credits": "49", "source_ref": "screen-b"},
            {"snapshot_at": "2026-11-06T02:00:00Z", "balance_credits": "50", "source_ref": "screen-c"},
        ])
        statuses = [s["status"] for s in audit(load_bundle(self.root))["snapshots"]]
        self.assertEqual(statuses, ["matched", "difference", "ambiguous"])

    def test_unmatched_usage_reference_fails_closed(self):
        self.usage[0]["event_id"] = "other"
        self.save()
        with self.assertRaisesRegex(InputError, "unknown debit"):
            load_bundle(self.root)

    def test_unknown_usage_reference_error_does_not_print_private_value(self):
        private = "private-customer-task-identifier"
        self.usage[0]["event_id"] = private
        self.save()
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = main(["audit", str(self.root)])
        self.assertEqual(result, 2)
        self.assertIn("usage.csv:2: usage references unknown debit", stderr.getvalue())
        self.assertNotIn(private, stderr.getvalue())

    def test_decimal_precision_has_no_binary_float_error(self):
        self.rules[0]["credits_per_unit"] = "0.1"
        self.usage[0]["quantity"] = "3"
        self.events[1]["delta_credits"] = "-0.3"
        self.save()
        self.assertEqual(audit(load_bundle(self.root))["debits"][0]["expected_credits"], "0.3")

    def test_cli_writes_reproducible_local_report(self):
        write_csv(self.root / "snapshots.csv", ["snapshot_at", "balance_credits", "source_ref"], [
            {"snapshot_at": "2026-11-06T03:00:00Z", "balance_credits": "50", "source_ref": "screen-a"}
        ])
        output = self.root / "report.json"
        result = subprocess.run([sys.executable, "-m", "credit_receipt", "audit", str(self.root),
                                 "--output", str(output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "reconciled")
        self.assertEqual(report["input_sha256"]["events.csv"], load_bundle(self.root).hashes["events.csv"])
        if os.name == "nt":
            acl = subprocess.run(["icacls", str(output)], capture_output=True,
                                 text=True, check=True).stdout
            self.assertNotIn("(I)", acl, "report must not inherit broad ACLs")
        else:
            self.assertEqual(stat.S_IMODE(output.stat().st_mode) & 0o077, 0,
                             "report must not allow group or other access")

    def test_report_permission_failure_preserves_existing_output(self):
        output = self.root / "report.json"
        original = b"existing private report\n"
        output.write_bytes(original)
        with patch("credit_receipt.cli._secure_private_path",
                   side_effect=InputError("cannot secure private report")):
            with patch("sys.stderr"):
                code = main(["audit", str(self.root), "--output", str(output)])
        self.assertEqual(code, 2)
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(list(self.root.glob(".credit-receipt-*.json")), [])

    def test_cli_difference_exit_code(self):
        self.events[1]["delta_credits"] = "-61"
        self.save()
        result = subprocess.run([sys.executable, "-m", "credit_receipt", "audit", str(self.root)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("differences_found", result.stdout)

    def test_cli_incomplete_exit_code(self):
        result = subprocess.run([sys.executable, "-m", "credit_receipt", "audit", str(self.root)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 3, result.stderr)

    def test_cli_invalid_input_exit_code(self):
        self.events[1]["delta_credits"] = "60"
        self.save()
        result = subprocess.run([sys.executable, "-m", "credit_receipt", "audit", str(self.root)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("negative", result.stderr)

    def test_snapshot_before_last_event_cannot_close_the_ledger(self):
        write_csv(self.root / "snapshots.csv", ["snapshot_at", "balance_credits", "source_ref"], [
            {"snapshot_at": "2026-11-06T01:30:00Z", "balance_credits": "110", "source_ref": "screen-a"}
        ])
        report = audit(load_bundle(self.root))
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["balance_status"], "no_closing_snapshot")

    def test_rule_verified_after_transaction_is_not_back_applied(self):
        self.rules[0]["verified_at"] = "2026-11-07"
        self.save()
        receipt = audit(load_bundle(self.root))["debits"][0]
        self.assertEqual(receipt["status"], "insufficient_data")
        self.assertEqual(receipt["usage_lines"][0]["status"], "rule_verified_after_event")

    def test_missing_rule_does_not_create_zero_expected_cost(self):
        self.rules = []
        self.save()
        receipt = audit(load_bundle(self.root))["debits"][0]
        self.assertIsNone(receipt["expected_credits"])
        self.assertEqual(receipt["status"], "insufficient_data")

    def test_purchase_rate_never_becomes_task_cash_cost(self):
        receipt = audit(load_bundle(self.root))
        self.assertEqual(receipt["purchases"][0]["paid_per_credit"], "0.03")
        self.assertNotIn("cash_cost", receipt["debits"][0])

    def test_nonterminating_purchase_rate_is_reported_as_exact_fraction(self):
        self.events[0]["delta_credits"] = "3"
        self.events[0]["paid_amount"] = "1"
        self.save()
        purchase = audit(load_bundle(self.root))["purchases"][0]
        self.assertEqual(purchase["paid_per_credit_fraction"], "1/3")
        self.assertIsNone(purchase["paid_per_credit"])

    def test_large_exact_values_do_not_round_at_default_decimal_precision(self):
        self.rules[0]["credits_per_unit"] = "12345678901234567890123456789"
        self.usage[0]["quantity"] = "3"
        self.events[1]["delta_credits"] = "-37037036703703703670370370367"
        self.save()
        receipt = audit(load_bundle(self.root))["debits"][0]
        self.assertEqual(receipt["expected_credits"], "37037036703703703670370370367")
        self.assertEqual(receipt["status"], "matched")

    def test_extreme_exponent_is_rejected_before_report_generation(self):
        self.events[0]["delta_credits"] = "1E+999999"
        self.save()
        with self.assertRaisesRegex(InputError, "out of supported range"):
            load_bundle(self.root)

    def test_hash_identifies_exact_bytes_that_were_parsed(self):
        event_file = self.root / "events.csv"
        original = event_file.read_bytes()
        real_reader = input_module._read_source

        def mutate_after_read(path):
            raw = real_reader(path)
            if path.name == "events.csv":
                event_file.write_bytes(original.replace(b"-60", b"-61"))
            return raw

        with patch.object(input_module, "_read_source", side_effect=mutate_after_read):
            bundle = load_bundle(self.root)
        self.assertEqual(bundle.events[1].delta, -60)
        self.assertEqual(bundle.hashes["events.csv"], hashlib.sha256(original).hexdigest())


if __name__ == "__main__":
    unittest.main()
