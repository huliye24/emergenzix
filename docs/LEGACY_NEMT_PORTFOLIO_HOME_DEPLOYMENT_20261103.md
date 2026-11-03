# Legacy NEMT portfolio home deployment — 2026-11-03

## Status

Implemented and deployed to the legacy public NEMT web runtime on the Ningxia/HK-facing host alias `dev`.

This is a UI-only micro-task. It does not change the Emergenzix product API, exchange permissions, order behavior, execution mode, or LIVE eligibility.

## Objective

Replace the illustration-led default portfolio page with a decision-oriented account home that prioritizes existing operational data.

## Acceptance result

- The default portfolio page leads with account equity, available USDT, BTC balance, and BTC/USDT market context.
- Account connectivity, market freshness, PAPER execution state, and open-order count are visible before any strategy or execution navigation.
- Asset details and recent simulated orders use the existing read-only API responses.
- Market, strategy, and PAPER execution remain explicit secondary destinations.
- Visitor access is identified as read-only and LIVE remains visibly disabled.
- No profit, position, risk, or valuation figure is invented when the existing API does not supply it.

## Changed runtime files

- `/opt/nemt-app/web/src/components/trade/studio/pages/OverviewPage.tsx`
- `/opt/nemt-app/web/src/components/trade/studio/pages/OverviewPage.module.css`

The legacy runtime is not a Git checkout. These runtime changes therefore have no source commit in that legacy tree. The supported Emergenzix repository remains the product source of truth; this document records the temporary legacy deployment evidence.

## Verification performed

- `npx tsc --noEmit` — passed.
- `npx eslint src/components/trade/studio/pages/OverviewPage.tsx` — passed with no reported warnings or errors.
- `npm run build` — passed under Next.js 16.3.2; all 14 static/dynamic routes completed.
- `nemt-web.service` — active after restart.
- `nemt-api.service` — active.
- Local `http://127.0.0.1:3101/trade` — HTTP 200.
- Public `https://postyouth.club/trade` — HTTP 200.
- Server-side visitor-session smoke test — `/auth/visitor`, `/auth/me`, `/system/status`, `/account/balances`, `/market/ticker`, and `/orders` all returned HTTP 200. The visitor code and response bodies were not printed.

Browser automation reached the visitor gate but did not submit the secret because doing so would expose it in automation logs. Authenticated visual review is therefore still a manual follow-up; production build/type checks and authenticated API smoke checks passed.

## Rollback evidence

- Source backup: `/opt/nemt-app/backups/portfolio-home-before-20261103-121054.tar.gz`
- Source backup SHA-256: `7a4b61a72c9fd9689ba14ebbafb29a4497b4ca06ead599f467cba022b81087eb`
- Previous build: `/opt/nemt-app/backups/.next-before-portfolio-home-20261103-121054`

## Safety boundary

- Visitor sessions remain viewer-only.
- The page calls existing GET/read paths plus the existing refresh workflow.
- PAPER/DEMO remains the operating boundary.
- LIVE trading remains disabled and no exchange order method was added or enabled.
