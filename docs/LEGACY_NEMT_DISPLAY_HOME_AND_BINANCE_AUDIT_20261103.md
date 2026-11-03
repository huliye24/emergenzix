# Legacy NEMT display home and Binance audit — 2026-11-03

## Status

The concise display-style portfolio home is implemented and deployed to the legacy public NEMT runtime on host alias `dev`.

The production Binance account was **not connected** because the supplied API key currently reports withdrawal permission. This is a deliberate fail-closed result.

## Objectives

1. Reduce the portfolio home to a numerical, icon-led monitoring display.
2. Determine whether the displayed balance is a real Binance read-only account or a DEMO account.
3. Verify the supplied Binance credential without exposing the key, secret, or asset balances.

## UI acceptance result

- The oversized bilingual heading and explanatory subtitle are removed.
- The page title is reduced to `组合`.
- Refresh is an icon-only accessible button.
- Runtime state uses four compact icon indicators: account source, market feed, PAPER execution, and open orders.
- Panel headings are reduced to `资产` and `订单` with numeric count badges.
- Long empty-state sentences, duplicated navigation cards, the valuation footnote, and the long footer warning are removed.
- The footer is reduced to a lock icon plus `PAPER · READ ONLY`.
- The top bar no longer presents DEMO `0.00` as a Binance account balance; it now shows `BINANCE — 未连接` until the API returns a connected Binance source.

## Binance audit result

- The local credential file was found at `E:\女校影视音乐企划\biance api.txt`.
- The local key and secret fingerprints match the credentials already installed in `/etc/nemt-app/api.env`; no credential values were printed.
- The current account API reports `mode=DEMO`, `source=demo`, zero account assets, and is not a Binance account snapshot.
- A signed read-only Binance `GET /api/v3/account` preflight succeeded.
- The preflight reported `canTrade=true` and `canWithdraw=true`.
- The application adapter reported `writes_allowed_by_app=false`.
- Because withdrawal permission is forbidden by project policy, the key was not connected to the public account display and no backend mode or execution flag was changed.

Before connection, create or update a Binance API key so that withdrawal is disabled. The preferred least-privilege configuration also disables spot trading and retains only the permissions required to read account balances. IP restriction to the server egress IP is recommended.

## Changed runtime files

- `/opt/nemt-app/web/src/components/trade/studio/pages/OverviewPage.tsx`
- `/opt/nemt-app/web/src/components/trade/studio/pages/OverviewPage.module.css`
- `/opt/nemt-app/web/src/components/trade/studio/layout/StudioTopbar.tsx`

No backend file, credential file, API permission, exchange order path, or execution-mode setting was changed.

## Verification performed

- `npx tsc --noEmit` — passed.
- Focused ESLint on the changed TSX files — passed with no reported warnings or errors.
- `npm run build` — passed under Next.js 16.3.2; all 14 routes completed.
- `nemt-web.service` and `nemt-api.service` — active.
- Local `/trade` — HTTP 200.
- Public `/trade` — HTTP 200.
- Authenticated account metadata after deployment remains `mode=DEMO`, `source=demo`, and zero account assets.

Authenticated browser automation was not performed because the visitor code is intentionally excluded from automation logs.

## Rollback evidence

- Source backup: `/opt/nemt-app/backups/display-home-before-20261103-130000.tar.gz`
- Source backup SHA-256: `bf07650f38674689e73fc3b6802f7bd1545353326c64bbf48671d1f042e2492f`
- Previous build: `/opt/nemt-app/backups/.next-before-display-home-20261103-130000`
