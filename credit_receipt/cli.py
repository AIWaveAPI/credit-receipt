"""Command-line interface for local, evidence-backed reconciliation."""

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .audit import audit
from .input import InputError, load_bundle
from .workbuddy import EVENT_FIELDS, read_workbuddy


def _secure_private_path(path: Path, *, directory: bool) -> None:
    """Restrict a new path before writing private accounting facts into it."""
    if os.name != "nt":
        os.chmod(path, 0o700 if directory else 0o600)
        return
    try:
        user = subprocess.run(["whoami"], capture_output=True, text=True,
                              timeout=10, check=True).stdout.strip()
        if not user:
            raise InputError("cannot identify the local Windows account")
        grant = f"{user}:(OI)(CI)(F)" if directory else f"{user}:(F)"
        result = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", grant],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode:
            raise InputError("cannot secure the private output path")
    except (OSError, subprocess.SubprocessError) as exc:
        raise InputError("cannot secure the private output path") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="credit-receipt",
        description="Audit user-supplied credit records locally; differences are not allegations.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    audit_command = commands.add_parser("audit", help="reconcile balances and per-task credit debits")
    audit_command.add_argument("directory", type=Path, help="directory containing canonical input files")
    audit_command.add_argument("--output", type=Path, help="write detailed local JSON report")
    import_command = commands.add_parser("workbuddy-import", help="import a local WorkBuddy request-usage XLSX")
    import_command.add_argument("file", type=Path)
    time_group = import_command.add_mutually_exclusive_group(required=True)
    time_group.add_argument("--utc-offset", help="fixed source wall-clock offset, e.g. +08:00")
    time_group.add_argument("--time-zone", help="IANA time zone, e.g. America/Los_Angeles; handles DST")
    import_command.add_argument("--output-dir", required=True, type=Path, help="new private output directory")
    args = parser.parse_args(argv)
    try:
        if args.command == "workbuddy-import":
            imported = read_workbuddy(args.file, args.utc_offset, time_zone=args.time_zone)
            target = args.output_dir.resolve()
            if target.exists():
                raise InputError("output directory already exists; choose a new path")
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".credit-receipt-", dir=target.parent) as temporary:
                staging = Path(temporary) / "result"
                staging.mkdir()
                _secure_private_path(staging, directory=True)
                with (staging / "events.csv").open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=EVENT_FIELDS)
                    writer.writeheader()
                    writer.writerows(imported.events)
                (staging / "summary.json").write_text(
                    json.dumps(imported.summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
                )
                if os.name != "nt":
                    os.chmod(staging / "events.csv", 0o600)
                    os.chmod(staging / "summary.json", 0o600)
                os.replace(staging, target)
            print(f"observed_only: {imported.summary['charged_requests']} charged, "
                  f"{imported.summary['zero_credit_requests']} zero-credit requests; "
                  "balance, token usage and cash value unavailable")
            return 0
        bundle = load_bundle(args.directory)
        report = audit(bundle)
        if args.output:
            target = args.output.resolve()
            protected = {(args.directory / name).resolve() for name in bundle.hashes}
            if target in protected:
                raise InputError("output would overwrite an input file")
            target.parent.mkdir(parents=True, exist_ok=True)
            pending = None
            try:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent,
                                                 prefix=".credit-receipt-", suffix=".json",
                                                 delete=False) as handle:
                    pending = Path(handle.name)
                    _secure_private_path(pending, directory=False)
                    json.dump(report, handle, indent=2, ensure_ascii=False)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(pending, target)
            finally:
                if pending is not None and pending.exists():
                    pending.unlink()
        print(f"{report['status']}: {report['summary']['debits']} debits, "
              f"{report['summary']['differences']} differences, "
              f"{report['summary']['incomplete_checks']} incomplete checks")
        return {"reconciled": 0, "differences_found": 1, "incomplete": 3}[report["status"]]
    except InputError as exc:
        print(f"credit-receipt: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        code = exc.errno if exc.errno is not None else "unknown"
        print(f"credit-receipt: filesystem operation failed (errno {code}); check local paths and permissions",
              file=sys.stderr)
        return 2
