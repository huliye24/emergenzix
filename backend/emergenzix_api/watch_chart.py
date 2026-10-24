"""Server-side chart rendering for PAPER watch notifications."""

from __future__ import annotations

from io import BytesIO
from typing import Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .schemas import Bar
from .watch_agent import WatchEvent


SCENARIO_LABELS = {
    "continuation_down": "延续下跌",
    "false_breakdown": "假跌破",
    "range": "震荡区间",
    "reversal_up": "向上反转",
}


def render_watch_chart(event: WatchEvent, bars: Sequence[Bar], *, candle_count: int = 60) -> bytes:
    """Render completed candles and the event's calculated levels as a PNG image."""
    selected = list(bars[-candle_count:])
    if not selected:
        raise ValueError("at least one completed candle is required for a watch chart")

    details = event.details
    levels = details.get("key_levels", {}) if isinstance(details, dict) else {}
    support = float(levels["support"])
    resistance = float(levels["resistance"])
    scenario = str(details.get("scenario", "unknown"))
    confidence = float(details.get("confidence", 0.0))

    plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    figure, (price_axis, volume_axis) = plt.subplots(
        2, 1, figsize=(14, 8), dpi=140, sharex=True,
        gridspec_kw={"height_ratios": [4, 1], "hspace": 0.04},
        layout="constrained",
    )
    figure.patch.set_facecolor("#ffffff")
    price_axis.set_facecolor("#ffffff")
    volume_axis.set_facecolor("#ffffff")

    for index, bar in enumerate(selected):
        rising = bar.close >= bar.open
        color = "#26a69a" if rising else "#ef5350"
        price_axis.vlines(index, bar.low, bar.high, color=color, linewidth=1.0)
        body_low = min(bar.open, bar.close)
        body_height = max(abs(bar.close - bar.open), max(bar.high - bar.low, 1.0) * 0.015)
        price_axis.add_patch(Rectangle((index - 0.32, body_low), 0.64, body_height, facecolor=color, edgecolor=color))
        volume_axis.bar(index, bar.volume, width=0.64, color=color, alpha=0.65)

    price_axis.axhline(support, color="#2e7d32", linewidth=1.4)
    price_axis.axhline(resistance, color="#d32f2f", linewidth=1.4)
    price_axis.text(len(selected) - 0.5, support, f" 支撑 {support:g}", color="#2e7d32", va="bottom", ha="right")
    price_axis.text(len(selected) - 0.5, resistance, f" 压力 {resistance:g}", color="#d32f2f", va="bottom", ha="right")

    latest = selected[-1]
    annotation = f"当前场景：{SCENARIO_LABELS.get(scenario, scenario)}\n置信度：{confidence:.1%}\n最新收盘：{latest.close:g}"
    price_axis.annotate(
        annotation,
        xy=(len(selected) - 1, latest.close),
        xytext=(max(0, len(selected) - 18), latest.close + (resistance - support) * 0.18),
        color="#c62828",
        fontsize=11,
        weight="bold",
        arrowprops={"arrowstyle": "->", "color": "#c62828", "linewidth": 1.8},
        bbox={"boxstyle": "round,pad=0.4", "facecolor": "white", "edgecolor": "#c62828", "alpha": 0.92},
    )

    price_axis.set_title(f"{event.symbol} · {event.timeframe} · Emergenzix PAPER 场景图", loc="left", fontsize=14, weight="bold")
    price_axis.set_ylabel("价格")
    volume_axis.set_ylabel("成交量")
    price_axis.grid(color="#e0e0e0", linewidth=0.6, alpha=0.7)
    volume_axis.grid(axis="y", color="#eeeeee", linewidth=0.6)
    tick_step = max(1, len(selected) // 8)
    ticks = list(range(0, len(selected), tick_step))
    if ticks[-1] != len(selected) - 1:
        ticks.append(len(selected) - 1)
    volume_axis.set_xticks(ticks)
    volume_axis.set_xticklabels([selected[index].timestamp.strftime("%m-%d\n%H:%M") for index in ticks])
    output = BytesIO()
    figure.savefig(output, format="png", bbox_inches="tight")
    plt.close(figure)
    return output.getvalue()
