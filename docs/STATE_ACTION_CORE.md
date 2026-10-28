# Emergenzix STATE / ACTION core — v0.2 proposal

**Two user-facing concepts, four internal research layers.**

> STATE: What market conditions are observable now?
> ACTION: What response, if any, deserves review under current constraints?

The public conceptual interface is intentionally small. Internal work remains complex, transparent and verifiable.

## State

An immutable, time-stamped summary of 4M evidence:
- M1 macro environment;
- M2 market regime;
- independent completed-candle trend;
- assessment score and evidence/uncertainty notes.

State is **not** an objective truth; it is the output of a specific model and data vintage. Absent or conflicting evidence remains unknown.

## Action

An immutable advisory recommendation:
- `REVIEW_LONG`: candidate long setup requiring separate validation and risk checks;
- `REVIEW_SHORT`: candidate short setup requiring separate validation and risk checks;
- `WAIT`: no aligned, sufficiently complete evidence;
- `HALT`: stale evidence or risk veto.

**ACTION is never an exchange order or authorization to execute.** The v0.2 output is always `PAPER_RESEARCH` and `live_trading_enabled=False`.

## Internal ownership

- NEMT: research provenance, mathematical hypotheses, parameter preregistration and falsification.
- 4M M1–M3: evolving market observations, regime interpretation and scenario mapping.
- 4M M4: independent risk veto and strategy lifecycle controls.
- Emergenzix runtime: paper simulation and operational monitoring, currently **not connected to this interface**.
- AI: may propose feature definitions, alternative estimators and experiments, but changes must be versioned, reviewed and tested offline before promotion.

## Loop

`new timestamped evidence -> STATE -> advisory ACTION -> simulated outcomes -> evaluation -> future STATE`

Production decisions cannot blindly retrain or promote themselves from paper outcomes. All changes require independently recorded model version, data vintage and holdout evidence.

## API

```python
from emergenzix_api.four_m import Snapshot
from emergenzix_api.four_m.state_action import evaluate

# snapshot must contain precomputed point-in-time features
result = evaluate(snapshot)
print(result.state)
print(result.action)
```

There is deliberately no HTTP endpoint or order side effect. See [FOUR_M_SYSTEM.md](FOUR_M_SYSTEM.md) for feature contract and validation gates.

## Codex AWS handoff (PAPER only)

1. Inspect the PR, review CI and execute `cd backend && pytest -q tests/test_four_m.py tests/test_state_action.py`; run full backend and frontend checks.
2. Verify the current EC2 instance, active Compose project, version, health, persistent volume and rollback snapshot **read-only**. Redact all secrets.
3. Verify PAPER mode and no real order capabilities. Confirm network ingress remains private; never publish the unauthenticated web/API ports.
4. This code-only change requires no service restart until a reviewed release is explicitly approved. A successful merge alone must not trigger production replacement.
5. If authorized separately to deploy: build/tag immutable reviewed images, back up persistent volumes, deploy via the existing Compose process, check `/health` and `/api/v1/overview`, exercise Telegram status and confirm no live order path. Roll back on unexpected output.
6. No Binance trade credentials, margin/futures order capabilities or real-money orders in this milestone.

**Do not** delete volumes (`docker compose down -v`) or disclose `.env` values.
