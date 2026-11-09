"""Strict, offline parsing of user-owned credit records."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_ROWS = 100_000
MAX_RULES = 2_000


class InputError(ValueError):
    """Input cannot be audited without inventing missing information."""


@dataclass(frozen=True)
class Event:
    event_id: str
    occurred_at: datetime
    kind: str
    delta: Decimal
    source_ref: str
    row: int
    task_id: str = ""
    lot_id: str = ""
    paid_amount: Decimal | None = None
    paid_currency: str = ""


@dataclass(frozen=True)
class Usage:
    usage_id: str
    event_id: str
    model: str
    operation: str
    quantity: Decimal
    unit: str
    source_ref: str
    row: int


@dataclass(frozen=True)
class Rule:
    model: str
    operation: str
    unit: str
    rate: Decimal
    start: datetime
    end: datetime | None
    rounding: str
    source_url: str
    verified_at: date
    row: int


@dataclass(frozen=True)
class Snapshot:
    at: datetime
    balance: Decimal
    source_ref: str
    row: int


@dataclass(frozen=True)
class Bundle:
    provider: str
    opening_at: datetime
    opening_balance: Decimal
    events: tuple[Event, ...]
    usage: tuple[Usage, ...]
    rules: tuple[Rule, ...]
    snapshots: tuple[Snapshot, ...]
    hashes: dict[str, str]


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{label}: required nonempty text")
    return value.strip()


def _decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{label}: required decimal string")
    try:
        result = Decimal(value.strip())
    except InvalidOperation as exc:
        raise InputError(f"{label}: invalid decimal") from exc
    if not result.is_finite():
        raise InputError(f"{label}: decimal must be finite")
    parts = result.as_tuple()
    if len(parts.digits) > 30 or abs(parts.exponent) > 18:
        raise InputError(f"{label}: decimal out of supported range")
    return result


def _datetime(value: object, label: str) -> datetime:
    raw = _text(value, label)
    try:
        result = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    except ValueError as exc:
        raise InputError(f"{label}: invalid ISO 8601 timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise InputError(f"{label}: timezone offset required")
    return result.astimezone(timezone.utc)


def _read_source(path: Path) -> bytes:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise InputError(f"{path.name}: file exceeds 20 MiB limit")
        raw = path.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            raise InputError(f"{path.name}: file exceeds 20 MiB limit")
        return raw
    except InputError:
        raise
    except OSError as exc:
        raise InputError(f"{path.name}: cannot read file") from exc


def _json(path: Path, raw: bytes) -> object:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InputError(f"{path.name}: invalid UTF-8 JSON") from exc


def _csv(path: Path, raw: bytes, required: set[str], allowed: set[str]) -> list[tuple[int, dict[str, str]]]:
    try:
        with io.StringIO(raw.decode("utf-8-sig"), newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            if reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise InputError(f"{path.name}: missing or duplicate header")
            missing, extra = required - set(reader.fieldnames), set(reader.fieldnames) - allowed
            if missing or extra:
                raise InputError(f"{path.name}: missing={sorted(missing)} unexpected_count={len(extra)}")
            rows = []
            for number, row in enumerate(reader, start=2):
                if number > MAX_ROWS + 1:
                    raise InputError(f"{path.name}: more than {MAX_ROWS} rows")
                if None in row or any(value is None for value in row.values()):
                    raise InputError(f"{path.name}:{number}: malformed column count")
                rows.append((number, row))
            return rows
    except InputError:
        raise
    except (UnicodeError, csv.Error) as exc:
        raise InputError(f"{path.name}: invalid UTF-8 CSV") from exc


def _unique(rows: list[tuple[int, dict[str, str]]], key: str, filename: str) -> None:
    seen: set[str] = set()
    for number, row in rows:
        value = _text(row.get(key), f"{filename}:{number} {key}")
        if value in seen:
            raise InputError(f"{filename}:{number}: duplicate {key}")
        seen.add(value)


def load_bundle(directory: str | Path) -> Bundle:
    """Load canonical files without a network request or credential access."""
    root = Path(directory)
    if not root.is_dir():
        raise InputError("input directory does not exist")
    files = ["opening.json", "events.csv", "usage.csv", "rules.json"]
    if (root / "snapshots.csv").exists():
        files.append("snapshots.csv")
    sources = {name: _read_source(root / name) for name in files}
    opening = _json(root / "opening.json", sources["opening.json"])
    if not isinstance(opening, dict) or set(opening) != {"provider", "as_of", "balance_credits"}:
        raise InputError("opening.json: expected provider, as_of, balance_credits")
    provider = _text(opening["provider"], "opening.json provider")
    opening_at = _datetime(opening["as_of"], "opening.json as_of")
    opening_balance = _decimal(opening["balance_credits"], "opening.json balance_credits")
    if opening_balance < 0:
        raise InputError("opening.json: balance cannot be negative")

    event_required = {"event_id", "occurred_at", "kind", "delta_credits", "source_ref"}
    event_rows = _csv(root / "events.csv", sources["events.csv"], event_required,
                      event_required | {"task_id", "lot_id", "paid_amount", "paid_currency"})
    _unique(event_rows, "event_id", "events.csv")
    signs = {"purchase": 1, "grant": 1, "refund": 1, "debit": -1, "expiry": -1, "adjustment": 0}
    events = []
    for number, row in event_rows:
        label = f"events.csv:{number}"
        kind = _text(row["kind"], f"{label} kind")
        if kind not in signs:
            raise InputError(f"{label}: unsupported kind")
        delta = _decimal(row["delta_credits"], f"{label} delta_credits")
        if delta == 0 or signs[kind] * delta < 0:
            direction = "positive" if signs[kind] == 1 else "negative" if signs[kind] == -1 else "nonzero"
            raise InputError(f"{label}: {kind} needs {direction} delta_credits")
        at = _datetime(row["occurred_at"], f"{label} occurred_at")
        if at <= opening_at:
            raise InputError(f"{label}: event must follow opening as_of")
        paid_raw = row.get("paid_amount", "").strip()
        paid = _decimal(paid_raw, f"{label} paid_amount") if paid_raw else None
        currency = row.get("paid_currency", "").strip()
        if paid is not None and (kind != "purchase" or paid <= 0 or len(currency) != 3
                                 or not currency.isascii() or not currency.isalpha() or currency != currency.upper()):
            raise InputError(f"{label}: paid_amount requires positive purchase and uppercase three-letter currency")
        if currency and paid is None:
            raise InputError(f"{label}: paid_currency without paid_amount")
        events.append(Event(_text(row["event_id"], f"{label} event_id"), at, kind, delta,
                            _text(row["source_ref"], f"{label} source_ref"), number,
                            row.get("task_id", "").strip(), row.get("lot_id", "").strip(), paid, currency))

    usage_required = {"usage_id", "event_id", "model", "operation", "quantity", "unit", "source_ref"}
    usage_rows = _csv(root / "usage.csv", sources["usage.csv"], usage_required, usage_required)
    _unique(usage_rows, "usage_id", "usage.csv")
    debit_ids = {event.event_id for event in events if event.kind == "debit"}
    usage = []
    for number, row in usage_rows:
        label = f"usage.csv:{number}"
        event_id = _text(row["event_id"], f"{label} event_id")
        if event_id not in debit_ids:
            raise InputError(f"{label}: usage references unknown debit")
        quantity = _decimal(row["quantity"], f"{label} quantity")
        if quantity <= 0:
            raise InputError(f"{label}: quantity must be positive")
        usage.append(Usage(_text(row["usage_id"], f"{label} usage_id"), event_id,
                           _text(row["model"], f"{label} model"), _text(row["operation"], f"{label} operation"),
                           quantity, _text(row["unit"], f"{label} unit"),
                           _text(row["source_ref"], f"{label} source_ref"), number))

    rule_data = _json(root / "rules.json", sources["rules.json"])
    if not isinstance(rule_data, list):
        raise InputError("rules.json: expected array")
    if len(rule_data) > MAX_RULES:
        raise InputError(f"rules.json: more than {MAX_RULES} rules")
    required = {"model", "operation", "unit", "credits_per_unit", "effective_from", "rounding", "source_url", "verified_at"}
    rules = []
    for number, row in enumerate(rule_data, start=1):
        label = f"rules.json:{number}"
        if not isinstance(row, dict) or not required <= set(row) or set(row) - (required | {"effective_to"}):
            raise InputError(f"{label}: missing or unexpected fields")
        start = _datetime(row["effective_from"], f"{label} effective_from")
        end = _datetime(row["effective_to"], f"{label} effective_to") if row.get("effective_to") else None
        if end is not None and end <= start:
            raise InputError(f"{label}: effective_to must follow effective_from")
        rate = _decimal(row["credits_per_unit"], f"{label} credits_per_unit")
        if rate <= 0:
            raise InputError(f"{label}: credits_per_unit must be positive")
        rounding = _text(row["rounding"], f"{label} rounding")
        if rounding not in {"exact", "ceil", "floor"}:
            raise InputError(f"{label}: unsupported rounding")
        source_url = _text(row["source_url"], f"{label} source_url")
        if not source_url.startswith("https://"):
            raise InputError(f"{label}: source_url must use HTTPS")
        try:
            verified_at = date.fromisoformat(_text(row["verified_at"], f"{label} verified_at"))
        except ValueError as exc:
            raise InputError(f"{label}: invalid verified_at") from exc
        rules.append(Rule(_text(row["model"], f"{label} model"), _text(row["operation"], f"{label} operation"),
                          _text(row["unit"], f"{label} unit"), rate, start, end, rounding,
                          source_url, verified_at, number))
    infinity = datetime.max.replace(tzinfo=timezone.utc)
    for index, left in enumerate(rules):
        for right in rules[index + 1:]:
            if (left.model, left.operation, left.unit) == (right.model, right.operation, right.unit):
                if left.start < (right.end or infinity) and right.start < (left.end or infinity):
                    raise InputError(f"rules.json: overlap between rules {left.row} and {right.row}")

    snapshots = []
    if (root / "snapshots.csv").exists():
        fields = {"snapshot_at", "balance_credits", "source_ref"}
        for number, row in _csv(root / "snapshots.csv", sources["snapshots.csv"], fields, fields):
            label = f"snapshots.csv:{number}"
            at = _datetime(row["snapshot_at"], f"{label} snapshot_at")
            balance = _decimal(row["balance_credits"], f"{label} balance_credits")
            if at < opening_at or balance < 0:
                raise InputError(f"{label}: snapshot predates opening or has negative balance")
            snapshots.append(Snapshot(at, balance, _text(row["source_ref"], f"{label} source_ref"), number))

    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in sources.items()}
    return Bundle(provider, opening_at, opening_balance, tuple(events), tuple(usage), tuple(rules), tuple(snapshots), hashes)
