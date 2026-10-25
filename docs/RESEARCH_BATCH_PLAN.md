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

## Explicit one-job historical research worker

After registering an approved batch plan through the trusted local Python
state API, an operator may execute at most **one** approved saved job:

    cd backend
    python -m emergenzix_api.research_batch_worker \
      --batch-id BATCH_SHA256 \
      --db-path ../data/emergenzix.db \
      --execute-one

Without the explicit --execute-one flag this CLI refuses execution. A single
invocation does not loop through the batch. The internal worker is NOT a public
HTTP endpoint or always-on scheduler.

A background heartbeat periodically renews the job's SQLite lease while
verified public historical archive data is fetched and the existing
create_date_range_backtest method executes. On success the worker validates
the saved record fingerprint, selected historical window, costs, training
duration, dataset ID and archive provenance, and PAPER/no-order safety
metadata before atomically marking the job completed. The saved record's
immutable ID and SHA-256 then appear in the batch state. A lost lease cannot
acknowledge an old result.

If an interrupted attempt has already persisted the identical immutable
record, the next claim can reuse that result; job attempts remain bounded
at three. Raw exception text (possibly containing credentials or internal
URLs) is not returned by the worker. Failed records cannot be marked complete.

Prior CHECK-constrained batch ledgers are migrated to a completed-capable
schema while preserving existing rows and retry counters. The historical
backtest results remain immutable.

This is single-host SQLite coordination, not a distributed exactly-once
scheduler; operators must control cloud resource budget, job rate and
concurrency. It never places PAPER, TESTNET or LIVE orders.

Codex acceptance:
    cd backend
    python -m pytest -q tests/test_research_batch_worker.py
    python -m pytest -q

## Read-only verified Research Batch Report

A separate read-only snapshot report summarizes persisted batch progress and
verifies every completed job against the immutable historical record stored
in SQLite. No backtests are executed by the report command.

Usage from the backend directory:

    python -m emergenzix_api.research_batch_report_cli \
      --batch-id BATCH_SHA256 \
      --db-path ../data/emergenzix.db \
      --output ../data/reports/research-batch.json

Omit --output to print JSON to stdout. With --output, export both JSON and
Markdown at the specified .json base path. This is an internal local CLI;
there is no public, unauthenticated batch-report HTTP mutation endpoint.

The report includes: planned job count, pending/leased/completed/failed/
cancelled state counts, percentage completed, per-job attempts and
allowlisted failure codes, selected candidates, independent completed
record IDs and SHA-256 digests, single-run return and max drawdown
distributions, and a stable snapshot hash.

The snapshot reads within one SQLite transaction with query_only enabled.
For every completed row, it independently checks the immutable result
fingerprint, actual stored dataset provenance, original strategy protocol,
costs and test dates. A missing or corrupted completed record fails the
ENTIRE report rather than showing partial/misleading financial statistics.
Unfinished, failed and cancelled jobs are not treated as zero-return
simulated weeks. An expired lease is flagged without causing a background
retry or mutating the ledger.

A snapshot SHA-256 is deterministic for identical persisted job state
(except expired-at-snapshot flags); a subsequent completed job changes
the snapshot. JSON/Markdown files are exported only on explicit request.

IMPORTANT: Individual weekly returns are NOT multiplied into a portfolio
return, and per-record maximum drawdown is NOT a live portfolio drawdown.
Different candidate selections are explicitly flagged. A report never
promotes strategies, creates trading orders or grants PAPER/TESTNET/LIVE
permissions. The return distribution is descriptive and may reflect
selection bias or correlated training periods.

Acceptance tests:

    cd backend
    python -m pytest -q tests/test_research_batch_report.py
    python -m pytest -q

Read-only CLI hardening: the report command opens an existing SQLite file via
`mode=ro` and explicitly enables `PRAGMA query_only`. It does **not** call the
normal mutable `Database.initialize()` constructor. Missing paths cannot
create empty database files; generation of JSON/Markdown outputs remains an
explicit `--output` action. The acceptance suite runs three historical weeks
on synthetic offline candles, restarts the database process between jobs,
confirms 100% completion, and checks that no paper orders were inserted.

## Minimal batch operator CLI (plan / register / status)

The internal operator entrypoint closes the gap between the dry-run HTTP
planner and the existing one-job historical Worker. It deliberately exposes
no strategy execution mode or public HTTP/Telegram write interface.

From backend, preview a 3-week plan (no filesystem or database operations):

    python -m emergenzix_api.research_batch_cli plan \
      --start-date 2025-10-21 \
      --start-date 2025-10-28 \
      --start-date 2025-11-04

Explicitly create the immutable manifest and pending jobs in SQLite:

    python -m emergenzix_api.research_batch_cli register \
      --start-date 2025-10-21 \
      --start-date 2025-10-28 \
      --start-date 2025-11-04 \
      --training-days 90 --fee-bps 10 --slippage-bps 5 \
      --db-path ../data/emergenzix.db

Record the returned batch_id; the command is idempotent, so repeating it
never resets an in-flight lease or completed result. To inspect jobs:

    python -m emergenzix_api.research_batch_cli status \
      --batch-id BATCH_SHA256 --db-path ../data/emergenzix.db

The status command reuses the verified read-only report snapshot, opening
only an **existing** database and validating completed record fingerprints.
It never initializes SQLite, fetches candles, alters work state, or reveals
private lease tokens.

To **run** a historical task requires an entirely separate explicit
one-task call to research_batch_worker with --execute-one (see above).
For JSON and Markdown return distributions, use research_batch_report_cli.
The CLI defaults to BTC/USDT spot 4h, 7-day tests, 90-day training and
bounded 12-week plans. It accepts only disjoint completed historical weeks.
No PAPER/TESTNET/LIVE exchange orders are authorized.

CI acceptance:

    cd backend
    python -m pytest -q tests/test_research_batch_cli.py
    python -m pytest -q

