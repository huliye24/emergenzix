"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

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

type Strategy = {
  id: string;
  name: string;
  symbol: string;
  strategy_type: string;
  fast_window: number;
  slow_window: number;
  status: string;
  created_at: string;
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

export default function DashboardPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [backtests, setBacktests] = useState<BacktestResult[]>([]);
  const [orders, setOrders] = useState<PaperOrder[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [selectedStrategyId, setSelectedStrategyId] = useState("");
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

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

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [nextOverview, nextStrategies, nextBacktests, nextOrders, nextPositions] =
        await Promise.all([
          apiRequest<Overview>("/api/v1/overview"),
          apiRequest<Strategy[]>("/api/v1/strategies"),
          apiRequest<BacktestResult[]>("/api/v1/backtests?limit=8"),
          apiRequest<PaperOrder[]>("/api/v1/paper/orders?limit=8"),
          apiRequest<Position[]>("/api/v1/paper/positions"),
        ]);
      setOverview(nextOverview);
      setStrategies(nextStrategies);
      setBacktests(nextBacktests);
      setOrders(nextOrders);
      setPositions(nextPositions);
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
        }),
      });
      setMessage(
        "Sample backtest saved: " +
          result.number_of_trades +
          " closed trades across " +
          result.bars_count +
          " synthetic bars. This is demonstration data, not market history.",
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
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Emergenzix home">
          <span className="brand-mark">E</span>
          <span className="brand-copy">
            <strong>Emergenzix</strong>
            <small>QUANTITATIVE WORKSPACE</small>
          </span>
        </a>
        <div className="topbar-status">
          <span className="status-dot" />
          <span>PAPER MODE</span>
          <span className="status-divider" />
          <span className="muted">LIVE DISABLED</span>
        </div>
        <button className="button button-quiet refresh-button" onClick={() => void refresh()} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh data"}
        </button>
      </header>

      <section className="hero">
        <div className="eyebrow"><span /> RESEARCH <b>→</b> VALIDATION <b>→</b> EXECUTION</div>
        <h1>Turn hypotheses<br /><em>into evidence.</em></h1>
        <p>
          A focused workspace for strategy experiments, reproducible backtests,
          and paper-only trading. Build knowledge before adding risk.
        </p>
        <div className="hero-tags">
          <span>EARLY ACCESS · 0.1</span>
          <span>NO EXCHANGE CONNECTION</span>
          <span>SYNTHETIC SAMPLE DATA</span>
        </div>
      </section>

      {error ? <div className="notice notice-error" role="alert"><strong>Action needed</strong><span>{error}</span></div> : null}
      {message ? <div className="notice notice-success" role="status"><strong>Completed</strong><span>{message}</span></div> : null}

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

      <section className="content-grid">
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

        <article className="panel">
          <div className="panel-heading">
            <div>
              <span className="section-index">02 / EVALUATION</span>
              <h2>Run a backtest</h2>
            </div>
            <span className="panel-mark panel-mark-green">BT</span>
          </div>
          <p className="panel-description">
            Run the selected strategy on a deterministic 120-bar synthetic series. Fees and slippage are included; results are saved for review.
          </p>
          <div className="selected-strategy">
            <span className="selected-label">SELECTED STRATEGY</span>
            {selectedStrategy ? (
              <>
                <strong>{selectedStrategy.name}</strong>
                <span>{selectedStrategy.symbol} · fast {selectedStrategy.fast_window} / slow {selectedStrategy.slow_window}</span>
              </>
            ) : (
              <strong>Create a strategy first</strong>
            )}
          </div>
          <div className="assumption-grid">
            <div><span>Initial cash</span><strong>$10,000</strong></div>
            <div><span>Fee / side</span><strong>10 bps</strong></div>
            <div><span>Slippage / side</span><strong>5 bps</strong></div>
            <div><span>Execution</span><strong>Next bar open</strong></div>
          </div>
          <button className="button button-primary button-wide" onClick={() => void handleRunBacktest()} disabled={busyAction !== "" || !selectedStrategy}>
            {busyAction === "backtest" ? "Running sample…" : "Run sample backtest"}
          </button>
          <p className="fine-print">Sample data is synthetic and must not be interpreted as historical market performance.</p>

          <div className="subsection-heading">
            <span>Recent backtests</span>
            <span>{backtests.length.toString().padStart(2, "0")}</span>
          </div>
          {backtests.length ? (
            <div className="table-wrap">
              <table>
                <thead><tr><th>STRATEGY</th><th>RETURN</th><th>DRAWDOWN</th><th>TRADES</th></tr></thead>
                <tbody>
                  {backtests.map((backtest) => (
                    <tr key={backtest.id}>
                      <td><strong>{backtest.strategy?.name ?? backtest.symbol}</strong><small>{formatDate(backtest.created_at)}</small></td>
                      <td className={backtest.total_return_pct >= 0 ? "positive" : "negative"}>{formatPercent(backtest.total_return_pct)}</td>
                      <td className="negative">-{backtest.max_drawdown_pct.toFixed(2)}%</td>
                      <td>{backtest.number_of_trades}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty-state">Backtest results will appear here.</div>
          )}
        </article>
      </section>

      <section className="content-grid lower-grid">
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
      </section>

      <footer className="footer">
        <span>EMERGENZIX · PRODUCT BUILD 0.1</span>
        <span>Research first. Verify before promotion.</span>
        <span>{loading ? "SYNCING" : "LOCAL API"} <i className={error ? "footer-dot footer-dot-error" : "footer-dot"} /></span>
      </footer>
    </main>
  );
}
