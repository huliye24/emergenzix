"""Server-side chart rendering for PAPER watch notifications."""

from __future__ import annotations

from io import BytesIO
from math import sqrt
from typing import Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .schemas import Bar
from .watch_agent import WatchEvent


def _bollinger_bands(
    bars: Sequence[Bar], *, period: int = 20, deviations: float = 2.0
) -> tuple[list[float], list[float], list[float]]:
    """Return causal SMA Bollinger bands, using only completed closes."""
    if period < 2:
        raise ValueError("Bollinger period must be at least 2")
    if deviations <= 0:
        raise ValueError("Bollinger deviations must be positive")
    if len(bars) < period:
        raise ValueError(f"at least {period} completed candles are required")

    closes = [float(bar.close) for bar in bars]
    middle: list[float] = []
    upper: list[float] = []
    lower: list[float] = []
    for index in range(period - 1, len(closes)):
        window = closes[index - period + 1 : index + 1]
        mean = sum(window) / period
        variance = sum((value - mean) ** 2 for value in window) / period
        width = deviations * sqrt(variance)
        middle.append(mean)
        upper.append(mean + width)
        lower.append(mean - width)
    return middle, upper, lower


def _confirmed_trendline(
    bars: Sequence[Bar], *, pivot_radius: int = 2
) -> tuple[int, float, float, str] | None:
    """Fit one line from the latest two confirmed rising lows or falling highs."""
    if pivot_radius < 1 or len(bars) < pivot_radius * 2 + 2:
        return None

    pivot_lows: list[tuple[int, float]] = []
    pivot_highs: list[tuple[int, float]] = []
    for index in range(pivot_radius, len(bars) - pivot_radius):
        neighborhood = bars[index - pivot_radius : index + pivot_radius + 1]
        if bars[index].low == min(bar.low for bar in neighborhood):
            pivot_lows.append((index, float(bars[index].low)))
        if bars[index].high == max(bar.high for bar in neighborhood):
            pivot_highs.append((index, float(bars[index].high)))

    candidates: list[tuple[int, float, float, str]] = []
    for pivots, kind in ((pivot_lows, "support"), (pivot_highs, "resistance")):
        for first, second in zip(reversed(pivots[:-1]), reversed(pivots[1:])):
            first_index, first_price = first
            second_index, second_price = second
            rising_support = kind == "support" and second_price > first_price
            falling_resistance = kind == "resistance" and second_price < first_price
            if rising_support or falling_resistance:
                slope = (second_price - first_price) / (second_index - first_index)
                candidates.append((second_index, first_price - slope * first_index, slope, kind))
                break
    return max(candidates, key=lambda item: item[0]) if candidates else None


def render_watch_chart(event: WatchEvent, bars: Sequence[Bar], *, candle_count: int = 60) -> bytes:
    """Render causal completed-bar analysis plus a clearly separated live candle."""
    selected = list(bars[-candle_count:])
    if len(selected) < 20:
        raise ValueError("at least 20 completed candles are required for a watch chart")

    details = event.details
    levels = details.get("key_levels", {}) if isinstance(details, dict) else {}
    support = float(levels["support"])
    resistance = float(levels["resistance"])
    live = details.get("live_monitoring") if isinstance(details, dict) else None
    live_bar = None
    if isinstance(live, dict) and live.get("forming") is True:
        candidate = Bar(
            timestamp=live["timestamp"],
            open=live["open"],
            high=live["high"],
            low=live["low"],
            close=live["price"],
            volume=live["volume"],
        )
        if candidate.timestamp > selected[-1].timestamp:
            live_bar = candidate

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

    if live_bar is not None:
        live_index = len(selected)
        live_color = "#00897b" if live_bar.close >= live_bar.open else "#e53935"
        price_axis.axvspan(live_index - 0.48, live_index + 0.48, color="#fff3e0", alpha=0.9)
        price_axis.vlines(live_index, live_bar.low, live_bar.high, color=live_color, linewidth=1.2)
        live_body_low = min(live_bar.open, live_bar.close)
        live_body_height = max(
            abs(live_bar.close - live_bar.open),
            max(live_bar.high - live_bar.low, 1.0) * 0.015,
        )
        price_axis.add_patch(
            Rectangle(
                (live_index - 0.32, live_body_low), 0.64, live_body_height,
                facecolor=live_color, edgecolor=live_color, alpha=0.78,
            )
        )
        volume_axis.bar(live_index, live_bar.volume, width=0.64, color=live_color, alpha=0.45)
        price_axis.axhline(live_bar.close, color="#00838f", linewidth=1.0, linestyle=":")
        price_axis.text(
            live_index + 0.4, live_bar.close, f" 实时参考 {live_bar.close:g}",
            color="#006064", va="center", ha="left", fontsize=9, weight="bold",
        )

    middle, upper, lower = _bollinger_bands(selected)
    band_x = list(range(19, len(selected)))
    price_axis.plot(band_x, upper, color="#ef5350", linewidth=1.25, label="BB上轨 (20,2)")
    price_axis.plot(band_x, middle, color="#2962ff", linewidth=1.25, label="BB中轨 SMA20")
    price_axis.plot(band_x, lower, color="#00a99d", linewidth=1.25, label="BB下轨 (20,2)")
    price_axis.fill_between(band_x, lower, upper, color="#90caf9", alpha=0.10)

    trendline = _confirmed_trendline(selected)
    if trendline is not None:
        anchor_index, intercept, slope, kind = trendline
        line_start = max(0, anchor_index - 20)
        line_x = list(range(line_start, len(selected) + 12))
        line_y = [intercept + slope * index for index in line_x]
        label = "上升支撑趋势线" if kind == "support" else "下降压力趋势线"
        price_axis.plot(line_x, line_y, color="#7b1fa2", linewidth=1.8, label=label)

    price_axis.axhline(support, color="#2e7d32", linewidth=1.4)
    price_axis.axhline(resistance, color="#d32f2f", linewidth=1.4)
    price_axis.text(len(selected) - 0.5, support, f" 支撑 {support:g}", color="#2e7d32", va="bottom", ha="right")
    price_axis.text(len(selected) - 0.5, resistance, f" 压力 {resistance:g}", color="#d32f2f", va="bottom", ha="right")

    price_axis.set_title(f"{event.symbol} · {event.timeframe} · Emergenzix PAPER 场景图", loc="left", fontsize=14, weight="bold")
    price_axis.set_ylabel("价格")
    volume_axis.set_ylabel("成交量")
    price_axis.legend(loc="upper left", fontsize=8, framealpha=0.88, ncol=2)
    price_axis.grid(color="#e0e0e0", linewidth=0.6, alpha=0.7)
    volume_axis.grid(axis="y", color="#eeeeee", linewidth=0.6)
    tick_step = max(1, len(selected) // 8)
    ticks = list(range(0, len(selected), tick_step))
    if ticks[-1] != len(selected) - 1:
        ticks.append(len(selected) - 1)
    if live_bar is not None:
        ticks.append(len(selected))
    volume_axis.set_xticks(ticks)
    tick_labels = [
        (live_bar.timestamp.strftime("%m-%d\n%H:%M") + "\n形成中")
        if live_bar is not None and index == len(selected)
        else selected[index].timestamp.strftime("%m-%d\n%H:%M")
        for index in ticks
    ]
    volume_axis.set_xticklabels(tick_labels)
    output = BytesIO()
    figure.savefig(output, format="png", bbox_inches="tight")
    plt.close(figure)
    return output.getvalue()
