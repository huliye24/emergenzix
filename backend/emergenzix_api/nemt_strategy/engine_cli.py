import argparse
import json
import math
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from emergenzix_api.historical_data import fetch_binance_btc_4h_archive
from emergenzix_api.schemas import Bar
from .nemt_momentum_v2 import MomentumV2Config, compute_momentum_state
from .nemt_strategy_v3 import (
    PositionControllerV3,
    StrategyV3Config,
    risk_sized_notional,
)
from .nemt_pyramiding_v4 import (
    AdaptivePyramidInputs, PyramidPosition, PyramidingControllerV4,
    PyramidingV4Config, adaptive_pyramid_decision, adaptive_pyramid_decision_v42,
)
from .trend_regime import (
    AnnualTrendConfig,
    classify_annual_trend,
)
from .trend_rollability_v43 import (
    RollabilityObservation, assess_rollability,
)
from .registry import data_fingerprint, get_strategy, report_fingerprint


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def atr(bars: list[Bar], period: int = 14) -> float:
    values = []
    for i in range(1, len(bars[-(period + 1):])):
        window = bars[-(period + 1):]
        values.append(max(window[i].high - window[i].low,
                          abs(window[i].high - window[i - 1].close),
                          abs(window[i].low - window[i - 1].close)))
    return sum(values) / len(values) if values else 0.0


def drawdown(values: list[float]) -> float:
    peak, result = values[0], 0.0
    for value in values:
        peak = max(peak, value)
        result = max(result, (peak - value) / peak if peak > 0 else 0.0)
    return result


parser = argparse.ArgumentParser()
parser.add_argument("--start", default="2026-06-01")
parser.add_argument("--end", default="2026-10-01")
parser.add_argument("--capital", type=float, default=100.0)
parser.add_argument("--variant", choices=["v3", "v4a", "v4b", "v4c", "v41", "v42", "v43"], default="v4b")
parser.add_argument("--output-dir", type=Path, default=Path("data/reports"))
args = parser.parse_args()

if args.variant == "v3":
    raise SystemExit("v3 is retired; use an NEMT v4 variant")

zone = ZoneInfo("Asia/Shanghai")
start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=zone)
end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=zone)
payloads = fetch_binance_btc_4h_archive(
    start_at=start.astimezone(timezone.utc) - timedelta(days=400),
    end_at=end.astimezone(timezone.utc) + timedelta(hours=4),
)
bars = [Bar.model_validate(item) for item in payloads]
closes = [bar.close for bar in bars]
config = MomentumV2Config()
trend_config = AnnualTrendConfig()
v3_config = StrategyV3Config()
controller = PositionControllerV3(v3_config)
fee_rate, slippage = 0.0005, 0.0002

cash = args.capital
position = None
trades = []
curve = []
exposed = 0
previous_quality = 0.0
fixed_controller = None
if args.variant in {"v4a", "v4b", "v4c"}:
    mode = {"v4a": "v4_a_breakeven", "v4b": "v4_b_atr_trailing", "v4c": "v4_c_trailing_only"}[args.variant]
    fixed_controller = PyramidingControllerV4(PyramidingV4Config(mode=mode))

# Frozen pre-test rollability evidence; every label horizon ends before test start.
roll_history = []
for j in range(120, len(bars) - 7):
    if bars[j + 6].timestamp.astimezone(zone) >= start:
        break
    historical_state = compute_momentum_state(closes[:j + 1], config)
    direction = 1 if historical_state.phase == "critical_up" else -1 if historical_state.phase == "critical_down" else 0
    if direction == 0:
        continue
    risk = max(0.01 * bars[j].close, min(0.05 * bars[j].close, 1.5 * atr(bars[max(0, j - 15):j + 1])))
    target = bars[j].close + direction * 1.5 * risk
    stop = bars[j].close - direction * risk
    outcome = -1.0
    for future in bars[j + 1:j + 7]:
        stopped = future.low <= stop if direction == 1 else future.high >= stop
        won = future.high >= target if direction == 1 else future.low <= target
        if stopped:
            outcome = -1.0; break
        if won:
            outcome = 1.5; break
    features = (historical_state.score / 100.0, historical_state.entropy, min(abs(historical_state.psi) / 2.0, 1.0), min(risk / bars[j].close * 20.0, 1.0))
    roll_history.append(RollabilityObservation(features, outcome > 0, outcome))

