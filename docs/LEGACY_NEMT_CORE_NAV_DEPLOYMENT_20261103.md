# Legacy NEMT core-navigation deployment — 2026-11-03

## Status

- Public page: `https://postyouth.club/trade`
- Deployment state: **active and verified**
- Runtime: Hong Kong control host, `/opt/nemt-app`
- Scope: navigation and route exposure only

The public workspace is now organized as a quantitative trading tool rather
than a research notebook. The Research and Experiments destinations are absent
from desktop navigation, mobile navigation, section types, route resolution,
page imports, and page rendering.

The underlying research/experiment components and browser-local data were not
deleted. They are simply unreachable from the public workspace, preserving a
future option to move them into a separately protected internal application.

## Public information architecture

Primary workflow:

1. Portfolio / account overview
2. Markets
3. Strategies and backtesting
4. PAPER execution

Secondary utilities:

1. Personal space
2. Settings

AI analysis should be added contextually inside markets, strategy evaluation,
and execution review instead of becoming another research-oriented top-level
destination.

## Changed files

- `/opt/nemt-app/web/src/components/trade/studio/navigation.ts`
- `/opt/nemt-app/web/src/components/trade/studio/types.ts`
- `/opt/nemt-app/web/src/components/trade/studio/StudioApp.tsx`

## Verification

- Next.js 16 production build and TypeScript checks passed.
- Focused ESLint on the three changed files reported zero errors and one
  pre-existing unused-parameter warning in `StudioApp.tsx`.
- `nemt-web.service` and `nemt-api.service` remained active.
- Public `/trade` returned HTTP 200 after deployment.
- Navigation source contains no Research or Experiments items.
- Section types and conditional page rendering contain neither destination, so
  stale `section=research` or `section=experiments` values resolve to the
  default Portfolio section.

## Rollback

- Source backup:
  `/opt/nemt-app/backups/core-nav-before-20261103-120628.tar.gz`
- Source backup SHA-256:
  `3b766beeb0688fb384dea310051f658c19e58006ddfc241662ed3cbe9c10cc79`
- Previous Next.js build:
  `/opt/nemt-app/backups/.next-before-core-nav-20261103-120628`

Restore the three source files and the matching `.next` directory together,
then restart `nemt-web.service`.
