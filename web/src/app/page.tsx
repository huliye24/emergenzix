"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import MarketCandlestickChart, { type MarketCandle } from "../components/market/MarketCandlestickChart";
import WorkspaceNavigation, { type WorkspaceView } from "../components/shell/WorkspaceNavigation";

type Overview = {
  mode: string;
  live_trading_enabled: boolean;
  strategy_count: number;
  backtest_count: number;
  open_position_count: number;
  paper_cash: number;
  starting_cash: number;
  realized_pnl: number;
};

type BinanceAccountSummary = {
  status: "connected";
  mode: "READ_ONLY";
  quote_balance: { asset: "USDT"; free: string; locked: string; total: string };
  nonzero_asset_count: number;
  permissions: {
    withdrawals: false;
    product_order_permission: false;
    credential_spot_trading: boolean;
    credential_futures: boolean;
    ip_restricted: boolean;
  };
};

type Strategy = {
  id: string;
  name: string;
  symbol: string;
  strategy_type: string;
  fast_window: number;
  slow_window: number;
  status: string;
  created_at: string;
  current_version_id: string;
  current_version: number;
  current_parameters_sha256: string;
};

type BacktestResult = {
  id: string;
  strategy_id: string;
  symbol: string;
  strategy: {
    name: string;
    strategy_type: string;
    fast_window: number;
    slow_window: number;
    version: number;
    version_id: string;
    parameters_sha256: string;
  };
  created_at: string;
  bars_count: number;
  initial_cash: number;
  final_equity: number;
  total_return_pct: number;
  max_drawdown_pct: number;
  number_of_trades: number;
  win_rate_pct: number;
  benchmark_return_pct: number;
  assumptions: {
    execution: string;
    positioning: string;
    fee_bps_per_side: number;
    slippage_bps_per_side: number;
    open_position: string;
  };
  data_provenance?: {
    source: string;
    exchange: string | null;
    symbol: string | null;
    timeframe: string | null;
    dataset_id?: string;
    dataset_sha256?: string;
    start_at?: string;
    end_at?: string;
  };
};

type MarketDataset = {
  id: string;
  sha256: string;
  source: string;
  exchange: string;
  symbol: string;
  timeframe: string;
  start_at: string;
  end_at: string;
  bars_count: number;
};

type TurtleMatrixMetrics = {
  bars_count: number;
  total_return_pct: number;
  max_drawdown_pct: number;
  number_of_trades: number;
  win_rate_pct: number;
  benchmark_return_pct: number;
};

type TurtleMatrixRun = {
  id: string;
  created_at: string;
  input_sha256: string;
  matrix_shape: { assets: number; timeframes: number; systems: number; arms: number };
  split: { method: string; train_fraction: number; warmup_policy: string };
  ranking: Array<{
    rank: number;
    symbol: string;
    timeframe: string;
    system: string;
    oos_excess_return_pct: number;
    in_sample: TurtleMatrixMetrics;
    out_of_sample: TurtleMatrixMetrics;
  }>;
  safety: {
    execution_mode: "BACKTEST_ONLY";
    paper_order_permission: false;
    live_order_permission: false;
    automatic_strategy_promotion: false;
  };
};

type PaperOrder = {
  id: string;
  symbol: string;
  side: "BUY" | "SELL";
  quantity: number;
  price: number;
  notional: number;
  fee: number;
  realized_pnl: number;
  status: string;
  created_at: string;
};

type Position = {
  symbol: string;
  quantity: number;
  avg_entry_price: number;
  updated_at: string;
};

type OhlcvBar = {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

type OperatorStatus = { authenticated: boolean; role: "research_operator" | null };

type MarketIntelligence = {
  symbol: string;
  timeframe: "15m" | "1h" | "4h";
  observed_at: string;
  price: { latest: number; latest_bar_timestamp: string; change_24_bars_pct: number };
  freshness: { latest_completed_at: string; age_seconds: number; stale: boolean; forming_bar_excluded: boolean };
  market_state: {
    regime: string;
    scenario: string;
    confidence: number;
    atr_pct: number;
    support: number;
    resistance: number;
  };
  prediction: {
    method: string;
    direction: "UP" | "DOWN" | "NEUTRAL";
    score: number;
    horizon_bars: number;
    calibrated_probability: false;
    model_consensus: "UP" | "DOWN" | "NEUTRAL" | "MIXED" | "UNAVAILABLE";
    verified_model_count: number;
  };
  observers: {
    nemt: { phase: string; confidence: number; dci: number; direction: string };
  };
  indicators: {
    rsi_14_simple: number;
    sma_20: number;
    sma_50: number;
    sma_20_50_spread_pct: number;
    bollinger_z_20: number;
    realized_volatility_20_pct: number;
    volume_ratio_20: number;
  };
  signal: {
    research_signal: string;
    strategy_candidate: string | null;
    reason: string;
    invalidation: string;
    order_permission: false;
  };
  models: Array<{ name: string; family: string; status: string }>;
  series: Array<{ timestamp: string; close: number }>;
  candles: MarketCandle[];
  safety: {
    advisory_only: true;
    execution_decision: "NO_ACTION";
    paper_order_permission: false;
    live_order_permission: false;
    live_trading_enabled: false;
  };
};

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof payload.detail === "string" ? payload.detail : "Request failed";
    throw new Error(detail);
  }
  return payload as T;
}

function formatMoney(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(value);
}