for i in range(1, len(bars)):
    bar = bars[i]
    local_open = bar.timestamp.astimezone(zone)
    if local_open < start or local_open >= end:
        continue
    decision_i = i - 1
    state = compute_momentum_state(closes[: decision_i + 1], config)
    annual = classify_annual_trend(closes, decision_i, trend_config)
    current_target = position["target"] if position else 0
    intent = controller.decide(state, current_target)

    # Additions and stop changes decided on the previous completed candle execute now.
    if position and position.get("pending_add", 0.0) > 0:
        add = position.pop("pending_add")
        fill = bar.open * (1 + slippage if position["target"] == 1 else 1 - slippage)
        position["qty"] += add / fill
        position["total_cost"] += add
        position["entry_fee"] += add * fee_rate
        position["notional"] += add
        position["add_count"] += 1
    if position and position.get("pending_stop") is not None:
        position["stop"] = position.pop("pending_stop")

    # Signal exits/reversals execute at this bar's open before intrabar risk checks.
    if position and intent.target != current_target:
        side = position["target"]
        exit_fill = bar.open * (1 - slippage if side == 1 else 1 + slippage)
        gross = position["qty"] * (exit_fill - position["entry_price"]) * side
        exit_fee = position["qty"] * exit_fill * fee_rate
        net = gross - position["entry_fee"] - exit_fee
        cash += net
        trades.append({**position, "exit_at": iso(bar.timestamp), "exit_price": exit_fill,
                       "exit_reason": "reverse" if intent.target else "decay",
                       "gross_pnl": gross, "fees": position["entry_fee"] + exit_fee,
                       "net_pnl": net, "equity_after": cash})
        position = None

    if position is None and intent.target != 0 and cash > 0:
        side = intent.target
        entry = bar.open * (1 + slippage if side == 1 else 1 - slippage)
        leverage = intent.leverage
        stop_pct = max(0.01, min(0.05, atr(bars[max(0, i - 15):i]) * 1.5 / entry))
        notional = risk_sized_notional(cash, stop_pct, leverage, v3_config)
        margin = notional / leverage
        qty = notional / entry
        entry_fee = notional * fee_rate
        position = {
            "target": side, "side": "LONG" if side == 1 else "SHORT",
            "entry_at": iso(bar.timestamp), "entry_price": entry, "qty": qty,
            "entry_fee": entry_fee, "margin": margin, "notional": notional,
            "leverage": leverage, "risk_per_trade": v3_config.risk_per_trade,
            "initial_risk_usdt": cash * v3_config.risk_per_trade,
            "annual_regime": annual.regime,
            "annual_return_pct": annual.trailing_return * 100 if annual.trailing_return is not None else None,
            "entry_phase": state.phase, "entry_psi": state.psi, "entry_entropy": state.entropy,
            "stop": entry * (1 - side * stop_pct),
            "target_price": entry * (1 + side * stop_pct * 2),
            "initial_stop": entry * (1 - side * stop_pct),
            "initial_notional": notional,
            "total_cost": notional, "add_count": 0, "best_price": entry,
        }

    if position:
        exposed += 1
        side = position["target"]
        stop_hit = bar.low <= position["stop"] if side == 1 else bar.high >= position["stop"]
        target_hit = args.variant == "v3" and (bar.high >= position["target_price"] if side == 1 else bar.low <= position["target_price"])
        if stop_hit or target_hit:
            raw_exit = position["stop"] if stop_hit else position["target_price"]
            exit_fill = raw_exit * (1 - slippage if side == 1 else 1 + slippage)
            gross = position["qty"] * (exit_fill - position["entry_price"]) * side
            exit_fee = position["qty"] * exit_fill * fee_rate
            net = gross - position["entry_fee"] - exit_fee
            cash += net
            trades.append({**position, "exit_at": iso(bar.timestamp), "exit_price": exit_fill,
                           "exit_reason": "stop_loss" if stop_hit else "take_profit",
                           "gross_pnl": gross, "fees": position["entry_fee"] + exit_fee,
                           "net_pnl": net, "equity_after": cash})
            position = None
            if stop_hit:
                controller.on_stop()

    # Use this completed close only to schedule next-open additions/protection.
    if position and args.variant != "v3":
        quality = state.score / 100.0
        if args.variant in {"v41", "v42", "v43"}:
            completed_wins = [t["net_pnl"] for t in trades if t["net_pnl"] > 0]
            completed_losses = [-t["net_pnl"] for t in trades if t["net_pnl"] <= 0]
            probability = (len(completed_wins) + 2) / (len(trades) + 4)
            payoff = ((sum(completed_wins) / len(completed_wins)) / (sum(completed_losses) / len(completed_losses))) if completed_wins and completed_losses else 2.0
            peak = max([args.capital] + [p["equity"] for p in curve])
            dd = max(0.0, (peak - cash) / peak)
            pyramid_inputs = AdaptivePyramidInputs(
                equity=cash, price=bar.close, protective_stop=position["stop"],
                current_quantity=position["qty"], average_entry=position["total_cost"] / position["qty"],
                side=position["target"], win_probability=probability, payoff_ratio=max(payoff, 0.01),
                nemt_quality=quality, previous_nemt_quality=previous_quality,
                regime_confidence=0.75, drawdown_fraction=min(dd, 1.0),
                same_direction_confirmed=(state.phase == ("critical_up" if position["target"] == 1 else "critical_down")),
            )
            if args.variant in {"v42", "v43"}:
                completed_r = [
                    t["net_pnl"] / t["initial_risk_usdt"]
                    for t in trades if t.get("initial_risk_usdt", 0) > 0
                ]
                if args.variant == "v43":
                    current_features = (quality, state.entropy, min(abs(state.psi) / 2.0, 1.0), min(atr(bars[max(0, i - 15):i + 1]) / bar.close * 20.0, 1.0))
                    permission = assess_rollability(current_features, roll_history)
                    pretrained_r = [item.outcome_r for item in roll_history]
                    decision = adaptive_pyramid_decision_v42(pyramid_inputs, pretrained_r + completed_r).pyramid
                    if not permission.permitted:
                        decision = decision.__class__(0.0, decision.remaining_risk_budget, decision.locked_profit, decision.open_stop_risk, decision.raw_kelly, decision.adjusted_kelly, decision.expected_edge, f"rollability_{permission.reason}")
                else:
                    decision = adaptive_pyramid_decision_v42(pyramid_inputs, completed_r).pyramid
            else:
                decision = adaptive_pyramid_decision(pyramid_inputs)
            position["pending_add"] = decision.add_notional
            trail = bar.close - position["target"] * 2.0 * atr(bars[max(0, i - 15):i + 1])
            position["pending_stop"] = max(position["stop"], trail) if position["target"] == 1 else min(position["stop"], trail)
        else:
            shadow = PyramidPosition(position["target"], position["entry_price"], position["initial_stop"],
                position["initial_notional"], position["qty"], position["total_cost"], position["stop"],
                position["add_count"], position["best_price"])
            decision = fixed_controller.evaluate(shadow, price=bar.close,
                atr=atr(bars[max(0, i - 15):i + 1]), equity=cash,
                same_direction_confirmed=(state.phase == ("critical_up" if position["target"] == 1 else "critical_down")))
            position["pending_add"] = decision.add_notional
            position["pending_stop"] = decision.new_stop
            position["best_price"] = shadow.best_price
        previous_quality = quality

    if position:
        unrealized = position["qty"] * (bar.close - position["entry_price"]) * position["target"]
        equity = cash + unrealized - position["entry_fee"]
    else:
        equity = cash
    curve.append({"time": iso(bar.timestamp), "equity": equity})

