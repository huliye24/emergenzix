# Legacy NEMT two-screen UI deployment — 2026-11-03

## Status

Implemented and deployed to the legacy public NEMT runtime on host alias `dev`.

## Objective

Reduce the public product workspace to two screens: a monitoring overview and settings. Market and execution data remain visible on the overview rather than living behind separate product pages.

## Final information architecture

### 1. Overview (`/trade`)

The first screen displays:

- Binance connection state; disconnected accounts remain explicitly labelled.
- Total estimated equity, available USDT, BTC holdings, and BTC/USDT market price.
- Compact account-source, market-feed, PAPER, and open-order indicators.
- Asset rows and recent order rows.
- A lock-labelled PAPER/read-only boundary.

When an approved Binance integration is eventually enabled, the same components will consume the connected balance and order responses. No synthetic connected-state preview was added.

### 2. Settings (`/trade?section=api`)

The second screen retains connection/runtime information, configuration status, session controls, and logout. It does not grant LIVE order permission.

## Acceptance result

- Desktop navigation contains only `总览` and `设置`.
- Mobile navigation contains only `总览` and `设置`.
- `portfolio` and `api` are the only accepted studio sections.
- `MarketsPage` and `ExecutionPage` are no longer imported or rendered by `StudioApp`.
- Legacy aliases for market, trade, and orders are removed.
- `/trade?section=markets` and `/trade?section=execution` fall back to the overview after authentication.
- Market metrics and recent orders remain embedded in the overview.
- Underlying market/execution components and records were not deleted.

## Changed runtime files

- `/opt/nemt-app/web/src/components/trade/studio/navigation.ts`
- `/opt/nemt-app/web/src/components/trade/studio/types.ts`
- `/opt/nemt-app/web/src/components/trade/studio/StudioApp.tsx`
- `/opt/nemt-app/web/src/components/trade/studio/pages/OverviewPage.tsx`
- `/opt/nemt-app/web/src/components/trade/studio/layout/StudioTopbar.tsx`
- `/opt/nemt-app/web/src/components/trade/studio/orders/OrdersPanel.tsx`
- `/opt/nemt-app/web/src/components/trade/studio/pages/AssetsPage.tsx`
- `/opt/nemt-app/web/src/components/trade/studio/pages/TradingWorkbench.tsx`

The final three files are retained legacy components whose internal navigation targets were changed to the overview so the narrower `StudioSection` type remains correct.

## Verification performed

- `npx tsc --noEmit` — passed.
- Focused ESLint on all changed TypeScript/TSX files — 0 errors; one pre-existing unused `_` warning in `StudioApp.tsx` remains.
- `npm run build` — passed under Next.js 16.3.2; all 14 routes completed.
- Source inspection confirmed exactly two accepted sections, two desktop navigation items, two mobile navigation items, and two render branches.
- `nemt-web.service` and `nemt-api.service` — active.
- Local `/trade` — HTTP 200.
- Public legacy market/execution URLs — HTTP 200 and resolve through the application fallback.

Authenticated browser automation was not performed because the visitor code is intentionally excluded from automation logs.

## Rollback evidence

- Source backup: `/opt/nemt-app/backups/two-screen-before-20261103-134000.tar.gz`
- Source backup SHA-256: `0a24054a0e55c80b3da72a99fa17f18b07aa2c3147282e91c799879bc45f1cb5`
- Previous build: `/opt/nemt-app/backups/.next-before-two-screen-20261103-134000`

## Safety boundary

- This is a UI/routing change only.
- Binance remains disconnected because the currently supplied key reports withdrawal permission.
- LIVE order placement remains disabled.
- Existing real orders were not queried, canceled, or modified.
