import unittest
from pathlib import Path

from credit_receipt.audit import audit
from credit_receipt.input import load_bundle


class PublishedExampleTests(unittest.TestCase):
    def test_synthetic_example_can_be_recomputed(self):
        root = Path(__file__).resolve().parents[1] / "examples" / "synthetic-runway"
        report = audit(load_bundle(root))
        self.assertEqual(report["status"], "reconciled")
        self.assertEqual(report["computed_closing_balance_credits"], "820")
        self.assertEqual([entry["expected_credits"] for entry in report["debits"]], ["60", "120"])
        self.assertEqual(report["purchases"][0]["paid_per_credit"], "0.01")