if position:
    last = next(bar for bar in reversed(bars) if bar.timestamp.astimezone(zone) < end)
    side = position["target"]
    exit_fill = last.close * (1 - slippage if side == 1 else 1 + slippage)
    gross = position["qty"] * (exit_fill - position["entry_price"]) * side
    exit_fee = position["qty"] * exit_fill * fee_rate
    net = gross - position["entry_fee"] - exit_fee
    cash += net
    trades.append({**position, "exit_at": iso(last.timestamp + timedelta(hours=4)),
                   "exit_price": exit_fill, "exit_reason": "end_of_period",
                   "gross_pnl": gross, "fees": position["entry_fee"] + exit_fee,
                   "net_pnl": net, "equity_after": cash})
    if curve:
        curve[-1]["equity"] = cash

equities = [args.capital] + [point["equity"] for point in curve]
returns = [equities[i] / equities[i - 1] - 1 for i in range(1, len(equities)) if equities[i - 1] > 0]
mean = statistics.mean(returns) if returns else 0.0
std = statistics.pstdev(returns) if len(returns) > 1 else 0.0
wins = [trade for trade in trades if trade["net_pnl"] > 0]
losses = [trade for trade in trades if trade["net_pnl"] <= 0]
gross_win = sum(trade["net_pnl"] for trade in wins)
gross_loss = abs(sum(trade["net_pnl"] for trade in losses))
expected_trade_pnl = sum(trade["net_pnl"] for trade in trades) / len(trades) if trades else 0.0
average_loss = gross_loss / len(losses) if losses else 0.0
metrics = {
    "initial_equity": args.capital, "final_equity": round(cash, 8),
    "total_return_pct": round((cash / args.capital - 1) * 100, 6),
    "trades": len(trades), "wins": len(wins), "losses": len(losses),
    "win_rate_pct": round(len(wins) / len(trades) * 100, 6) if trades else 0.0,
    "profit_factor": round(gross_win / gross_loss, 6) if gross_loss else None,
    "payoff_ratio": round((gross_win / len(wins)) / (gross_loss / len(losses)), 6) if wins and losses else None,
    "expected_trade_pnl": round(expected_trade_pnl, 6),
    "expectancy_over_average_loss": round(expected_trade_pnl / average_loss, 6) if average_loss else None,
    "max_drawdown_pct": round(drawdown(equities) * 100, 6),
    "annualized_sharpe_4h": round(mean / std * math.sqrt(6 * 365), 6) if std else 0.0,
    "exposure_pct": round(exposed / len(curve) * 100, 6) if curve else 0.0,
    "longs": sum(t["side"] == "LONG" for t in trades),
    "shorts": sum(t["side"] == "SHORT" for t in trades),
}
evidence = {
    "sample_size": metrics["trades"],
    "positive_expectancy": expected_trade_pnl > 0,
    "profit_factor": metrics["profit_factor"],
    "payoff_ratio": metrics["payoff_ratio"],
    "sharpe": metrics["annualized_sharpe_4h"],
    "max_drawdown_pct": metrics["max_drawdown_pct"],
}
strategy = get_strategy()
result = {"record_type": f"nemt_{args.variant}_continuous_backtest", "start_local": start.isoformat(),
          "end_local_exclusive": end.isoformat(), "config": {**config.__dict__, **v3_config.__dict__}, "metrics": metrics,
          "evidence": evidence,
          "trades": trades, "equity_curve": curve,
          "provenance": {"strategy_id": strategy.strategy_id, "strategy_version": strategy.version,
                         "variant": args.variant, "source_commit": strategy.source_commit,
                         "validation_commit": strategy.validation_commit,
                         "parameter_fingerprint": strategy.parameter_fingerprint,
                         "evidence_fingerprint": strategy.evidence_fingerprint,
                         "data_fingerprint": data_fingerprint([bar.model_dump() for bar in bars]),
                         "execution_mode": strategy.execution_mode, "live_enabled": strategy.live_enabled},
          "assumptions": {"fee_bps_each_side": 5, "slippage_bps_each_side": 2,
                          "funding_modeled": False, "intrabar_both_hit": "stop_first"}}
