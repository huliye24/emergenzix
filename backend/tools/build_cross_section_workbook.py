"""Build the 10-symbol cross-section workbook (33k trades) using xlsxwriter."""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import xlsxwriter

SOURCE = Path(r"C:\Users\Administrator\AppData\Local\Temp\xsec_payload.json")
AGG = Path(r"C:\Users\Administrator\AppData\Local\Temp\xsec_aggregates.json")
TARGET = Path(r"E:\NEMT Simulator\docs\reports\NEMT_trades_cross_section_10_symbols.xlsx")

MONEY = "#,##0.00"
PRICE = "#,##0.0000"
PCT = "0.00"
R3 = "0.000"


def main() -> None:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    agg = json.loads(AGG.read_text(encoding="utf-8"))
    runs = payload["runs"]
    trades = payload["trades"]

    wb = xlsxwriter.Workbook(str(TARGET), {"constant_memory": False})
    f = {
        "title": wb.add_format({"bold": True, "font_size": 14, "font_color": "#1F3864"}),
        "head": wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#1F3864",
                               "border": 1, "align": "center", "valign": "vcenter",
                               "text_wrap": True}),
        "text": wb.add_format({"border": 1}),
        "int": wb.add_format({"border": 1, "num_format": "0"}),
        "money": wb.add_format({"border": 1, "num_format": MONEY}),
        "price": wb.add_format({"border": 1, "num_format": PRICE}),
        "pct": wb.add_format({"border": 1, "num_format": PCT}),
        "r3": wb.add_format({"border": 1, "num_format": R3}),
        "r4": wb.add_format({"border": 1, "num_format": "0.0000"}),
        "bold": wb.add_format({"bold": True, "border": 1}),
        "wrap": wb.add_format({"text_wrap": True, "valign": "top"}),
    }

    def sheet(name, headers, rows, formats):
        ws = wb.add_worksheet(name)
        for c, h in enumerate(headers):
            ws.write(0, c, h, f["head"])
        for r, record in enumerate(rows, 1):
            for c, h in enumerate(headers):
                value = record.get(h)
                fmt = f[formats.get(h, "text")]
                if value is None:
                    ws.write_blank(r, c, None, fmt)
                elif isinstance(value, (int, float)):
                    ws.write_number(r, c, value, fmt)
                else:
                    ws.write_string(r, c, str(value), fmt)
        ws.freeze_panes(1, 0)
        ws.autofilter(0, 0, len(rows), len(headers) - 1)
        for c, h in enumerate(headers):
            ws.set_column(c, c, max(9, min(24, len(h) + 3)))
        ws.set_row(0, 30)
        return ws

    # 1. README
    ws = wb.add_worksheet("说明")
    ws.write(0, 0, "NEMT 策略 · 10 币种横截面回测 · 全量开单记录", f["title"])
    lines = [
        ("文件用途", "10 币 × 4 年 × 8 种风险/杠杆组合 × 6 个策略格子，逐笔开单全部导出"),
        ("交易总数", f"{len(trades):,} 笔"),
        ("配置总数", f"{len(runs):,} 个"),
        ("币种", "、".join(agg["symbols"])),
        ("年份", "、".join(str(y) for y in agg["years"]) + "（各年独立，年初重置 100 USDT）"),
        ("格子", "R0 单笔 / F0 固定预算 / T3 三批 / T2 两批 / C1 平衡止损 / U1 周级UP-UP闸门"),
        ("风险档", "1% / 2% / 3% / 5% / 7.5% / 10%（杠杆 3x）；5% 与 10% 另有 10x 算术演示档"),
        ("R 倍数", "net_pnl ÷ (风险比例 × 开仓前权益)，即每笔以初始风险为单位的盈亏"),
        ("数据来源", "Binance 现货 4h 官方归档，校验 .CHECKSUM；覆盖率审计 10/10 通过"),
        ("横截面结果", f"资产-年份正收益 {agg['asset_year_positive']}/{agg['asset_year_count']}"
                    f" = {agg['asset_year_positive_pct']}%"),
        ("证据等级", "PREREGISTERED（8 个币种首次使用）；EXPLORATORY 于 BTC/ETH"),
        ("重要限制", "加密币高度相关，10 币收益相加 ≠ 分散化；10x 档真实交易会被强平"),
        ("订单权限", "无。本工作簿不涉及任何 PAPER / TESTNET / LIVE 订单"),
    ]
    for r, (k, v) in enumerate(lines, 2):
        ws.write(r, 0, k, f["bold"])
        ws.write(r, 1, v, f["wrap"])
        ws.set_row(r, 28)
    ws.set_column(0, 0, 16)
    ws.set_column(1, 1, 100)

    # 2. 总览
    run_headers = ["run_id", "symbol", "year", "risk_pct", "max_leverage", "cell",
                   "final", "ret_pct", "dd_pct", "trades", "wins", "losses",
                   "profit_factor", "max_open_risk_pct", "leverage_truncations",
                   "w_o_best_pct", "fees", "max_consec_losses"]
    run_fmt = {"final": "money", "ret_pct": "pct", "dd_pct": "pct",
               "profit_factor": "r3", "max_open_risk_pct": "pct",
               "w_o_best_pct": "pct", "fees": "r4", "risk_pct": "pct",
               "trades": "int", "wins": "int", "losses": "int",
               "leverage_truncations": "int", "max_consec_losses": "int",
               "year": "int", "max_leverage": "int"}
    sheet("总览", run_headers,
          sorted(runs, key=lambda x: (x["symbol"], x["year"], x["risk_pct"],
                                      x["max_leverage"], x["cell"])), run_fmt)

    # 3. 全部交易
    trade_headers = ["run_id", "symbol", "year", "risk_pct", "max_leverage", "cell",
                     "seq", "side", "exit_reason", "exit_at", "holding_bars",
                     "holding_days", "avg_entry", "exit_price", "quantity", "notional",
                     "entry_fee", "exit_fee", "net_pnl", "equity_before", "equity_after",
                     "risk_usdt", "r_multiple", "tranches"]
    trade_fmt = {"avg_entry": "price", "exit_price": "price", "notional": "money",
                 "entry_fee": "r4", "exit_fee": "r4", "net_pnl": "r4",
                 "equity_before": "money", "equity_after": "money",
                 "risk_usdt": "r4", "r_multiple": "r3", "holding_days": "pct",
                 "risk_pct": "pct", "seq": "int", "year": "int",
                 "max_leverage": "int", "holding_bars": "int", "tranches": "int"}
    sheet("全部交易", trade_headers,
          sorted(trades, key=lambda x: (x["symbol"], x["year"], x["risk_pct"],
                                        x["max_leverage"], x["cell"], x["seq"])),
          trade_fmt)

    # 4. 风险档汇总
    buckets = defaultdict(list)
    for row in trades:
        if row["max_leverage"] != 3.0:
            continue
        buckets[(row["symbol"], row["risk_pct"], row["cell"])].append(row)
    summary = []
    for key in sorted(buckets):
        sym, risk, cell = key
        items = buckets[key]
        rs = [x["r_multiple"] for x in items if x["r_multiple"] is not None]
        gw = sum(x["net_pnl"] for x in items if x["net_pnl"] > 0)
        gl = abs(sum(x["net_pnl"] for x in items if x["net_pnl"] <= 0))
        summary.append({
            "symbol": sym, "risk_pct": risk, "cell": cell, "trades": len(items),
            "win_rate_pct": round(sum(1 for x in items if x["net_pnl"] > 0) / len(items) * 100, 2),
            "avg_R": round(statistics.fmean(rs), 3),
            "median_R": round(statistics.median(rs), 3),
            "best_R": round(max(rs), 3), "worst_R": round(min(rs), 3),
            "sum_R": round(sum(rs), 2),
            "profit_factor": round(gw / gl, 3) if gl else None,
            "avg_net_pnl": round(statistics.fmean(x["net_pnl"] for x in items), 4),
            "avg_holding_days": round(statistics.fmean(x["holding_days"] for x in items), 2),
        })
    sheet("风险档汇总",
          ["symbol", "risk_pct", "cell", "trades", "win_rate_pct", "avg_R", "median_R",
           "best_R", "worst_R", "sum_R", "profit_factor", "avg_net_pnl",
           "avg_holding_days"], summary,
          {"risk_pct": "pct", "trades": "int", "win_rate_pct": "pct", "avg_R": "r3",
           "median_R": "r3", "best_R": "r3", "worst_R": "r3", "sum_R": "pct",
           "profit_factor": "r3", "avg_net_pnl": "r4", "avg_holding_days": "pct"})

    # 5. 币种汇总
    sym_rows = []
    for sym, v in agg["per_symbol"].items():
        sym_rows.append({"symbol": sym, **v})
    sheet("币种汇总",
          ["symbol", "four_year_pct", "four_year_multiple", "years_positive", "years",
           "min_year_pct", "max_year_pct", "worst_dd", "trades_total"], sym_rows,
          {"four_year_pct": "pct", "four_year_multiple": "r4", "years_positive": "int",
           "years": "int", "min_year_pct": "pct", "max_year_pct": "pct",
           "worst_dd": "pct", "trades_total": "int"})

    # 6. 格子汇总
    cell_rows = []
    for cell, v in agg["per_cell_at_1pct"].items():
        tv = agg["per_cell_trades"].get(cell, {})
        cell_rows.append({"cell": cell, **v,
                          "trades_pooled": tv.get("trades"),
                          "win_rate_pct": tv.get("win_rate_pct"),
                          "avg_R": tv.get("avg_R"),
                          "median_R": tv.get("median_R"),
                          "profit_factor_pooled": tv.get("profit_factor")})
    sheet("格子汇总",
          ["cell", "configs", "positive", "median_ret_pct", "mean_ret_pct",
           "worst_ret_pct", "best_ret_pct", "worst_dd", "trades_pooled",
           "win_rate_pct", "avg_R", "median_R", "profit_factor_pooled"], cell_rows,
          {"configs": "int", "positive": "int", "median_ret_pct": "pct",
           "mean_ret_pct": "pct", "worst_ret_pct": "pct", "best_ret_pct": "pct",
           "worst_dd": "pct", "trades_pooled": "int", "win_rate_pct": "pct",
           "avg_R": "r3", "median_R": "r3", "profit_factor_pooled": "r3"})

    # 7. 年份汇总
    year_rows = [{"year": int(k), **v} for k, v in agg["per_year_at_1pct"].items()]
    sheet("年份汇总", ["year", "configs", "positive", "median_ret_pct", "mean_ret_pct"],
          year_rows, {"year": "int", "configs": "int", "positive": "int",
                      "median_ret_pct": "pct", "mean_ret_pct": "pct"})

    # 8. 连亏分析
    streak_rows = []
    for r in runs:
        streak_rows.append({"run_id": r["run_id"], "cell": r["cell"],
                            "trades": r["trades"], "wins": r["wins"],
                            "losses": r["losses"],
                            "max_consec_losses": r["max_consec_losses"]})
    sheet("连亏分析", ["run_id", "cell", "trades", "wins", "losses",
                       "max_consec_losses"], streak_rows,
          {"trades": "int", "wins": "int", "losses": "int", "max_consec_losses": "int"})

    # 9. 退出原因
    reasons = defaultdict(Counter)
    for row in trades:
        reasons[(row["symbol"], row["exit_reason"])][row["cell"]] += 1
    cells_present = sorted({c for counter in reasons.values() for c in counter})
    exit_rows = []
    for (sym, reason), counter in sorted(reasons.items()):
        rec = {"symbol": sym, "exit_reason": reason, "total": sum(counter.values())}
        for c in cells_present:
            rec[f"cell_{c}"] = counter.get(c, 0)
        exit_rows.append(rec)
    sheet("退出原因", ["symbol", "exit_reason", "total"] + [f"cell_{c}" for c in cells_present],
          exit_rows, {"total": "int", **{f"cell_{c}": "int" for c in cells_present}})

    wb.close()
    print(f"saved {TARGET} ({TARGET.stat().st_size:,} bytes)")
    print(f"trades={len(trades):,} runs={len(runs):,}")


if __name__ == "__main__":
    main()
