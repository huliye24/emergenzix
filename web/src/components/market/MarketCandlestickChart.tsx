"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type WheelEvent,
} from "react";

export type MarketCandle = {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  complete: boolean;
};

type Props = {
  candles: MarketCandle[];
  symbol: string;
  timeframe: string;
  support: number;
  resistance: number;
};

const WIDTH = 1120;
const HEIGHT = 560;
const LEFT = 64;
const RIGHT = 1096;
const PRICE_TOP = 24;
const PRICE_BOTTOM = 386;
const VOLUME_TOP = 424;
const VOLUME_BOTTOM = 512;

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function movingAverage(candles: MarketCandle[], period: number): Array<number | null> {
  return candles.map((candle, index) => {
    if (!candle.complete || index + 1 < period) return null;
    const sample = candles.slice(index + 1 - period, index + 1);
    if (sample.some((item) => !item.complete)) return null;
    return sample.reduce((total, item) => total + item.close, 0) / period;
  });
}

function formatAxisPrice(value: number): string {
  return new Intl.NumberFormat("en-US", {
    notation: value >= 100_000 ? "compact" : "standard",
    maximumFractionDigits: value < 10 ? 3 : 0,
  }).format(value);
}

export default function MarketCandlestickChart({
  candles,
  symbol,
  timeframe,
  support,
  resistance,
}: Props) {
  const shellRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ clientX: number; endIndex: number } | null>(null);
  const [windowSize, setWindowSize] = useState(72);
  const [endIndex, setEndIndex] = useState(candles.length);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const [fullscreen, setFullscreen] = useState(false);

  useEffect(() => {
    setEndIndex(candles.length);
  }, [candles.length]);

  useEffect(() => {
    const onFullscreen = () => setFullscreen(document.fullscreenElement === shellRef.current);
    document.addEventListener("fullscreenchange", onFullscreen);
    return () => document.removeEventListener("fullscreenchange", onFullscreen);
  }, []);

  const minimumWindow = Math.min(20, candles.length);
  const effectiveWindow = clamp(windowSize, minimumWindow, Math.max(minimumWindow, candles.length));
  const effectiveEnd = clamp(endIndex, effectiveWindow, candles.length);
  const startIndex = Math.max(0, effectiveEnd - effectiveWindow);
  const visible = candles.slice(startIndex, effectiveEnd);
  const sma20 = useMemo(() => movingAverage(candles, 20), [candles]);
  const sma50 = useMemo(() => movingAverage(candles, 50), [candles]);

  const geometry = useMemo(() => {
    if (!visible.length) return null;
    const lows = visible.map((candle) => candle.low);
    const highs = visible.map((candle) => candle.high);
    const rawMinimum = Math.min(...lows, support);
    const rawMaximum = Math.max(...highs, resistance);
    const padding = Math.max((rawMaximum - rawMinimum) * 0.08, rawMaximum * 0.002);
    const minimum = rawMinimum - padding;
    const maximum = rawMaximum + padding;
    const priceSpan = maximum - minimum || 1;
    const step = (RIGHT - LEFT) / visible.length;
    const candleWidth = Math.max(2, Math.min(12, step * 0.62));
    const maxVolume = Math.max(...visible.map((candle) => candle.volume), 1);
    const x = (index: number) => LEFT + (index + 0.5) * step;
    const y = (price: number) => PRICE_BOTTOM - ((price - minimum) / priceSpan) * (PRICE_BOTTOM - PRICE_TOP);
    const volumeY = (volume: number) => VOLUME_BOTTOM - (volume / maxVolume) * (VOLUME_BOTTOM - VOLUME_TOP);
    return { minimum, maximum, step, candleWidth, x, y, volumeY };
  }, [visible, support, resistance]);

  const linePath = (values: Array<number | null>): string => {
    if (!geometry) return "";
    return visible.map((_, index) => {
      const value = values[startIndex + index];
      return value === null ? "" : `${geometry.x(index)},${geometry.y(value)}`;
    }).filter(Boolean).join(" ");
  };

  const zoom = (delta: number) => {
    const next = clamp(effectiveWindow + delta, minimumWindow, candles.length);
    setWindowSize(next);
    setEndIndex((current) => clamp(current, next, candles.length));
  };

  const reset = () => {
    setWindowSize(Math.min(72, candles.length));
    setEndIndex(candles.length);
    setHoverIndex(null);
  };

  const updateHover = (clientX: number, element: SVGSVGElement) => {
    if (!geometry || !visible.length) return;
    const bounds = element.getBoundingClientRect();
    const viewX = ((clientX - bounds.left) / bounds.width) * WIDTH;
    const index = clamp(Math.floor((viewX - LEFT) / geometry.step), 0, visible.length - 1);
    setHoverIndex(startIndex + index);
  };

  const onPointerMove = (event: PointerEvent<SVGSVGElement>) => {
    if (dragRef.current && visible.length) {
      const bounds = event.currentTarget.getBoundingClientRect();
      const pixelsPerCandle = bounds.width / visible.length;
      const shift = Math.round((dragRef.current.clientX - event.clientX) / pixelsPerCandle);
      setEndIndex(clamp(dragRef.current.endIndex + shift, effectiveWindow, candles.length));
      return;
    }
    updateHover(event.clientX, event.currentTarget);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "+" || event.key === "=") zoom(-10);
    if (event.key === "-") zoom(10);
    if (event.key === "ArrowLeft") setEndIndex((value) => clamp(value - 5, effectiveWindow, candles.length));
    if (event.key === "ArrowRight") setEndIndex((value) => clamp(value + 5, effectiveWindow, candles.length));
    if (event.key === "0") reset();
  };

  const onWheel = (event: WheelEvent<SVGSVGElement>) => {
    event.preventDefault();
    zoom(event.deltaY > 0 ? 10 : -10);
  };

  const toggleFullscreen = async () => {
    if (!shellRef.current) return;
    if (document.fullscreenElement) await document.exitFullscreen();
    else await shellRef.current.requestFullscreen();
  };

  if (!geometry || !visible.length) return <div className="candlestick-empty">等待 K 线数据</div>;
  const hovered = hoverIndex === null ? null : candles[hoverIndex];
  const hoveredVisibleIndex = hoverIndex === null ? null : hoverIndex - startIndex;
  const priceTicks = Array.from({ length: 5 }, (_, index) => geometry.maximum - (index / 4) * (geometry.maximum - geometry.minimum));

  return (
    <div
      ref={shellRef}
      className={`candlestick-shell${fullscreen ? " is-fullscreen" : ""}`}
      tabIndex={0}
      onKeyDown={onKeyDown}
      aria-label={`${symbol} ${timeframe} K 线图，滚轮缩放，拖拽平移`}
    >
      <div className="candlestick-toolbar">
        <div className="chart-legend">
          <span><i className="legend-sma20" /> SMA 20</span>
          <span><i className="legend-sma50" /> SMA 50</span>
          <span>{visible.length} / {candles.length} 根</span>
        </div>
        <div className="chart-actions">
          <button type="button" onClick={() => zoom(-10)} title="放大" aria-label="放大 K 线">＋</button>
          <button type="button" onClick={() => zoom(10)} title="缩小" aria-label="缩小 K 线">−</button>
          <button type="button" onClick={reset} title="回到最新" aria-label="重置图表">↺</button>
          <button type="button" onClick={() => void toggleFullscreen()} title="全屏" aria-label="切换全屏">{fullscreen ? "⊙" : "⛶"}</button>
        </div>
      </div>

      {hovered ? (
        <div className="chart-ohlcv-readout">
          <strong>{new Date(hovered.timestamp).toLocaleString()}</strong>
          <span>O {formatAxisPrice(hovered.open)}</span><span>H {formatAxisPrice(hovered.high)}</span>
          <span>L {formatAxisPrice(hovered.low)}</span><span>C {formatAxisPrice(hovered.close)}</span>
          <span>V {new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(hovered.volume)}</span>
          {!hovered.complete ? <em>FORMING</em> : null}
        </div>
      ) : <div className="chart-gesture-hint">滚轮缩放 · 拖动查看历史 · 双击复位</div>}

      <svg
        className="candlestick-chart"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="none"
        role="img"
        onWheel={onWheel}
        onDoubleClick={reset}
        onPointerDown={(event) => {
          dragRef.current = { clientX: event.clientX, endIndex: effectiveEnd };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={onPointerMove}
        onPointerUp={(event) => {
          dragRef.current = null;
          event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onPointerLeave={() => {
          if (!dragRef.current) setHoverIndex(null);
        }}
      >
        <rect x={LEFT} y={PRICE_TOP} width={RIGHT - LEFT} height={PRICE_BOTTOM - PRICE_TOP} className="chart-plot-bg" />
        {priceTicks.map((price) => {
          const y = geometry.y(price);
          return <g key={price}><line x1={LEFT} x2={RIGHT} y1={y} y2={y} className="chart-grid-line" /><text x={RIGHT - 5} y={y - 6} textAnchor="end" className="chart-axis-label">{formatAxisPrice(price)}</text></g>;
        })}
        <line x1={LEFT} x2={RIGHT} y1={VOLUME_TOP - 14} y2={VOLUME_TOP - 14} className="chart-divider" />
        <text x={LEFT} y={VOLUME_TOP - 22} className="chart-section-label">VOLUME</text>

        {visible.map((candle, index) => {
          const rising = candle.close >= candle.open;
          const x = geometry.x(index);
          const openY = geometry.y(candle.open);
          const closeY = geometry.y(candle.close);
          const bodyY = Math.min(openY, closeY);
          const bodyHeight = Math.max(1.5, Math.abs(openY - closeY));
          const className = `${rising ? "candle-up" : "candle-down"}${candle.complete ? "" : " candle-forming"}`;
          return (
            <g key={candle.timestamp} className={className}>
              <line x1={x} x2={x} y1={geometry.y(candle.high)} y2={geometry.y(candle.low)} className="candle-wick" />
              <rect x={x - geometry.candleWidth / 2} y={bodyY} width={geometry.candleWidth} height={bodyHeight} rx="1" className="candle-body" />
              <rect x={x - geometry.candleWidth / 2} y={geometry.volumeY(candle.volume)} width={geometry.candleWidth} height={VOLUME_BOTTOM - geometry.volumeY(candle.volume)} className="volume-bar" />
            </g>
          );
        })}

        <polyline points={linePath(sma20)} className="indicator-line indicator-sma20" />
        <polyline points={linePath(sma50)} className="indicator-line indicator-sma50" />
        <line x1={LEFT} x2={RIGHT} y1={geometry.y(support)} y2={geometry.y(support)} className="level-line support-line" />
        <line x1={LEFT} x2={RIGHT} y1={geometry.y(resistance)} y2={geometry.y(resistance)} className="level-line resistance-line" />
        <text x={LEFT + 7} y={geometry.y(support) - 6} className="level-label support-label">SUPPORT</text>
        <text x={LEFT + 7} y={geometry.y(resistance) - 6} className="level-label resistance-label">RESISTANCE</text>

        {hovered && hoveredVisibleIndex !== null ? (
          <g className="chart-crosshair">
            <line x1={geometry.x(hoveredVisibleIndex)} x2={geometry.x(hoveredVisibleIndex)} y1={PRICE_TOP} y2={VOLUME_BOTTOM} />
            <line x1={LEFT} x2={RIGHT} y1={geometry.y(hovered.close)} y2={geometry.y(hovered.close)} />
          </g>
        ) : null}

        {visible.map((candle, index) => index % Math.max(1, Math.ceil(visible.length / 6)) === 0 ? (
          <text key={`time-${candle.timestamp}`} x={geometry.x(index)} y={546} textAnchor="middle" className="chart-time-label">
            {new Date(candle.timestamp).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: timeframe === "4h" ? undefined : "2-digit" })}
          </text>
        ) : null)}
      </svg>
    </div>
  );
}
