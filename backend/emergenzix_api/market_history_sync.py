"""Operator CLI for immutable BTC/BNB 4h research dataset synchronization."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from .config import Settings
from .database import Database
from .dataset_store import persist_dataset
from .market_history import SYMBOLS, build_binance_verified_history
from .schemas import DatasetCreate


def sync_symbol(*, db: Database, symbol: str, start_at: datetime) -> dict[str, object]:
    history = build_binance_verified_history(symbol=symbol, start_at=start_at)
    payload = DatasetCreate(
        source="binance_verified_history",
        exchange="binance",
        symbol=str(history["symbol"]),
        timeframe="4h",
        bars=history["bars"],
    )
    row, inserted = persist_dataset(db, payload)
    return {
        "symbol": row["symbol"], "dataset_id": row["id"],
        "bars_count": int(row["bars_count"]), "start_at": row["start_at"],
        "end_at": row["end_at"], "inserted": inserted,
        "forming_candle_excluded": history["forming_candle_excluded"],
        "archive_checksum_verified": history["archive_checksum_verified"],
        "public_tail_reconciled": history["public_tail_reconciled"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync verified Binance 4h research histories")
    parser.add_argument("--symbols", nargs="+", choices=sorted(SYMBOLS), default=sorted(SYMBOLS))
    parser.add_argument("--start", default="2021-01-01T00:00:00Z")
    parser.add_argument("--db-path", default=Settings.from_env().db_path)
    args = parser.parse_args()
    start = datetime.fromisoformat(args.start.replace("Z", "+00:00"))
    if start.tzinfo is None or start.utcoffset() is None:
        raise SystemExit("--start must include a timezone offset")
    db = Database(args.db_path)
    reports = [sync_symbol(db=db, symbol=symbol, start_at=start.astimezone(timezone.utc)) for symbol in args.symbols]
    print(json.dumps({"execution_mode": "RESEARCH_ONLY", "datasets": reports}, indent=2))


if __name__ == "__main__":
    main()
