# Legacy NEMT classic backtest layout deployment — 2026-11-03

Status: **implemented and deployed** to `https://postyouth.club/trade?section=api`.

## Objective

Move global experiment inputs into one top row, present strategies as a vertical rail, and add a real Buy & Hold classic benchmark for same-data comparison.

## Deployed layout

- Top row: Classic mode, BTCUSDT, start date, end date, 1h/4h/1d interval, initial capital, and Run.
- Left vertical rail: B&H, MA, RSI, BB, and NEMT. NEMT is visually separated as the custom strategy.
- Middle column: parameters for the selected strategy, reset action, and fixed cost assumptions.
- Right result area: data-lake evidence, price chart, ending equity, strategy return, excess return versus B&H, drawdown, Sharpe, win rate, and trade count.
- Responsive behavior changes the vertical rail to a horizontal scrollable rail on narrow screens.

## Classic benchmark behavior

- Added `buy_hold` to the existing client backtest engine.
- B&H enters once using next-bar-open execution and exits at the final close.
- The same fixed fee and slippage assumptions used by other strategies apply to B&H.
- Every non-B&H run also calculates B&H on the exact same candles, dates, timeframe, and initial capital.
- The `Δ` result is strategy total return minus B&H total return.

## Verification

- `npx tsc --noEmit`: passed after adding the B&H compatibility entry to the unreachable legacy strategy editor.
- Focused ESLint for the active control page and backtest engine: passed with 0 errors and 0 warnings.
- `npm run build`: passed with Next.js 16.3.2.
- Deterministic B&H smoke test: 1 trade, ending equity 10,968.624845992847 from 10,000, total return 9.686248459928471% after costs.
- `nemt-web.service`: active.
- `nemt-api.service`: active.
- Public control URL: HTTP 200.

The removed legacy `StrategiesPage.tsx` still has an existing `react-hooks/set-state-in-effect` lint violation when linted directly. It remains unreachable and was changed only to add the required empty B&H parameter mapping; the active files pass focused lint.

## Runtime files changed

- `/opt/nemt-app/web/src/components/trade/studio/backtest/engine.ts`
- `/opt/nemt-app/web/src/components/trade/studio/pages/ControlPage.tsx`
- `/opt/nemt-app/web/src/components/trade/studio/pages/ControlPage.module.css`
- `/opt/nemt-app/web/src/components/trade/studio/pages/StrategiesPage.tsx` (type compatibility only; still unreachable)

## Backup

- Main source backup: `/opt/nemt-app/backups/control-panel-v3-before-20261103-160500.tar.gz`
- Main source backup SHA-256: `d8c59ba0b8c9225add56f41ce7f0d5fca586757107a9a15ed63bb99135afed48`
- Legacy editor backup: `/opt/nemt-app/backups/StrategiesPage-before-control-panel-v3-20261103-160500.tsx`
- Previous build: `/opt/nemt-app/backups/.next-before-control-panel-v3-20261103-160500`

## Safety boundary

This remains a PAPER research workflow. A benchmark result, strategy result, or positive excess return cannot authorize an exchange order or promote a strategy to LIVE execution.
