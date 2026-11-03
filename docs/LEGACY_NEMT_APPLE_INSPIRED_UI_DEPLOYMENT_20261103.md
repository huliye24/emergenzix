# Legacy NEMT Apple-inspired three-screen UI deployment

Date: 2026-11-03

Target: `https://postyouth.club/trade`

Status: deployed and build-verified

## Objective

Unify the live-monitor, backtest, and shadow-session screens around a restrained, Apple-inspired product language without changing trading behavior or authorization.

The design uses principles rather than copying Apple product assets: system-first typography, clear spatial hierarchy, generous whitespace, continuous rounded surfaces, low-contrast separators, restrained shadows, translucent material, and a small violet accent system.

## Screen changes

### Live monitor

- Replaced the report-like ruled grid with independent account cards.
- Elevated total equity with a soft violet material surface.
- Converted account health into a compact translucent status rail.
- Converted assets and orders into rounded content panels with clearer row hierarchy.

### Backtest

- Rebuilt the parameter strip as one continuous rounded control surface.
- Changed strategy selection from a narrow engineering rail into larger rounded targets.
- Restyled parameters, assumptions, results, metrics, and empty state as one coherent workbench.
- Preserved all existing strategy, direction, capital, interval, date, and data-lake behavior.

### Shadow session

- Aligned its parameter strip, strategy controls, result canvas, and metrics with the backtest screen.
- Preserved the browser-session PAPER lifecycle, 30-second refresh, and no-exchange-order boundary.

### Shared shell

- Uses the native system font stack first, with Chinese platform fallbacks.
- Changed the top mode switcher into a translucent segmented control with a raised active surface.
- Retained cyan for market-data cues and violet for interaction state.

## Changed deployment files

- `web/src/app/studio.css`
- `web/src/components/trade/studio/pages/OverviewPage.module.css`
- `web/src/components/trade/studio/pages/ControlPage.module.css`
- `web/src/components/trade/studio/pages/ShadowPage.module.css`

No authentication, exchange adapter, risk, execution, strategy, or data-source code changed.

The deployed legacy application is not a Git checkout. This repository records the deployment boundary and verification evidence only.

## Verification

- `npx tsc --noEmit` — passed.
- `npm run build` — passed; all 14 routes generated.
- `nemt-web.service` — active after restart.
- `nemt-api.service` — active.
- Public trade route — HTTP 200.

## Recovery

- Source backup: `/opt/nemt-app/backups/apple-ui-before-20261103-221500`
- Previous build: `/opt/nemt-app/backups/.next-before-apple-ui-20261103-221500`