function formatPercent(value: number): string {
  return (value > 0 ? "+" : "") + value.toFixed(2) + "%";
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatBacktestSpan(timeframe: string, bars: number): string {
  const hours = bars * ({ "1h": 1, "4h": 4, "1d": 24 }[timeframe] ?? 1);
  if (hours < 24) return `约 ${hours} 小时`;
  const days = Math.round(hours / 24);
  if (days < 365) return `约 ${days} 天`;
  return `约 ${(days / 365).toFixed(1)} 年`;
}

function buildSyntheticBars(): OhlcvBar[] {
  const bars: OhlcvBar[] = [];
  let previousClose = 100;
  for (let index = 0; index < 120; index += 1) {
    const close =
      100 +
      index * 0.12 +
      Math.sin(index / 4.5) * 4 +
      Math.sin(index / 11) * 2;
    const open = index === 0 ? close : previousClose;
    bars.push({
      timestamp: new Date(Date.UTC(2026, 0, 1 + index)).toISOString(),
      open,
      high: Math.max(open, close) * 1.008,
      low: Math.min(open, close) * 0.992,
      close,
      volume: 1000 + index * 3,
    });
    previousClose = close;
  }
  return bars;
}


const WORKSPACE_VIEW_META: Record<
  WorkspaceView,
  { index: string; kicker: string; title: string; description: string }
> = {
  overview: {
    index: "00",
    kicker: "OVERVIEW",
    title: "Turn hypotheses into evidence.",
    description: "Strategy development, evaluation, and paper-only operations in one place.",
  },
  market: {
    index: "01",
    kicker: "MARKET INTELLIGENCE",
    title: "市场观测与信号",
    description: "用已完成 K 线识别市场状态，展示可审计的预测基线与策略候选。信号仅供研究，不授权下单。",
  },
  strategies: {
    index: "02",
    kicker: "STRATEGY LAB",
    title: "Strategy definitions",
    description: "Create and inspect strategy definitions. A saved strategy is a research artifact, not approval to trade.",
  },
  backtests: {
    index: "03",
    kicker: "EVALUATION",
    title: "Backtest evidence",
    description: "Evaluate strategies against public exchange candles or clearly labeled synthetic demo data, with assumptions recorded.",
  },
  paper: {
    index: "04",
    kicker: "PAPER OPERATIONS",
    title: "Simulated execution",
    description: "Simulate orders at an entered reference price. No exchange is contacted and no real funds are used.",
  },
  activity: {
    index: "05",
    kicker: "ACTIVITY LOG",
    title: "Recorded paper activity",
    description: "Review simulated fills, positions, and realized profit or loss. These records are not exchange execution history.",
  },
};

export default function DashboardPage() {
  const [activeView, setActiveView] = useState<WorkspaceView>("overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [binanceAccount, setBinanceAccount] = useState<BinanceAccountSummary | null>(null);
  const [binanceStatus, setBinanceStatus] = useState("正在连接…");
  const [operatorAuthenticated, setOperatorAuthenticated] = useState(false);
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [backtests, setBacktests] = useState<BacktestResult[]>([]);
  const [orders, setOrders] = useState<PaperOrder[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [selectedStrategyId, setSelectedStrategyId] = useState("");
  const [backtestSymbol, setBacktestSymbol] = useState("BTCUSDT");
  const [backtestTimeframe, setBacktestTimeframe] = useState("4h");
  const [backtestBars, setBacktestBars] = useState("500");
  const [backtestInitialCash, setBacktestInitialCash] = useState("10000");
  const [backtestSystem, setBacktestSystem] = useState("20-10");
  const [backtestFeeBps, setBacktestFeeBps] = useState("10");
  const [backtestSlippageBps, setBacktestSlippageBps] = useState("5");
  const [backtestWorkspace, setBacktestWorkspace] = useState<"catalog" | "turtle">("catalog");
  const [lastBacktestReceipt, setLastBacktestReceipt] = useState<BacktestResult | null>(null);
  const [matrixBars, setMatrixBars] = useState("1000");
  const [turtleMatrices, setTurtleMatrices] = useState<TurtleMatrixRun[]>([]);
  const [latestTurtleMatrix, setLatestTurtleMatrix] = useState<TurtleMatrixRun | null>(null);
  const [matrixProgress, setMatrixProgress] = useState("");
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [marketSymbol, setMarketSymbol] = useState("BTCUSDT");
  const [marketTimeframe, setMarketTimeframe] = useState<"15m" | "1h" | "4h">("1h");
  const [marketSnapshot, setMarketSnapshot] = useState<MarketIntelligence | null>(null);
  const [marketLoading, setMarketLoading] = useState(false);
  const [marketError, setMarketError] = useState("");

  const [strategyName, setStrategyName] = useState("BTC trend baseline");
  const [strategySymbol, setStrategySymbol] = useState("BTCUSDT");
  const [fastWindow, setFastWindow] = useState("5");
  const [slowWindow, setSlowWindow] = useState("20");

  const [orderSymbol, setOrderSymbol] = useState("BTCUSDT");
  const [orderSide, setOrderSide] = useState<"BUY" | "SELL">("BUY");
  const [orderQuantity, setOrderQuantity] = useState("0.01");
  const [orderPrice, setOrderPrice] = useState("50000");

  const selectedStrategy = useMemo(
    () => strategies.find((strategy) => strategy.id === selectedStrategyId) ?? strategies[0],
    [strategies, selectedStrategyId],
  );
  const selectedBacktestStrategy = useMemo(
    () => {
      const [entryWindow, exitWindow] = backtestSystem.split("-").map(Number);
      return (
      strategies.find(
        (strategy) =>
          strategy.strategy_type === "turtle_breakout" &&
          strategy.symbol === backtestSymbol &&
          strategy.slow_window === entryWindow &&
          strategy.fast_window === exitWindow,
      ) ?? null
      );
    },
    [strategies, backtestSymbol, backtestSystem],
  );
  const turtleBacktests = useMemo(
    () => backtests.filter((backtest) => backtest.strategy?.strategy_type === "turtle_breakout"),
    [backtests],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const accountRequest = apiRequest<BinanceAccountSummary>(
        "/api/v1/exchange/binance/account-summary",
      )
        .then((account) => ({ account, error: "" }))
        .catch(() => ({ account: null, error: "账户不可用" }));
      const [nextOverview, nextStrategies, nextBacktests, nextOrders, nextPositions, nextMatrices, operatorStatus] =
        await Promise.all([
          apiRequest<Overview>("/api/v1/overview"),
          apiRequest<Strategy[]>("/api/v1/strategies"),
          apiRequest<BacktestResult[]>("/api/v1/backtests?limit=50"),
          apiRequest<PaperOrder[]>("/api/v1/paper/orders?limit=8"),
          apiRequest<Position[]>("/api/v1/paper/positions"),
          apiRequest<TurtleMatrixRun[]>("/api/v1/turtle-matrices?limit=10"),
          apiRequest<OperatorStatus>("/api/operator"),
        ]);
      const accountResult = await accountRequest;
      setOverview(nextOverview);
      setStrategies(nextStrategies);
      setBacktests(nextBacktests);
      setOrders(nextOrders);
      setPositions(nextPositions);
      setTurtleMatrices(nextMatrices);
      setLatestTurtleMatrix((current) => current ?? nextMatrices[0] ?? null);
      setOperatorAuthenticated(operatorStatus.authenticated);
      setBinanceAccount(accountResult.account);
      setBinanceStatus(accountResult.error);
      setSelectedStrategyId((current) => current || nextStrategies[0]?.id || "");
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const loadMarketSnapshot = useCallback(async () => {
    setMarketLoading(true);
    try {
      const snapshot = await apiRequest<MarketIntelligence>(
        "/api/v1/market-intelligence?symbol=" + encodeURIComponent(marketSymbol) +
          "&timeframe=" + encodeURIComponent(marketTimeframe),
      );
      setMarketSnapshot(snapshot);
      setMarketError("");
    } catch (caught) {
      setMarketError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setMarketLoading(false);
    }
  }, [marketSymbol, marketTimeframe]);

  useEffect(() => {
    if (activeView !== "market") return;
    void loadMarketSnapshot();
    const timer = window.setInterval(() => void loadMarketSnapshot(), 30_000);
    return () => window.clearInterval(timer);
  }, [activeView, loadMarketSnapshot]);

  async function handleCreateStrategy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusyAction("strategy");
    setError("");
    setMessage("");
    try {
      const strategy = await apiRequest<Strategy>("/api/v1/strategies", {
        method: "POST",
        body: JSON.stringify({
          name: strategyName,
          symbol: strategySymbol.trim().toUpperCase(),
          strategy_type: "sma_crossover",
          fast_window: Number(fastWindow),
          slow_window: Number(slowWindow),
        }),
      });
      setSelectedStrategyId(strategy.id);
      setMessage("Strategy created. Run a backtest to evaluate it before any promotion.");
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusyAction("");
    }
  }

  async function handleRunBacktest() {
    if (!selectedBacktestStrategy) {
      setError("The selected Turtle baseline is not available.");
      return;
    }
    setBusyAction("backtest");
    setError("");
    setMessage("");
    try {
      const marketData = await apiRequest<{
        exchange: string;
        symbol: string;
        timeframe: string;
        bars: OhlcvBar[];
      }>(
        "/api/v1/market-data/ohlcv?exchange=binance&symbol=" +
          encodeURIComponent(backtestSymbol) +
          "&timeframe=" + encodeURIComponent(backtestTimeframe) +
          "&limit=" + encodeURIComponent(backtestBars),
      );
      const dataset = await apiRequest<MarketDataset>("/api/v1/datasets", {
        method: "POST",
        body: JSON.stringify({
          source: "ccxt_public_ohlcv",
          exchange: marketData.exchange,
          symbol: marketData.symbol,
          timeframe: marketData.timeframe,
          bars: marketData.bars,
        }),
      });
      const result = await apiRequest<BacktestResult>("/api/v1/backtests", {
        method: "POST",
        body: JSON.stringify({
          strategy_id: selectedBacktestStrategy.id,
          strategy_version_id: selectedBacktestStrategy.current_version_id,
          dataset_id: dataset.id,
          initial_cash: Number(backtestInitialCash),
          fee_bps: Number(backtestFeeBps),
          slippage_bps: Number(backtestSlippageBps),
        }),
      });
      setLastBacktestReceipt(result);
      setMessage(
        `实验已保存：${dataset.bars_count} 根 ${dataset.timeframe} K 线，` +
          `${result.number_of_trades} 笔已平仓交易。数据 ${dataset.sha256.slice(0, 12)}…`,
      );
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusyAction("");
    }
  }

  async function handleRunTurtleMatrix() {
    setBusyAction("turtle-matrix");
    setError("");
    setMessage("");
    setMatrixProgress("准备 9 份市场快照");
    try {
      const symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT"] as const;
      const timeframes = ["1h", "4h", "1d"] as const;
      const datasets: Array<{ symbol: string; timeframe: string; dataset_id: string }> = [];

      for (let batch = 0; batch < timeframes.length; batch += 1) {
        const timeframe = timeframes[batch];
        setMatrixProgress(`冻结行情 ${batch * symbols.length + 1}–${(batch + 1) * symbols.length} / 9`);
        const registered = await Promise.all(
          symbols.map(async (symbol) => {
            const marketData = await apiRequest<{
              exchange: string;
              symbol: string;
              timeframe: string;
              bars: OhlcvBar[];
            }>(
              "/api/v1/market-data/ohlcv?exchange=binance&symbol=" +
                encodeURIComponent(symbol) +
                "&timeframe=" + encodeURIComponent(timeframe) +
                "&limit=" + encodeURIComponent(matrixBars),
            );
            const dataset = await apiRequest<MarketDataset>("/api/v1/datasets", {
              method: "POST",
              body: JSON.stringify({
                source: "ccxt_public_ohlcv",
                exchange: marketData.exchange,
                symbol: marketData.symbol,
                timeframe: marketData.timeframe,
                bars: marketData.bars,
              }),
            });
            return { symbol, timeframe, dataset_id: dataset.id };
          }),
        );
        datasets.push(...registered);
      }

      setMatrixProgress("计算 18 个样本外组合");
      const result = await apiRequest<TurtleMatrixRun>("/api/v1/turtle-matrices", {
        method: "POST",
        body: JSON.stringify({
          datasets,
          train_fraction: 0.7,
          initial_cash: Number(backtestInitialCash),
          fee_bps: Number(backtestFeeBps),
          slippage_bps: Number(backtestSlippageBps),
        }),
      });
      setLatestTurtleMatrix(result);
      setMessage(`矩阵 ${result.id} 已保存：18 个组合完成固定 70/30 样本外对照。`);
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusyAction("");
      setMatrixProgress("");
    }
  }

  async function handleRunSyntheticBacktest() {
    if (!selectedStrategy) {
      setError("Create or select a strategy before running a backtest.");
      return;
    }
    setBusyAction("backtest");
    setError("");
    setMessage("");
    try {
      const result = await apiRequest<BacktestResult>("/api/v1/backtests", {
        method: "POST",
        body: JSON.stringify({
          strategy_id: selectedStrategy.id,
          bars: buildSyntheticBars(),
          initial_cash: 10000,
          fee_bps: 10,
          slippage_bps: 5,
          data_source: "synthetic_demo",
          data_symbol: selectedStrategy.symbol,
          data_timeframe: "1d",
        }),
      });
      setMessage(
        "Synthetic demo backtest saved: " +
          result.number_of_trades +
          " closed trades across " +
          result.bars_count +
          " generated bars. These are not historical market data.",
      );
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusyAction("");
    }
  }

  async function handlePaperOrder(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusyAction("order");
    setError("");
    setMessage("");
    try {
      const order = await apiRequest<PaperOrder>("/api/v1/paper/orders", {
        method: "POST",
        body: JSON.stringify({
          symbol: orderSymbol.trim().toUpperCase(),
          side: orderSide,
          quantity: Number(orderQuantity),
          reference_price: Number(orderPrice),
        }),
      });
      setMessage(
        order.side +
          " paper order filled at the entered reference price. Fee: " +
          formatMoney(order.fee) +
          ". No exchange was contacted.",
      );
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusyAction("");
    }
  }

  return (
    <div className="dashboard-shell">
      <WorkspaceNavigation activeView={activeView} onNavigate={setActiveView} />
      <main className="app-shell">
      <header className="topbar">
        <div className="page-context">
          <small>EMERGENZIX / {WORKSPACE_VIEW_META[activeView].kicker}</small>
          <strong>{WORKSPACE_VIEW_META[activeView].title}</strong>
        </div>
        <div className="exchange-balance" aria-live="polite">
          <span>BINANCE SPOT · READ ONLY</span>
          <strong>
            {binanceAccount
              ? new Intl.NumberFormat("en-US", { maximumFractionDigits: 8 }).format(
                  Number(binanceAccount.quote_balance.total),
                ) + " USDT"
              : binanceStatus}
          </strong>
        </div>
        <div className="topbar-status">
          <span className="status-dot" />
          <span>{operatorAuthenticated ? "OPERATOR · BACKTEST" : "VISITOR · VIEW ONLY"}</span>
          <span className="status-divider" />
          <span className="muted">LIVE DISABLED</span>
        </div>
        <button className="button button-quiet refresh-button" onClick={() => void refresh()} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh data"}
        </button>
      </header>

      {activeView === "overview" ? (
      <section className="hero">
        <div className="eyebrow"><span /> RESEARCH <b>→</b> VALIDATION <b>→</b> EXECUTION</div>
        <h1>Turn hypotheses<br /><em>into evidence.</em></h1>
        <p>
          A focused workspace for strategy experiments, reproducible backtests,
          and paper-only trading. Build knowledge before adding risk.
        </p>
        <div className="hero-tags">
          <span>EARLY ACCESS · 0.1</span>
          <span>PUBLIC MARKET DATA · READ ONLY</span>
          <span>PAPER EXECUTION ONLY</span>
        </div>
      </section>
      ) : activeView !== "market" ? (
      <section className="view-header">
        <div className="eyebrow"><span /> WORKSPACE <b>/</b> {WORKSPACE_VIEW_META[activeView].kicker}</div>
        <div className="view-header-row">
          <div>
            <h1>{WORKSPACE_VIEW_META[activeView].title}</h1>
            <p>{WORKSPACE_VIEW_META[activeView].description}</p>
          </div>
          <span className="view-index">{WORKSPACE_VIEW_META[activeView].index}</span>
        </div>
      </section>
      ) : null}

      {error ? <div className="notice notice-error" role="alert"><strong>Action needed</strong><span>{error}</span></div> : null}
      {message ? <div className="notice notice-success" role="status"><strong>Completed</strong><span>{message}</span></div> : null}

      {activeView === "market" ? (
      <section className="market-observer" aria-label="市场实时观测与信号">
        <div className="market-command-bar">
          <label>
            <span>交易品种</span>
            <select value={marketSymbol} onChange={(event) => setMarketSymbol(event.target.value)}>
              <option value="BTCUSDT">BTC / USDT</option>
              <option value="ETHUSDT">ETH / USDT</option>
              <option value="BNBUSDT">BNB / USDT</option>
            </select>
          </label>
          <fieldset>
            <legend>观测周期</legend>
            {(["15m", "1h", "4h"] as const).map((timeframe) => (
              <button key={timeframe} type="button" className={marketTimeframe === timeframe ? "is-active" : ""} onClick={() => setMarketTimeframe(timeframe)}>{timeframe}</button>
            ))}
          </fieldset>
          <div className="market-poll-status">
            <span className={marketSnapshot?.freshness.stale ? "signal-dot stale" : "signal-dot"} />
            <span>{marketSnapshot?.freshness.stale ? "数据已过期" : "30 秒轮询"}</span>
          </div>
          <button className="button button-quiet" type="button" onClick={() => void loadMarketSnapshot()} disabled={marketLoading}>
            {marketLoading ? "更新中…" : "立即更新"}
          </button>
        </div>

        {marketError ? <div className="notice notice-error" role="alert"><strong>行情不可用</strong><span>{marketError}</span></div> : null}
        {marketSnapshot ? (
          <div className="market-observer-grid">
            <article className="market-chart-card">
              <div className="market-price-row">
                <div>
                  <span>{marketSnapshot.symbol} · BINANCE SPOT</span>
                  <strong>{new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 }).format(marketSnapshot.price.latest)}</strong>
                </div>
                <span className={marketSnapshot.price.change_24_bars_pct >= 0 ? "positive" : "negative"}>
                  {formatPercent(marketSnapshot.price.change_24_bars_pct)} / 24 bars
                </span>
              </div>
              <MarketCandlestickChart
                candles={marketSnapshot.candles}
                symbol={marketSnapshot.symbol}
                timeframe={marketSnapshot.timeframe}
                support={marketSnapshot.market_state.support}
                resistance={marketSnapshot.market_state.resistance}
              />
              <div className="market-chart-footer">
                <span>RSI 14 <strong>{marketSnapshot.indicators.rsi_14_simple.toFixed(1)}</strong></span>
                <span>BB Z-Score <strong>{marketSnapshot.indicators.bollinger_z_20.toFixed(2)}</strong></span>
                <span>量比 / 20 <strong>{marketSnapshot.indicators.volume_ratio_20.toFixed(2)}×</strong></span>
                <span>实现波动率 <strong>{marketSnapshot.indicators.realized_volatility_20_pct.toFixed(2)}%</strong></span>
                <span>SMA 20/50 <strong>{formatPercent(marketSnapshot.indicators.sma_20_50_spread_pct)}</strong></span>
                <span>NEMT DCI <strong>{marketSnapshot.observers.nemt.dci.toFixed(2)}</strong></span>
              </div>
            </article>

            <aside className="market-decision-rail">
              <article className="decision-card">
                <span className="decision-step">01 · MARKET STATE</span>
                <strong>{marketSnapshot.market_state.regime.replaceAll("_", " ")}</strong>
                <small>{marketSnapshot.observers.nemt.phase.replaceAll("_", " ")} · DCI {marketSnapshot.observers.nemt.dci.toFixed(2)}</small>
              </article>
              <article className="decision-card">
                <span className="decision-step">02 · BASELINE</span>
                <strong className={`direction-${marketSnapshot.prediction.direction.toLowerCase()}`}>{marketSnapshot.prediction.model_consensus}</strong>
                <small>{marketSnapshot.prediction.verified_model_count} 个已验证模型 · 非校准概率</small>
              </article>
              <article className="decision-card decision-card-signal">
                <span className="decision-step">03 · RESEARCH SIGNAL</span>
                <strong>{marketSnapshot.signal.research_signal.replaceAll("_", " ")}</strong>
                <small>{marketSnapshot.signal.strategy_candidate ?? "暂无策略候选"}</small>
              </article>
              <article className="decision-card decision-card-safe">
                <span className="decision-step">04 · EXECUTION</span>
                <strong>{marketSnapshot.safety.execution_decision.replaceAll("_", " ")}</strong>
                <small>PAPER / LIVE 权限均关闭</small>
              </article>
            </aside>

            <div className="market-model-strip">
              {marketSnapshot.models.map((model) => (
                <div key={model.name}><span>{model.name}</span><strong>{model.status.replaceAll("_", " ")}</strong></div>
              ))}
              <div><span>策略候选</span><strong>{marketSnapshot.signal.strategy_candidate ?? "等待有效状态"}</strong></div>
            </div>
            <div className="market-safety-banner">
              <strong>研究信号 ≠ 交易许可</strong>
              <span>当前页面只做观测与候选策略选择。数据过期、模型制品缺失或风险检查未完成时一律 NO ACTION。</span>
            </div>
          </div>
        ) : <div className="market-loading-state">{marketLoading ? "正在读取公开行情并计算市场状态…" : "等待行情快照"}</div>}
      </section>
      ) : null}

      {activeView === "overview" ? (
      <section className="metrics-grid" aria-label="Product overview">
        <article className="metric-card">
          <span className="metric-label">PAPER CASH</span>
          <strong>{overview ? formatMoney(overview.paper_cash) : "—"}</strong>
          <small>Starting balance {overview ? formatMoney(overview.starting_cash) : "—"}</small>
        </article>
        <article className="metric-card">
          <span className="metric-label">STRATEGIES</span>
          <strong>{overview?.strategy_count ?? "—"}</strong>
          <small>Saved strategy definitions</small>
        </article>
        <article className="metric-card">
          <span className="metric-label">BACKTESTS</span>
          <strong>{overview?.backtest_count ?? "—"}</strong>
          <small>Persisted evaluation runs</small>
        </article>
        <article className="metric-card">
          <span className="metric-label">REALIZED P&amp;L</span>
          <strong className={(overview?.realized_pnl ?? 0) >= 0 ? "positive" : "negative"}>
            {overview ? formatMoney(overview.realized_pnl) : "—"}
          </strong>
          <small>{overview?.open_position_count ?? 0} open paper positions</small>
        </article>
      </section>
      ) : null}

      {activeView === "overview" || activeView === "strategies" || activeView === "backtests" ? (
<section className={"content-grid" + (activeView === "overview" ? "" : " single-view-grid") + (activeView === "backtests" ? " backtest-view-grid" : "")}>

        {activeView !== "backtests" ? (
<article className="panel">
          <div className="panel-heading">
            <div>
              <span className="section-index">01 / STRATEGY LAB</span>
              <h2>Create a strategy</h2>
            </div>
            <span className="panel-mark">SMA</span>
          </div>
          <p className="panel-description">
            Define a simple moving-average crossover. New strategies start as drafts and are not automatically promoted.
          </p>
          <form className="form-stack" onSubmit={handleCreateStrategy}>
            <label>
              Strategy name
              <input value={strategyName} onChange={(event) => setStrategyName(event.target.value)} maxLength={80} required />
            </label>
            <label>
              Market symbol
              <input value={strategySymbol} onChange={(event) => setStrategySymbol(event.target.value.toUpperCase())} pattern="[A-Z0-9]{3,20}" required />
            </label>
            <div className="two-fields">
              <label>
                Fast window
                <input type="number" min={2} max={50} value={fastWindow} onChange={(event) => setFastWindow(event.target.value)} required />
              </label>
              <label>
                Slow window
                <input type="number" min={3} max={100} value={slowWindow} onChange={(event) => setSlowWindow(event.target.value)} required />
              </label>
            </div>
            <button className="button button-primary" type="submit" disabled={busyAction !== ""}>
              {busyAction === "strategy" ? "Saving strategy…" : "Create strategy"}
            </button>
          </form>

          <div className="subsection-heading">
            <span>Saved strategies</span>
            <span>{strategies.length.toString().padStart(2, "0")}</span>
          </div>
          {strategies.length ? (
            <div className="strategy-list">
              {strategies.map((strategy) => (
                <button
                  type="button"
                  className={"strategy-row" + (selectedStrategy?.id === strategy.id ? " selected" : "")}
                  key={strategy.id}
                  onClick={() => setSelectedStrategyId(strategy.id)}
                >
                  <span className="strategy-icon">↗</span>
                  <span className="strategy-info">
                    <strong>{strategy.name}</strong>
                    <small>{strategy.symbol} · SMA {strategy.fast_window}/{strategy.slow_window}</small>
                  </span>
                  <span className="draft-pill">{strategy.status}</span>
                </button>
              ))}
            </div>
          ) : (
            <div className="empty-state">No strategies yet. Create one to begin.</div>
          )}
        </article>
) : null}


        
{activeView !== "strategies" ? (
<article className="panel backtest-workspace-panel">
          {backtestWorkspace === "catalog" ? (
            <>
              <div className="panel-heading">
                <div>
                  <span className="section-index">02 / STRATEGY CATALOG</span>
                  <h2>选择一个策略开始</h2>
                </div>
                <span className="panel-mark panel-mark-green">01</span>
              </div>
              <p className="panel-description">
                每个策略拥有独立参数、实验流程和历史记录。先进入策略，再决定品种、周期和成本。
              </p>
              <div className="backtest-strategy-catalog">
                <button className="backtest-strategy-card" type="button" onClick={() => setBacktestWorkspace("turtle")}>
                  <span className="strategy-card-symbol">T</span>
                  <span className="strategy-card-copy">
                    <small>CLASSIC TREND FOLLOWING</small>
                    <strong>海龟交易法</strong>
                    <span>Donchian 突破 · ATR 风险单位 · 金字塔加仓</span>
                  </span>
                  <span className="strategy-card-meta">
                    <b>2</b> 固定系统
                    <b>{turtleBacktests.length}</b> 次实验
                  </span>
                  <span className="strategy-card-arrow" aria-hidden="true">→</span>
                </button>
              </div>
              <p className="fine-print">当前先跑通海龟交易法。后续策略会以相同方式加入目录，但参数界面彼此独立。</p>
            </>
          ) : (
            <>
              <div className="strategy-detail-toolbar">
                <button className="back-link" type="button" onClick={() => setBacktestWorkspace("catalog")}>
                  ← 策略目录
                </button>
                <span>{turtleBacktests.length} 次已保存实验</span>
              </div>
              <div className="panel-heading strategy-detail-heading">
                <div>
                  <span className="section-index">TURTLE / FIXED WORKFLOW</span>
                  <h2>海龟交易法</h2>
                </div>
                <span className="panel-mark panel-mark-green">T</span>
              </div>
              <p className="panel-description">
                选择海龟系统与市场参数。系统冻结行情快照、固定策略版本并保存完整结果，供后续 AI 对照分析与模型升级。
              </p>
              <div className="experiment-steps" aria-label="固定实验流程">
                <span><b>01</b> 固定系统</span>
                <span><b>02</b> 市场样本</span>
                <span><b>03</b> 交易成本</span>
                <span><b>04</b> 永久回执</span>
              </div>
              <div className="turtle-workspace">
                <div className="turtle-parameter-panel">
                  <div className="backtest-controls form-stack">
                    <div className="two-fields">
                      <label>
                        海龟系统
                        <select value={backtestSystem} onChange={(event) => setBacktestSystem(event.target.value)}>
                          <option value="20-10">系统一 · 20 日突破 / 10 日退出</option>
                          <option value="55-20">系统二 · 55 日突破 / 20 日退出</option>
                        </select>
                      </label>
                      <label>
                        交易品种
                        <select value={backtestSymbol} onChange={(event) => setBacktestSymbol(event.target.value)}>
                          <option value="BTCUSDT">BTC / USDT</option>
                          <option value="ETHUSDT">ETH / USDT</option>
                          <option value="BNBUSDT">BNB / USDT</option>
                        </select>
                      </label>
                    </div>
                    <div className="three-fields">
                      <label>
                        K 线周期
                        <select value={backtestTimeframe} onChange={(event) => setBacktestTimeframe(event.target.value)}>
                          <option value="1h">1 小时</option>
                          <option value="4h">4 小时</option>
                          <option value="1d">1 天</option>
                        </select>
                      </label>
                      <label>
                        样本长度
                        <select value={backtestBars} onChange={(event) => setBacktestBars(event.target.value)}>
                          <option value="120">120 根</option>
                          <option value="250">250 根</option>
                          <option value="500">500 根</option>
                          <option value="1000">1000 根</option>
                        </select>
                      </label>
                      <label>
                        初始资金（USDT）
                        <input type="number" min={100} max={1000000000} step={100} value={backtestInitialCash} onChange={(event) => setBacktestInitialCash(event.target.value)} />
                      </label>
                    </div>
                    <div className="two-fields">
                      <label>
                        手续费 / 单边（bps）
                        <input type="number" min={0} max={1000} step={1} value={backtestFeeBps} onChange={(event) => setBacktestFeeBps(event.target.value)} />
                      </label>
                      <label>
                        滑点 / 单边（bps）
                        <input type="number" min={0} max={1000} step={1} value={backtestSlippageBps} onChange={(event) => setBacktestSlippageBps(event.target.value)} />
                      </label>
                    </div>
                    <div className="duration-readout">
                      <span>{backtestBars} 根 {backtestTimeframe} K 线</span>
                      <strong>{formatBacktestSpan(backtestTimeframe, Number(backtestBars))}</strong>
                    </div>
                  </div>
                </div>
                <aside className="turtle-run-summary">
                  <div className="selected-strategy">
                    <span className="selected-label">FROZEN STRATEGY VERSION</span>
                    {selectedBacktestStrategy ? (
                      <>
                        <strong>{selectedBacktestStrategy.name}</strong>
                        <span>{selectedBacktestStrategy.slow_window} 根突破 · {selectedBacktestStrategy.fast_window} 根退出</span>
                        <span>ATR(20) · 1% 风险单位 · 最多 4 次加仓</span>
                        <code>{selectedBacktestStrategy.current_parameters_sha256}</code>
                      </>
                    ) : <strong>所选海龟策略尚未加载</strong>}
                  </div>
                  <div className="assumption-grid">
                    <div><span>初始资金</span><strong>{formatMoney(Number(backtestInitialCash) || 0)}</strong></div>
                    <div><span>往返摩擦</span><strong>{2 * (Number(backtestFeeBps) + Number(backtestSlippageBps)) || 0} bps</strong></div>
                    <div><span>信号</span><strong>完成 K 线</strong></div>
                    <div><span>成交</span><strong>下一根开盘</strong></div>
                  </div>
                  <button className="button button-primary button-wide" onClick={() => operatorAuthenticated ? void handleRunBacktest() : window.location.assign("/operator")} disabled={busyAction !== "" || !selectedBacktestStrategy}>
                    {busyAction === "backtest" ? "冻结数据并计算…" : operatorAuthenticated ? "运行并保存实验" : "操作员登录后运行"}
                  </button>
                  <p className="fine-print">只读取公开行情；不会创建 PAPER 或 LIVE 订单。</p>
                </aside>
              </div>

              {lastBacktestReceipt ? (
                <div className="experiment-receipt" aria-live="polite">
                  <div><span>实验 ID</span><code>{lastBacktestReceipt.id}</code></div>
                  <div><span>数据 SHA</span><code>{lastBacktestReceipt.data_provenance?.dataset_sha256}</code></div>
                  <div><span>策略 SHA</span><code>{lastBacktestReceipt.strategy.parameters_sha256}</code></div>
                  <div><span>区间</span><strong>{lastBacktestReceipt.data_provenance?.start_at?.slice(0, 10)} → {lastBacktestReceipt.data_provenance?.end_at?.slice(0, 10)}</strong></div>
                </div>
              ) : null}

              <div className="subsection-heading">
                <span>海龟实验历史</span>
                <span>{turtleBacktests.length.toString().padStart(2, "0")} / 50</span>
              </div>
              {turtleBacktests.length ? (
                <div className="table-wrap">
                  <table className="experiment-history-table">
                    <thead><tr><th>实验</th><th>市场</th><th>资金</th><th>收益</th><th>回撤</th><th>交易</th><th>数据指纹</th></tr></thead>
                    <tbody>
                      {turtleBacktests.map((backtest) => (
                        <tr key={backtest.id}>
                          <td><strong>海龟 {backtest.strategy.slow_window}/{backtest.strategy.fast_window}</strong><small>{formatDate(backtest.created_at)}</small></td>
                          <td><strong>{backtest.symbol}</strong><small>{backtest.data_provenance?.timeframe ?? "—"} · {backtest.bars_count} 根</small></td>
                          <td>{formatMoney(backtest.initial_cash)}</td>
                          <td className={backtest.total_return_pct >= 0 ? "positive" : "negative"}>{formatPercent(backtest.total_return_pct)}</td>
                          <td className="negative">-{backtest.max_drawdown_pct.toFixed(2)}%</td>
                          <td>{backtest.number_of_trades}</td>
                          <td><code>{backtest.data_provenance?.dataset_sha256?.slice(0, 12) ?? "legacy"}</code></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <div className="empty-state">还没有海龟实验。设置参数后运行第一次固定回测。</div>}

              <section className="matrix-lab" aria-labelledby="matrix-title">
                <div className="matrix-heading">
                  <div>
                    <span className="section-index">TURTLE / 18-ARM OOS MATRIX</span>
                    <h3 id="matrix-title">海龟实验矩阵</h3>
                    <p>BTC、ETH、BNB × 1h、4h、1d × 20/10、55/20。每个组合固定使用前 70% 训练上下文和后 30% 样本外评分。</p>
                  </div>
                  <span className="matrix-count">18</span>
                </div>
                <div className="matrix-control-row">
                  <label>
                    每组样本
                    <select value={matrixBars} onChange={(event) => setMatrixBars(event.target.value)}>
                      <option value="250">250 根 / 组</option>
                      <option value="500">500 根 / 组</option>
                      <option value="1000">1000 根 / 组</option>
                    </select>
                  </label>
                  <div><span>品种</span><strong>BTC · ETH · BNB</strong></div>
                  <div><span>周期</span><strong>1h · 4h · 1d</strong></div>
                  <div><span>切分</span><strong>70% / 30% OOS</strong></div>
                  <button className="button button-primary" type="button" onClick={() => operatorAuthenticated ? void handleRunTurtleMatrix() : window.location.assign("/operator")} disabled={busyAction !== ""}>
                    {busyAction === "turtle-matrix" ? matrixProgress || "运行矩阵…" : operatorAuthenticated ? "运行 18 组合矩阵" : "操作员登录后运行"}
                  </button>
                </div>
                <div className="matrix-safety-note">
                  统一排名按样本外超额收益排序，仅用于研究导航。不同 K 线周期覆盖的自然时间长度不同，不能把名次直接解释为未来盈利概率。
                </div>

                {latestTurtleMatrix ? (
                  <>
                    <div className="matrix-receipt">
                      <span><small>矩阵 ID</small><code>{latestTurtleMatrix.id}</code></span>
                      <span><small>输入指纹</small><code>{latestTurtleMatrix.input_sha256.slice(0, 16)}…</code></span>
                      <span><small>运行时间</small><strong>{formatDate(latestTurtleMatrix.created_at)}</strong></span>
                      <span><small>历史矩阵</small><strong>{turtleMatrices.length}</strong></span>
                    </div>
                    <div className="table-wrap">
                      <table className="matrix-ranking-table">
                        <thead><tr><th>#</th><th>组合</th><th>样本内</th><th>样本外</th><th>基准</th><th>超额</th><th>回撤</th><th>交易</th></tr></thead>
                        <tbody>
                          {latestTurtleMatrix.ranking.map((row) => (
                            <tr key={`${row.symbol}-${row.timeframe}-${row.system}`}>
                              <td><strong>{row.rank.toString().padStart(2, "0")}</strong></td>
                              <td><strong>{row.symbol.replace("USDT", "")}</strong><small>{row.timeframe} · {row.system}</small></td>
                              <td className={row.in_sample.total_return_pct >= 0 ? "positive" : "negative"}>{formatPercent(row.in_sample.total_return_pct)}</td>
                              <td className={row.out_of_sample.total_return_pct >= 0 ? "positive" : "negative"}>{formatPercent(row.out_of_sample.total_return_pct)}</td>
                              <td>{formatPercent(row.out_of_sample.benchmark_return_pct)}</td>
                              <td className={row.oos_excess_return_pct >= 0 ? "positive" : "negative"}>{formatPercent(row.oos_excess_return_pct)}</td>
                              <td className="negative">-{row.out_of_sample.max_drawdown_pct.toFixed(2)}%</td>
                              <td>{row.out_of_sample.number_of_trades}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                ) : <div className="empty-state matrix-empty">尚无矩阵记录。运行后会保存 18 个组合的统一样本外排名。</div>}
              </section>
            </>
          )}
        </article>
) : null}

      
</section>
) : null}

      {activeView === "overview" || activeView === "paper" || activeView === "activity" ? (
<section className={"content-grid lower-grid" + (activeView === "overview" ? "" : " single-view-grid")}>

        {activeView !== "activity" ? (
<article className="panel">
          <div className="panel-heading">
            <div>
              <span className="section-index">03 / PAPER EXECUTION</span>
              <h2>Simulate an order</h2>
            </div>
            <span className="panel-mark panel-mark-orange">P</span>
          </div>
          <p className="panel-description">
            Orders are simulated at the reference price you enter. The API applies a notional cap, fees, cash checks, and position checks.
          </p>
          <form className="form-stack" onSubmit={handlePaperOrder}>
            <label>
              Market symbol
              <input value={orderSymbol} onChange={(event) => setOrderSymbol(event.target.value.toUpperCase())} pattern="[A-Z0-9]{3,20}" required />
            </label>
            <div className="side-switch" role="group" aria-label="Order side">
              <button type="button" className={orderSide === "BUY" ? "side-active" : ""} onClick={() => setOrderSide("BUY")}>BUY</button>
              <button type="button" className={orderSide === "SELL" ? "side-active side-sell" : ""} onClick={() => setOrderSide("SELL")}>SELL</button>
            </div>
            <div className="two-fields">
              <label>
                Quantity
                <input type="number" min="0.00000001" step="any" value={orderQuantity} onChange={(event) => setOrderQuantity(event.target.value)} required />
              </label>
              <label>
                Reference price
                <input type="number" min="0.00000001" step="any" value={orderPrice} onChange={(event) => setOrderPrice(event.target.value)} required />
              </label>
            </div>
            <div className="order-estimate">
              <span>Estimated notional</span>
              <strong>{formatMoney((Number(orderQuantity) || 0) * (Number(orderPrice) || 0))}</strong>
            </div>
            <button className="button button-primary" type="submit" disabled={busyAction !== ""}>
              {busyAction === "order" ? "Simulating order…" : "Submit paper order"}
            </button>
          </form>
          <div className="subsection-heading"><span>Open positions</span><span>{positions.length.toString().padStart(2, "0")}</span></div>
          {positions.length ? (
            <div className="position-list">
              {positions.map((position) => (
                <div className="position-row" key={position.symbol}>
                  <span><strong>{position.symbol}</strong><small>Avg. entry {formatMoney(position.avg_entry_price)}</small></span>
                  <strong>{position.quantity.toLocaleString(undefined, { maximumFractionDigits: 8 })}</strong>
                </div>
              ))}
            </div>
          ) : <div className="empty-state">No open paper positions.</div>}
        </article>
) : null}


        
{activeView !== "paper" ? (
<article className="panel">
          <div className="panel-heading">
            <div>
              <span className="section-index">04 / ACTIVITY LOG</span>
              <h2>Paper order history</h2>
            </div>
            <span className="panel-mark">LOG</span>
          </div>
          <p className="panel-description">
            Simulated fills and realized profit/loss are persisted in the local development database.
          </p>
          {orders.length ? (
            <div className="table-wrap">
              <table>
                <thead><tr><th>ORDER</th><th>PRICE</th><th>NOTIONAL</th><th>REALIZED P&amp;L</th></tr></thead>
                <tbody>
                  {orders.map((order) => (
                    <tr key={order.id}>
                      <td><span className={"side-label " + (order.side === "BUY" ? "buy-label" : "sell-label")}>{order.side}</span><small>{order.symbol} · {formatDate(order.created_at)}</small></td>
                      <td>{formatMoney(order.price)}</td>
                      <td>{formatMoney(order.notional)}</td>
                      <td className={order.realized_pnl > 0 ? "positive" : order.realized_pnl < 0 ? "negative" : ""}>
                        {order.side === "SELL" ? formatMoney(order.realized_pnl) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty-state tall-empty">
              <span className="empty-symbol">⌁</span>
              <strong>No paper activity yet</strong>
              <span>Your simulated orders will be recorded here.</span>
            </div>
          )}
          <div className="risk-note">
            <span className="risk-icon">!</span>
            <span><strong>Risk boundary</strong> · Paper mode only. No exchange is contacted, and no real funds are used.</span>
          </div>
        </article>
) : null}

      
</section>
) : null}

      <footer className="footer">
        <span>EMERGENZIX · PRODUCT BUILD 0.1</span>
        <span>Research first. Verify before promotion.</span>
        <span>{loading ? "SYNCING" : "LOCAL API"} <i className={error ? "footer-dot footer-dot-error" : "footer-dot"} /></span>
      </footer>
      </main>
    </div>
  );
}
