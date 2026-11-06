"""Pure credit-balance and rule reconciliation; no provider verdicts."""

from bisect import bisect_right
from collections import defaultdict
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext
from fractions import Fraction

from .input import Bundle, Event, Rule


def _number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _rule_for(event: Event, model: str, operation: str, unit: str,
              rules: dict[tuple[str, str, str], list[Rule]]) -> Rule | None:
    matches = [rule for rule in rules.get((model, operation, unit), [])
               if rule.start <= event.occurred_at and (rule.end is None or event.occurred_at < rule.end)]
    return matches[0] if len(matches) == 1 else None


def _expected(quantity: Decimal, rule: Rule) -> Decimal:
    raw = quantity * rule.rate
    if rule.rounding == "ceil":
        return raw.to_integral_value(rounding=ROUND_CEILING)
    if rule.rounding == "floor":
        return raw.to_integral_value(rounding=ROUND_FLOOR)
    return raw


def _purchase_rate(paid: Decimal | None, credits: Decimal) -> tuple[str | None, str | None]:
    if paid is None:
        return None, None
    exact = Fraction(paid) / Fraction(credits)
    denominator = exact.denominator
    for factor in (2, 5):
        while denominator % factor == 0:
            denominator //= factor
    decimal_rate = _number(Decimal(exact.numerator) / Decimal(exact.denominator)) if denominator == 1 else None
    return decimal_rate, str(exact)


def audit(bundle: Bundle) -> dict:
    """Reconcile only what the supplied evidence can identify."""
    with localcontext() as context:
        context.prec = 80
        return _audit(bundle)


def _audit(bundle: Bundle) -> dict:
    by_event = defaultdict(list)
    for line in bundle.usage:
        by_event[line.event_id].append(line)
    rules_by_key = defaultdict(list)
    for rule in bundle.rules:
        rules_by_key[(rule.model, rule.operation, rule.unit)].append(rule)
    debits = []
    purchases = []
    event_receipts = []
    ordered_events = sorted(bundle.events, key=lambda item: (item.occurred_at, item.row))
    event_times = []
    running_balances = []
    running_balance = bundle.opening_balance
    for event in ordered_events:
        running_balance += event.delta
        event_times.append(event.occurred_at)
        running_balances.append(running_balance)
    for event in ordered_events:
        event_receipts.append({
            "event_id": event.event_id, "occurred_at": event.occurred_at.isoformat(),
            "kind": event.kind, "delta_credits": _number(event.delta),
            "source_ref": event.source_ref, "input_row": event.row,
            "task_id": event.task_id or None, "lot_id": event.lot_id or None,
        })
        if event.kind == "purchase":
            decimal_rate, fraction_rate = _purchase_rate(event.paid_amount, event.delta)
            purchases.append({
                "event_id": event.event_id, "credits": _number(event.delta),
                "paid_amount": _number(event.paid_amount) if event.paid_amount is not None else None,
                "paid_currency": event.paid_currency or None,
                "paid_per_credit": decimal_rate,
                "paid_per_credit_fraction": fraction_rate,
                "note": "purchase-lot rate only; no task cash allocation is inferred",
            })
        if event.kind != "debit":
            continue
        lines = by_event[event.event_id]
        details = []
        expected_total = Decimal(0)
        complete = bool(lines)
        for line in lines:
            rule = _rule_for(event, line.model, line.operation, line.unit, rules_by_key)
            if rule is None or rule.verified_at > event.occurred_at.date():
                complete = False
                details.append({
                    "usage_id": line.usage_id,
                    "status": "no_effective_rule" if rule is None else "rule_verified_after_event",
                    "model": line.model, "operation": line.operation, "unit": line.unit,
                    "quantity": _number(line.quantity), "source_ref": line.source_ref,
                    "input_row": line.row, "expected_credits": None,
                })
                continue
            amount = _expected(line.quantity, rule)
            expected_total += amount
            details.append({
                "usage_id": line.usage_id, "status": "calculated",
                "model": line.model, "operation": line.operation, "unit": line.unit,
                "quantity": _number(line.quantity), "source_ref": line.source_ref,
                "input_row": line.row, "expected_credits": _number(amount),
                "rule_row": rule.row, "rule_url": rule.source_url,
                "rule_verified_at": rule.verified_at.isoformat(),
            })
        observed = -event.delta
        difference = observed - expected_total if complete else None
        status = "insufficient_data" if not complete else "matched" if difference == 0 else "difference"
        debits.append({
            "event_id": event.event_id, "task_id": event.task_id or None,
            "occurred_at": event.occurred_at.isoformat(), "observed_credits": _number(observed),
            "expected_credits": _number(expected_total) if complete else None,
            "difference_credits": _number(difference) if difference is not None else None,
            "status": status, "source_ref": event.source_ref, "input_row": event.row,
            "usage_lines": details,
            "interpretation": "unexplained arithmetic difference; not proof of misconduct" if status == "difference" else None,
        })

    closing = running_balance
    snapshots = []
    event_time_set = set(event_times)
    for snapshot in bundle.snapshots:
        same_time = snapshot.at in event_time_set
        index = bisect_right(event_times, snapshot.at) - 1
        computed = running_balances[index] if index >= 0 else bundle.opening_balance
        difference = snapshot.balance - computed
        status = "ambiguous" if same_time else "matched" if difference == 0 else "difference"
        snapshots.append({
            "snapshot_at": snapshot.at.isoformat(), "observed_credits": _number(snapshot.balance),
            "computed_credits": _number(computed) if not same_time else None,
            "difference_credits": _number(difference) if not same_time else None,
            "status": status, "source_ref": snapshot.source_ref, "input_row": snapshot.row,
        })
    differences = sum(item["status"] == "difference" for item in debits + snapshots)
    incomplete = sum(item["status"] in {"insufficient_data", "ambiguous"} for item in debits + snapshots)
    latest_event = event_times[-1] if event_times else bundle.opening_at
    closing_snapshot = any(snapshot.at > latest_event for snapshot in bundle.snapshots)
    if not snapshots or not closing_snapshot:
        incomplete += 1
    status = "differences_found" if differences else "incomplete" if incomplete else "reconciled"
    return {
        "schema_version": 1, "provider": bundle.provider,
        "opening_at": bundle.opening_at.isoformat(), "opening_balance_credits": _number(bundle.opening_balance),
        "computed_closing_balance_credits": _number(closing),
        "balance_status": "no_provider_snapshot" if not snapshots else
                          "closing_snapshot_present" if closing_snapshot else "no_closing_snapshot",
        "status": status, "summary": {
            "events": len(bundle.events), "debits": len(debits), "snapshots": len(snapshots),
            "differences": differences, "incomplete_checks": incomplete,
        },
        "input_sha256": bundle.hashes, "events": event_receipts,
        "debits": debits, "snapshots": snapshots, "purchases": purchases,
        "limitations": [
            "User-supplied records may be incomplete or incorrectly transcribed.",
            "A difference is not evidence of hidden charges or misconduct.",
            "Purchase-lot cash rates do not value subscription or promotional credits.",
            "Credits do not determine token counts without original model and usage data.",
        ],
    }
