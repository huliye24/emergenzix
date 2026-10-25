# Weekly Research Batch: Dry-Run Planning v1

This micro-task only creates reproducible manifests for bounded weekly historical BTC/USDT research. It does not schedule jobs, fetch Binance OHLCV, run a backtest, write an SQLite record, create orders, or guarantee the dataset is available.

## Private API

POST /api/v1/research/date-range-batches/plan

Example JSON request:

    {
      "start_dates": ["2025-10-21", "2025-10-28", "2025-11-04"],
      "duration_days": 7,
      "training_days": 90,
      "initial_cash": 10000,
      "fee_bps": 10,
      "slippage_bps": 5
    }

The default duration is exactly seven days, with up to twelve nonoverlapping historical test windows. Each interval must have completed by the current UTC instant. Dates must be unique and in canonical YYYY-MM-DD form. Training days can range 60–365; costs and starting cash follow the existing individual date-range backtest API constraints. Any invalid request is rejected with HTTP 422. A valid plan returns HTTP 200.

Response: stable SHA-256 batch and per-job identifiers derived from sorted dates, versioned protocol and common financial assumptions; chronologically ordered future request payloads; UTC training/test boundaries; expected 4-hour candle counts and pending dataset checks. Dates passed in any order produce exactly the same manifest. Changing fees, training period or other settings changes the fingerprint.

All tasks are explicitly marked planned_only with null saved record IDs and unverified data coverage. The plan itself is not persisted. Existing single-run backtesting remains a separate, explicit operator action. The planner is not a queue, worker or new trading capability.

### Required follow-on micro-tasks

1. Durable batch run state with idempotency keys, bounded single-worker claims, retry/recovery and cancellation semantics.
2. One-at-a-time worker invoking the existing historical research function, storing record IDs or sanitized failures.
3. Immutable batch summary with per-week record references and comparisons; never compound unrelated return percentages as an investable portfolio curve.

Disjoint holdout weeks can still have overlapping training data or correlated conditions. No evidence here proves profitability, statistical independence, or ability to place PAPER/TESTNET/LIVE orders.

The product API remains unauthenticated; restrict access to the internal Compose network.

Codex acceptance: from backend run python -m pytest -q tests/test_research_batch_plan.py and then python -m pytest -q.

## Durable internal batch-task state (next micro-task)

A separate internal SQLite ledger (no remote mutation routes) can register a validated
plan from the dry-run endpoint using the Python service API:
- \`register_research_batch(db, plan)\`: verify the deterministic v1 manifest,
  persist one immutable plan and its pending job rows atomically. Duplicate
  registration returns the existing state without resetting attempts.
- \`get_research_batch(db, batch_id)\`: inspect states and sanitized error codes,
  never exposing active lease tokens.
- \`claim_next_research_job(db, batch_id, worker_id=...)\`: atomically lease only
  one pending job within a batch. Claims carry a random, private lease token
  and expire after 300 seconds by default.
- \`renew_research_job_lease(db, job_id, token)\`: heartbeat for long-running
  archive downloads; cannot extend an expired or superseded lease.
- \`fail_research_job(db, job_id, token, error_code=...)\`: release the attempt
  to the queue or mark it failed after a maximum of **three** attempts.
  Error codes are allowlisted, not arbitrary exception text.
- \`cancel_unclaimed_research_jobs(db, batch_id)\`: cancel only unclaimed jobs;
  a running lease is **not** preempted.

SQLite \`BEGIN IMMEDIATE\` serializes claims and enforces at most one active
lease per batch. On the next claim, expired leases are reclaimed and their
attempts are retained; an old token cannot acknowledge a new attempt.
The plan is immutable and stored in the existing Emergenzix database.
No executor or completion path exists in this micro-task; a follow-on worker
will verify immutable result IDs before marking a job successful.
All functions are for trusted local server code and are **not exposed
through an unauthenticated HTTP or Telegram command**.

On restart, reopen the same SQLite path and read/claim as before. The
ledger is not a distributed job broker and does not guarantee exactly-once
external work; future execution must preserve idempotency on saved backtests.

Acceptance for Codex:
\`\`\`bash
cd backend
python -m pytest -q tests/test_research_batch_state.py
python -m pytest -q
\`\`\`
