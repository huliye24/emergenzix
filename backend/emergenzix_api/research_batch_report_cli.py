"""Private CLI for verified read-only saved research batch reports."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Sequence

from .config import Settings
from .database import Database
from .research_batch_report import (
    BatchReportError, build_research_batch_report, write_batch_report,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read verified, independent historical batch observations"
    )
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
        db = Database(
            args.db_path or settings.db_path,
            initial_paper_cash=settings.initial_paper_cash,
        )
        report = build_research_batch_report(db, args.batch_id)
        if args.output:
            print(json.dumps(
                {"status": "ok", "snapshot_sha256": report["snapshot_sha256"],
                 "files": write_batch_report(report, args.output)},
                ensure_ascii=False, sort_keys=True,
            ))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    except (BatchReportError, OSError, sqlite3.Error, ValueError, TypeError):
        print(json.dumps(
            {"status": "error", "error_code": "batch_report_unavailable"}
        ), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
