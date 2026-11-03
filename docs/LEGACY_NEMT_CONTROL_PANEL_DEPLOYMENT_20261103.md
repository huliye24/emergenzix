# Legacy NEMT control panel deployment — 2026-11-03

Status: **implemented and deployed** to the legacy public application at `https://postyouth.club/trade`.

## Objective

Replace the second, settings-oriented screen with a compact control panel for running supported PAPER backtests. Keep the public application to two primary screens: Overview and Control.

## Deployed UI changes

- Renamed the second navigation entry from Settings to Control and changed its icon to Play.
- Moved test search to the upper-right toolbar.
- Exposed four existing client-side backtests: NEMT critical momentum, moving-average crossover, RSI reversion, and Bollinger breakout.
- Kept symbol, interval, date range, and initial capital controls in a single run bar.
- Reduced results to total return, maximum drawdown, Sharpe ratio, win rate, trade count, and ending equity.
- Displayed Chronos and Qlib as unavailable status cards because the legacy web application has no callable job API for either runtime.
- Removed the browser credential/settings form from the reachable second screen. No exchange key is requested or stored by this control panel.
- Preserved PAPER/read-only labeling. No LIVE execution path was added or enabled.

## Runtime files changed

- `/opt/nemt-app/web/src/components/trade/studio/navigation.ts`
- `/opt/nemt-app/web/src/components/trade/studio/StudioApp.tsx`
- `/opt/nemt-app/web/src/components/trade/studio/pages/ControlPage.tsx`
- `/opt/nemt-app/web/src/components/trade/studio/pages/ControlPage.module.css`

The legacy deployment directory is not a Git checkout; this document is the repository-side deployment record.

## Verification

- `npx tsc --noEmit`: passed.
- Focused ESLint on the new page, navigation, and shell: 0 errors; one pre-existing unused `_` warning in `StudioApp.tsx`.
- `npm run build`: passed with Next.js 16.3.2.
- `nemt-web.service`: active after restart.
- `nemt-api.service`: active.
- Local `/trade` request following redirects: HTTP 200.
- Public `https://postyouth.club/trade` request following redirects: HTTP 200.
- Unauthenticated browser check reached the visitor-code gate as expected.

## Backup and rollback evidence

- Source backup: `/opt/nemt-app/backups/control-panel-before-20261103-140500.tar.gz`
- Source backup SHA-256: `60aae93ddb55c96b919cc462e15d267d75a5953c9b70cf59d2c07c2864d2b96a`
- Previous build: `/opt/nemt-app/backups/.next-before-control-panel-20261103-140500`

## Known limitations

- Backtests use the existing legacy K-line endpoint and run in the browser; this is not a durable server-side experiment queue.
- Chronos and Qlib cannot be launched from this screen until authenticated server-side task APIs, job state, logs, and artifact references exist.
- The control panel does not authorize, schedule, or place real-money orders.
