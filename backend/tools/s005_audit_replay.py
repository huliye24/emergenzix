"""S-005.A audit replay v2: real trades through the strict account.

Fixes over v1:
  * R comes from each trade's own engine risk (not a fixed 100 USDT denominator)
  * exits are processed in chronological order, so multiple positions can be
    genuinely open at once and the margin/rejection logic is actually exercised
"""

from __future__ import annotations

import csv
import pathlib
import statistics
import sys
from datetime import datetime, timezone

sys.path.insert(0, r"E:\NEMT Simulator\.cloud-work\emergenzix-pr-review\backend")

from emergenzix_api.nemt_strategy.s005_account import Account  # noqa: E402

SOURCE = pathlib.Path(r"C:\Users\Administrator\AppData\Local\Temp\trades_flat.csv")
CAPITAL = 10_000.0
BUDGET = 0.075
MMR = 0.010


def load(arm: str) -> list[dict]:
    rows = []
    for r in csv.DictReader(SOURCE.open(encoding="utf-8")):
        if r["arm"] != arm:
            continue
        rows.append({"symbol": r["symbol"],
                     "entry": datetime.fromisoformat(r["entry_ts"]),
                     "exit": datetime.fromisoformat(r["exit_ts"]),
                     "side": int(r["side"]),
                     "entry_price": float(r["entry_price"]),
                     "exit_price": float(r["exit_price"]),
                     "distance": float(r["distance"]),
                     "r_multiple": float(r["r_multiple"]),
                     "exit_reason": r["exit_reason"]})
    rows.sort(key=lambda r: r["entry"])
    return rows


def naive(trades: list[dict], f: float) -> dict:
    equity, peak, worst = CAPITAL, CAPITAL, 0.0
    for t in trades:
        equity *= (1 + f * t["r_multiple"])
        peak = max(peak, equity)
        worst = max(worst, (peak - equity) / peak)
    return {"final": round(equity, 2), "dd": round(worst * 100, 1)}


def audited(trades: list[dict], f: float) -> dict:
    account = Account(initial_capital=CAPITAL, leverage=3.0, risk_budget=BUDGET)
    marks: dict[str, float] = {}
    pending: dict[str, dict] = {}          # symbol -> the trade record currently open
    taken = skipped = liquidated = 0
    max_seen = 0
    peak = CAPITAL
    worst_dd = 0.0

    def close_due(moment: datetime) -> None:
        nonlocal peak, worst_dd, liquidated
        for symbol in list(pending):
            trade = pending[symbol]
            if trade["exit"] > moment:
                continue
            marks[symbol] = trade["exit_price"]
            if account.mark(marks, MMR):
                liquidated += 1
            if symbol in account.positions:
                account.close(symbol=symbol, price=trade["exit_price"],
                              reason=trade["exit_reason"], marks=marks)
            pending.pop(symbol, None)
            equity = account.equity(marks)
            peak = max(peak, equity)
            worst_dd = max(worst_dd, (peak - equity) / peak)

    for trade in trades:
        close_due(trade["entry"])
        equity = account.equity(marks) if marks else account.free_cash
        if equity <= 0:
            break
        if trade["symbol"] in pending:
            skipped += 1
            continue
        marks[trade["symbol"]] = trade["entry_price"]
        risk_usdt = f * equity
        ok, reason = account.open(
            symbol=trade["symbol"], side=trade["side"], price=trade["entry_price"],
            stop_price=trade["entry_price"] - trade["side"] * trade["distance"],
            risk_usdt=risk_usdt, marks=marks, mmr=MMR)
        if not ok:
            skipped += 1
            continue
        taken += 1
        pending[trade["symbol"]] = trade
        max_seen = max(max_seen, len(account.positions))

    close_due(datetime.max.replace(tzinfo=timezone.utc))
    final = account.equity(marks) if marks else account.free_cash
    return {"final": round(final, 2), "taken": taken, "skipped": skipped,
            "liquidations": liquidated, "max_concurrent": max_seen,
            "rejections": dict(account.rejections),
            "fees": round(account.fees_paid, 2),
            "dd": round(worst_dd * 100, 1)}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    for arm in ("L1", "R0"):
        trades = load(arm)
        total_r = sum(t["r_multiple"] for t in trades)
        print(f"\n=== {arm}: {len(trades)} trades, sum R = {total_r:+.1f} ===")
        print(f"{'risk':>6s} {'naive':>12s} {'audited':>12s} {'a/n':>7s} {'taken':>6s} "
              f"{'skip':>5s} {'liq':>4s} {'maxc':>5s}  rejections")
        for f in (0.01, 0.02, 0.05, 0.075):
            n = naive(trades, f)
            a = audited(trades, f)
            ratio = a["final"] / n["final"] if n["final"] else float("nan")
            print(f"{f*100:5.1f}% {n['final']:12,.0f} {a['final']:12,.0f} {ratio:7.3f} "
                  f"{a['taken']:6d} {a['skipped']:5d} {a['liquidations']:4d} "
                  f"{a['max_concurrent']:5d}  {a['rejections']}")

    print("\n=== margin capacity at 3x leverage (2.5% stop distance) ===")
    for f in (0.01, 0.02, 0.05, 0.075):
        margin_per_pos = (f / 0.025) / 3.0
        print(f"  risk {f*100:4.1f}%/trade -> notional {(f/0.025):4.2f}x equity, "
              f"margin {margin_per_pos:5.3f}x equity -> max {int(1/margin_per_pos)} concurrent")


if __name__ == "__main__":
    main()
