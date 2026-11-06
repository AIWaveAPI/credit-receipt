"""Command-line interface for local, evidence-backed reconciliation."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from .audit import audit
from .input import InputError, load_bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="credit-receipt",
        description="Audit user-supplied credit records locally; differences are not allegations.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    audit_command = commands.add_parser("audit", help="reconcile balances and per-task credit debits")
    audit_command.add_argument("directory", type=Path, help="directory containing canonical input files")
    audit_command.add_argument("--output", type=Path, help="write detailed local JSON report")
    args = parser.parse_args(argv)
    try:
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
    except (InputError, OSError) as exc:
        print(f"credit-receipt: {exc}", file=sys.stderr)
        return 2
