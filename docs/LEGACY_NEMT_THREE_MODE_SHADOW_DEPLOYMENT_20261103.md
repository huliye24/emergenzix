# Legacy NEMT three-mode navigation and shadow session deployment

Date: 2026-11-03

Target: `https://postyouth.club/trade`

Status: deployed and verified

## Objective

Reduce the trading workspace to three primary modes and add a clearly bounded PAPER experience:

- **实盘** — read-only Binance account and order monitor. This change does not grant LIVE execution permission.
- **回测** — data-lake-backed historical strategy evaluation.
- **影子** — browser-session PAPER following with selectable strategy, bar interval, and initial capital.

The active navigation and PAPER action states use a restrained violet accent. Cyan remains reserved for market data and connection cues.

## Shadow-session boundary

The shadow page reads BTCUSDT candles through the existing read-only CLI market-data route and evaluates the selected strategy with the existing client backtest engine. While the page remains open, it refreshes every 30 seconds.

It does **not**:

- submit Binance orders;
- call server-side PAPER mutation or auto-trade endpoints;
- persist a process after the browser leaves the page;
- change visitor, trader, or LIVE permissions.

The interface labels this behavior `SESSION PAPER` so a browser session is not mistaken for a durable 24/7 execution service.

## Changed deployment files

- `src/components/trade/studio/types.ts`
- `src/components/trade/studio/navigation.ts`
- `src/components/trade/studio/StudioApp.tsx`
- `src/components/trade/studio/pages/ShadowPage.tsx`
- `src/components/trade/studio/pages/ShadowPage.module.css`
- `src/components/trade/studio/pages/ControlPage.module.css`
- `src/app/studio.css`

The deployed legacy application is not a Git checkout. This repository records the deployment boundary and verification evidence only.

## Verification

- `npx tsc --noEmit` — passed.
- Focused ESLint over the changed TypeScript/TSX files — passed with one pre-existing `StudioApp.tsx` unused-parameter warning and no errors.
- `npm run build` — passed; all 14 routes generated.
- `nemt-web.service` — active after restart.
- `nemt-api.service` — active.
- Public `GET https://postyouth.club/trade` — HTTP 200.

## Recovery

- Source backup: `/opt/nemt-app/backups/three-tabs-before-20261103-191500`
- Previous build: `/opt/nemt-app/backups/.next-before-three-tabs-20261103-191500`

## Follow-up boundary

A durable 24/7 shadow executor is separate work. It requires server-side lifecycle management, reconciliation, idempotency, stale-data blocking, audit logs, risk limits, and explicit acceptance tests before it can be considered for unattended operation. LIVE order placement remains disabled.

## Top mode-navigation refinement

The same-day visual follow-up moved the three primary modes from the narrow left rail into the top bar:

- desktop mode icons increased from 18 px to 26 px;
- each icon now has a short Chinese label and a larger hit target;
- the selected mode uses the violet surface and underline;
- the left mode rail and duplicate mobile bottom navigation were removed;
- mobile renders the same three modes as equal-width top buttons.

Verification was repeated after the refinement: TypeScript passed, the focused ESLint run had no errors and retained the same pre-existing unused-parameter warning, the production build passed, both services were active, and the public route returned HTTP 200.

Recovery for this refinement:

- Source backup: `/opt/nemt-app/backups/top-mode-nav-before-20261103-201500`
- Previous build: `/opt/nemt-app/backups/.next-before-top-mode-nav-20261103-201500`
