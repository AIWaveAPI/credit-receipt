"""Import observed WorkBuddy credits from a user-owned XLSX export.

The export has no token counts, rate rules, grant/expiry movements, or balance.
This module therefore records debits; it does not manufacture a full audit.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .input import InputError, MAX_FILE_BYTES, MAX_ROWS

HEADERS = ("RequestID", "积分消耗", "模型", "使用端", "时间")
EVENT_FIELDS = ("event_id", "occurred_at", "kind", "delta_credits", "source_ref",
                "task_id", "lot_id", "paid_amount", "paid_currency")
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_CELL_CHARS = 1024


@dataclass(frozen=True)
class WorkBuddyImport:
    summary: dict
    events: tuple[dict[str, str], ...]


def _offset(value: str) -> timezone:
    match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", value)
    if not match:
        raise InputError("UTC offset required in +HH:MM or -HH:MM form")
    hours, minutes = int(match[2]), int(match[3])
    if hours > 14 or minutes > 59 or (hours == 14 and minutes):
        raise InputError("UTC offset is out of range")
    duration = timedelta(hours=hours, minutes=minutes)
    if match[1] == "-":
        duration = -duration
    return timezone(duration)


def _cell(value: object, row: int, column: str) -> str:
    if value is None or isinstance(value, bool):
        raise InputError(f"row {row}: missing or invalid {column}")
    text = str(value).strip()
    if not text or len(text) > MAX_CELL_CHARS:
        raise InputError(f"row {row}: missing or oversized {column}")
    return text


def _wall_time_to_utc(local_time: datetime, zone: timezone | ZoneInfo, row_number: int) -> datetime:
    if isinstance(zone, ZoneInfo):
        first = local_time.replace(tzinfo=zone, fold=0)
        second = local_time.replace(tzinfo=zone, fold=1)
        first_valid = first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == local_time
        second_valid = second.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == local_time
        if not first_valid and not second_valid:
            raise InputError(f"row {row_number}: nonexistent wall time in selected time zone")
        if first_valid and second_valid and first.utcoffset() != second.utcoffset():
            raise InputError(f"row {row_number}: ambiguous wall time at daylight-saving transition")
    return local_time.replace(tzinfo=zone).astimezone(timezone.utc)


def read_workbuddy(path: str | Path, utc_offset: str | None = None, *,
                   time_zone: str | None = None) -> WorkBuddyImport:
    """Read a five-column export without retaining request IDs or task text."""
    if (utc_offset is None) == (time_zone is None):
        raise InputError("choose exactly one of UTC offset or IANA time zone")
    if time_zone is not None:
        try:
            zone = ZoneInfo(time_zone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise InputError("time zone unavailable; use an IANA name and install tzdata") from exc
    else:
        zone = _offset(utc_offset)
    source = Path(path)
    if source.suffix.lower() != ".xlsx":
        raise InputError("WorkBuddy import requires an .xlsx export")
    try:
        if source.stat().st_size > MAX_FILE_BYTES:
            raise InputError("XLSX exceeds 20 MiB limit")
        raw = source.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            raise InputError("XLSX exceeds 20 MiB limit")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_UNCOMPRESSED_BYTES:
                raise InputError("XLSX exceeds 100 MiB expanded-size limit")
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(raw), read_only=True, data_only=False, keep_links=False)
    except ImportError as exc:
        raise InputError("XLSX support requires: pip install 'credit-receipt[xlsx]'") from exc
    except InputError:
        raise
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        raise InputError("cannot read WorkBuddy XLSX") from exc

    try:
        if len(book.worksheets) != 1:
            raise InputError("WorkBuddy export must contain exactly one worksheet")
        sheet = book.active
        rows = sheet.iter_rows()
        try:
            header = tuple(cell.value for cell in next(rows))
        except StopIteration as exc:
            raise InputError("WorkBuddy XLSX has no header") from exc
        if header != HEADERS:
            raise InputError("WorkBuddy XLSX header does not match the verified five-column schema")

        source_hash = hashlib.sha256(raw).hexdigest()
        seen_ids: set[str] = set()
        events = []
        totals: dict[str, Decimal] = {}
        count = positive = zero = 0
        total = Decimal(0)
        first = last = None
        for row_number, cells in enumerate(rows, start=2):
            if row_number > MAX_ROWS + 1:
                raise InputError(f"WorkBuddy XLSX exceeds {MAX_ROWS} rows")
            if len(cells) != 5:
                raise InputError(f"row {row_number}: expected five cells")
            if any(cell.data_type == "f" for cell in cells):
                raise InputError(f"row {row_number}: formula cells are not accepted")
            request_id, amount, model, client, timestamp = (
                _cell(cell.value, row_number, HEADERS[index]) for index, cell in enumerate(cells)
            )
            if request_id in seen_ids:
                raise InputError(f"row {row_number}: duplicate RequestID")
            seen_ids.add(request_id)
            try:
                credits = Decimal(amount)
            except InvalidOperation as exc:
                raise InputError(f"row {row_number}: invalid credit amount") from exc
            if not credits.is_finite() or credits.as_tuple().exponent < -8 or credits >= Decimal("1e12"):
                raise InputError(f"row {row_number}: credit amount out of range")
            if credits < 0:
                raise InputError(f"row {row_number}: negative credit amount")
            try:
                local_time = (cells[4].value if isinstance(cells[4].value, datetime)
                              else datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S"))
            except ValueError as exc:
                raise InputError(f"row {row_number}: invalid timestamp") from exc
            if local_time.tzinfo is not None:
                raise InputError(f"row {row_number}: export timestamp must be timezone-naive")
            utc_time = _wall_time_to_utc(local_time, zone, row_number)
            first = min(first, local_time) if first is not None else local_time
            last = max(last, local_time) if last is not None else local_time
            count += 1
            totals[model] = totals.get(model, Decimal(0)) + credits
            total += credits
            if credits == 0:
                zero += 1
                continue
            positive += 1
            events.append({
                "event_id": f"wb-{source_hash[:12]}-row-{row_number}",
                "occurred_at": utc_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "kind": "debit",
                "delta_credits": str(-credits),
                "source_ref": f"WorkBuddy XLSX row {row_number}",
                "task_id": "", "lot_id": "", "paid_amount": "", "paid_currency": "",
            })
        if count == 0:
            raise InputError("WorkBuddy XLSX has no usage rows")
        summary = {
            "provider": "WorkBuddy", "schema": "observed-request-usage-xlsx-v1",
            "source_sha256": source_hash,
            "time_zone_assumed": time_zone,
            "utc_offset_assumed": utc_offset,
            "source_wall_time_first": first.strftime("%Y-%m-%d %H:%M:%S"),
            "source_wall_time_last": last.strftime("%Y-%m-%d %H:%M:%S"),
            "rows": count, "charged_requests": positive, "zero_credit_requests": zero,
            "total_credits": str(total),
            "credits_by_model": {model: str(value) for model, value in sorted(totals.items())},
            "unavailable_checks": [
                "opening and closing balance (not in this export)",
                "grants, expiry and top-ups (not in this export)",
                "token usage and per-request pricing formula (not in this export)",
                "cash value of consumed credits (spending credit bucket unknown)",
            ],
            "note": "Observed provider charges only. An unmatched balance is not evidence of hidden billing.",
        }
        return WorkBuddyImport(summary, tuple(events))
    finally:
        book.close()