result["report_fingerprint"] = report_fingerprint(result)
out = args.output_dir
out.mkdir(parents=True, exist_ok=True)
slug = f"nemt-{args.variant}-continuous-{args.start}-to-{args.end}"
json_path, md_path = out / f"{slug}.json", out / f"{slug}.md"
json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
lines = "\n".join(f"| {t['entry_at'][:10]} | {t['side']} | {t['leverage']}× | {t['exit_reason']} | {t['net_pnl']:+.3f} |" for t in trades)
md = f"""# NEMT {args.variant} 连续单账户回测

- 区间：{args.start} 至 {args.end}（北京时间，结束日不含）
- 初始权益：{args.capital:.2f} USDT
- 单账户、单方向、禁止重叠开仓；同向信号持有
- 年度趋势仅记录，不参与方向或仓位
- 杠杆：3×/5×；每笔止损风险不超过权益2%；1.5×ATR止损、2R止盈
- 反向信号连续确认2根K线；止损后冷却2根K线

| 指标 | 结果 |
|---|---:|
| 期末权益 | {metrics['final_equity']:.4f} USDT |
| 总收益率 | {metrics['total_return_pct']:+.2f}% |
| 完成交易 | {metrics['trades']} |
| 胜率 | {metrics['win_rate_pct']:.2f}% |
| 盈利因子 | {metrics['profit_factor']} |
| 盈亏比 | {metrics['payoff_ratio']} |
| 单笔期望 | {metrics['expected_trade_pnl']:+.4f} USDT |
| 期望/平均亏损 | {metrics['expectancy_over_average_loss']}R |
| 年化4h夏普 | {metrics['annualized_sharpe_4h']:.3f} |
| 最大回撤 | {metrics['max_drawdown_pct']:.2f}% |
| 市场暴露 | {metrics['exposure_pct']:.2f}% |
| 多/空 | {metrics['longs']} / {metrics['shorts']} |

## 交易明细

| 入场UTC | 方向 | 杠杆 | 离场原因 | 净损益USDT |
|---|---|---:|---|---:|
{lines or '| — | — | — | 无交易 | 0.000 |'}

## 限制

- 未计资金费率、强平阶梯、延迟和盘口深度。
- 同根K线同时触及止盈止损时按止损优先。
- 这是历史模拟，不代表未来收益，也未创建任何真实订单。
"""
md_path.write_text(md, encoding="utf-8")
print(json.dumps({"json_path": str(json_path), "markdown_path": str(md_path), "metrics": metrics, "evidence": evidence}, ensure_ascii=False))
