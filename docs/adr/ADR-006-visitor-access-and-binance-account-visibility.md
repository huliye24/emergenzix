# ADR-006: Visitor access and read-only Binance account visibility

- **Status:** Accepted for the PAPER research deployment
- **Date:** 2026-11-03

## Context

The public-facing research workspace needs a small access gate and a trusted
server-side view of the owner's Binance Spot account balance. The product does
not have an approved TESTNET or LIVE execution adapter, and a visitor must not
gain mutation or order permissions merely by entering a shared code.

## Decision

The Next.js web tier issues a 12-hour, HMAC-signed, HttpOnly visitor cookie
after validating one high-entropy visitor code. Visitor sessions are a
`viewer` role. The proxy rejects every non-GET/HEAD/OPTIONS request under
`/api/v1/` before it can reach the backend. This gate is for a private research
deployment; it is not a replacement for future per-user operator
authentication or backend authorization.

Binance credentials remain server-side and are mounted from read-only secret
files. The backend adapter implements only signed GET requests. On each
summary request it first verifies that account reading is enabled and
withdrawals are disabled, then returns only the USDT balance, non-zero asset
count, and bounded permission metadata. It exposes no order, transfer,
withdrawal, or credential-management method. If configuration, permissions,
network state, or response data are uncertain, the endpoint fails closed.

The credential's exchange-side trading flags are reported as warnings but do
not grant product permission. Emergenzix continues to expose PAPER execution
only; LIVE remains disabled and no Binance order endpoint exists.

## Consequences

- A leaked visitor code permits read-only access to the displayed research and
  bounded account summary until the code/session secret is rotated.
- Rate limiting is process-local and suitable only for the current single web
  process. A distributed deployment needs a shared limiter or upstream control.
- The backend stays private to the Compose network. Future state-changing
  operator workflows require explicit server-side identity and authorization;
  the visitor proxy rule must not be treated as their sole control.
- A dedicated, IP-restricted Binance key with reading enabled and withdrawals
  disabled remains preferred even though the adapter cannot submit orders.
