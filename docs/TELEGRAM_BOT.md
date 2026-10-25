# Telegram Command Bot

## Scope and safety

The Telegram command bot is an opt-in process for private operational queries and historical PAPER research. It does not provide exchange order, account credential, or LIVE trading commands.

The bot accepts commands only in a private Telegram chat whose chat ID exactly matches **EMERGENZIX_TELEGRAM_ALLOWED_CHAT_ID**. Group and channel chats are always ignored, even if a group ID is accidentally configured. Unauthorized chats are ignored without a reply. The bot token is read from the server environment and must never be committed to Git.

The current API has no user authentication. Keep the API and web service on the private Compose network or localhost; do not publish the API port. The bot talks to the API using the internal Compose hostname.

## Configuration

Set these values in the local .env file:

- **EMERGENZIX_TELEGRAM_BOT_TOKEN**: token created for your Telegram bot.
- **EMERGENZIX_TELEGRAM_CHAT_ID**: destination for outbound watch notifications.
- **EMERGENZIX_TELEGRAM_ALLOWED_CHAT_ID**: exact numeric chat ID allowed to issue commands. Set this explicitly even if it is the same chat used for notifications.
- **EMERGENZIX_TELEGRAM_API_BASE_URL**: defaults to http://api:8000 in Compose.
- **EMERGENZIX_TELEGRAM_BACKTEST_COOLDOWN_SECONDS**: minimum delay between accepted backtest requests; default 60 seconds.

Do not put real values in .env.example or commit .env.

## Start and stop

The command bot is behind the telegram Compose profile and is not started by the default Compose command. After configuring the token and allowed chat ID, run:

~~~bash
docker compose --profile telegram up -d --build
docker compose --profile telegram logs -f telegram-bot
~~~

Stop the services with:

~~~bash
docker compose --profile telegram down
~~~

The bot persists its update offset, initialization flag, and backtest cooldown timestamp in the existing emergenzix-data volume. On first startup it intentionally discards any commands already queued before the bot was initialized, preventing stale messages from unexpectedly starting work. Later updates are offset-tracked across restarts. The date-range API is idempotent for identical research inputs, but a Telegram response can still be lost if the process stops during a request.

## Commands

- **/help** — list available commands.
- **/status** — report API health/readiness, mode, live-trading state, and notification queue counts.
- **/latest** — summarize the most recent saved date-range backtest.
- **/history** — list up to five most recent saved date-range backtests with UTC start date, chosen candidate, net return, and **full 64-character record IDs**.
- **/report RECORD_ID** — retrieve one immutable saved date-range backtest report using an exact lowercase 64-character ID copied from /history; invalid IDs are rejected before contacting the API.
- **/compare ID1 ID2** — compare two different immutable saved backtest records using the read-only comparison API (requires the separate comparison API PR to be deployed). Displays returns, benchmark, drawdown, trades, win rate, and per-side costs, with warnings for different test periods, costs, or datasets. Does not choose a winner.
- **/evidence RECORD_ID** — display the seven heuristic evidence checks for a saved v4 research report and underlying trade counts, walk-forward fold coverage, positive cost-stress scenarios, and worst stressed return. Requires PR #37's read-only evidence endpoint deployed. Never approves strategy promotion or grants PAPER/TESTNET/LIVE order permission.
- **/stats [limit]** — descriptive read-only aggregate for the newest saved backtest records (default 30, allowed 2–100). Displays positive/negative/flat **record** counts, return distribution, per-record drawdown distribution and explicit overlap/cost/candidate warnings. Requires the private statistics API from PR #39; never compounds returns or treats duplicate/overlapping intervals as separate weeks.
- **/backtest YYYY-MM-DD [days]** — run a historical BTC/USDT spot 4-hour backtest. Duration defaults to 7 days and must be 1–30 days. The full requested interval must end in the past.

The backtest command uses the existing date-range research API with a fixed 90-day training window, 10 basis points of fee per side, 5 basis points of slippage per side, and 10,000 USDT initial simulated cash. It reports the selected candidate, net PnL, return, maximum drawdown, completed trades, win rate, and validation-evidence status. When the API returns complete data, the report also includes the pre-test candidate validation table, a UTC daily PnL/return/equity table derived from the holdout 4-hour equity curve, and the fixed 3×3 fee/slippage sensitivity matrix for the selected strategy. Candidate metrics are explicitly labeled as pre-test validation, and cost scenarios do not reselect the strategy or change the test window. The daily table is omitted when the curve is incomplete or malformed, and the cost matrix is omitted when its data or protocol metadata is incomplete, rather than estimating missing values. These settings are intentionally fixed in this first command interface.

The /history, /report, and /compare commands call only read-only date-range research API endpoints. /compare requires the separate date-range comparison API integration to be deployed first. Its response represents independent historical observations, not controlled head-to-head strategy evaluation.

The /history and /report commands call only the existing read-only date-range research API endpoints. The API verifies saved record integrity before returning results. These commands never rerun a strategy, download market data, consume the backtest cooldown, or place any order. A malformed history response is rejected rather than displaying partial results.

## Cross-period statistics

The `/stats` command (optional `/stats 30`, `/stats 100`, etc.) performs exactly one GET of `/api/v1/research/date-range-statistics?limit=N`. There is no market data fetch, no new backtest, and no order action. The bot validates the API protocol, record identities and counts, underlying return/drawdown distributions, overlap flags, and all explicit no-order permissions. Malformed or contradictory data results in an error message rather than misleading summary figures.

**Important:** The sample count is the number of **saved experiments**, not necessarily calendar weeks, independent trials, or returns from a continuous fixed-strategy portfolio. If two records cover the same/overlapping period, they both count as records but cannot be called distinct profitable weeks. Even when periods are disjoint, training histories may overlap. No cumulative return, annualized return or portfolio max drawdown is inferred. This command requires deployment of the independent read-only statistics API from PR #39 on the private network.

## Evidence screening

The `/evidence RECORD_ID` command uses only `GET /api/v1/research/date-range-backtests/{record_id}/evidence`. It checks a strict 64-character lowercase record ID before the API request and verifies a complete `date-range-evidence-v1` response before displaying metrics. Missing, inconsistent, unsafe, or non-finite evidence is rejected rather than partially rendered. The command does not consume the backtest cooldown, download candles, execute a strategy, update a saved record, or create orders.

Results distinguish `limited_evidence` from `checks_met`. **Neither status is a trading approval or statistical-significance finding**: the underlying v4 minimum of two completed trades is only a screening heuristic, and historical returns cannot establish future performance. The API must remain on the private Compose network and requires the evidence endpoint from PR #37 to be deployed.

## Long report delivery

Telegram messages have a finite text-length limit. Long research reports are now broken into ordered, lossless parts using a conservative UTF-16-unit limit; each part includes its position (e.g. 1/3) and an explicit PAPER/no-order safety note. Short responses retain their previous single-message format. When a response would exceed twelve bounded parts, the bot sends one explanatory error message rather than an incomplete, misleading report.

Telegram delivery is not transactional: if a transport error occurs in the middle of a multipart response, the earlier parts may already be visible. The failed update is not automatically replayed; reissue `/report RECORD_ID` if a part is missing. This change does not modify the backtest engine or add any order capability.

## Operational limitations

- The bot does not create paper orders or live orders; it only queries status and runs historical research.
- The Telegram chat allowlist is a command authorization boundary, not a substitute for API authentication if the service is later exposed to other networks.
- A long historical-data download can take time. After validating the date range and cooldown, the bot sends an acceptance message before calling the research API, then sends the final report when the run completes. The polling loop remains occupied while the backtest runs, so other commands are processed afterward; the cooldown is persisted across restarts.
- Telegram and API errors are sanitized; diagnostic logs record error classes rather than tokens or raw response bodies.
- No real Telegram call, cloud deployment, or profitability claim is part of this code change. Run the documented tests and operational acceptance steps before enabling the Compose profile.

## Optional private weekly batch research commands

Weekly batch commands are **disabled by default**. To enable them, keep
the API private and the Telegram Bot on the same Compose SQLite volume.
Add the following to the local (untracked) .env file:

    EMERGENZIX_TELEGRAM_BATCH_ENABLED=true

The Telegram service explicitly uses EMERGENZIX_DB_PATH=/data/emergenzix.db,
matching the API container's shared SQLite volume. Enabling batch commands
without an explicit shared DB path fails closed. The existing exact private
chat allowlist applies to every /batch subcommand; groups are still ignored.

Commands:

    /batch plan 2025-10-21 2025-10-28
    /batch register 2025-10-21 2025-10-28
    /batch status BATCH_SHA256
    /batch report BATCH_SHA256

- plan is pure, never accesses SQLite or fetches candles.
- register saves a canonical plan and pending jobs; it requires an already
  created database and will not silently create a second empty SQLite file.
  Registering the same plan twice cannot reset attempts or an active lease.
- status and report are verified, strictly read-only SQLite snapshots. They
  do not reveal lease tokens and exclude incomplete/failed weeks from returns.
- No execution, retry, cancellation, strategy promotion, PAPER order or LIVE
  trading subcommand is exposed. Registering tasks does **not** start a worker.
  The private one-job historical worker remains a separate operator action.

All batch research uses BTC/USDT spot 4h with 90 days of training and 7-day
nonoverlapping historical holdouts, up to 12 weeks. Costs are fixed at
10 bps fees + 5 bps slippage per side and 10,000 USDT simulation capital.
Independent weekly returns must NOT be compounded into a live portfolio ROI.
The bot does not grant any exchange-order authorization.

The backend API remains unauthenticated and MUST stay behind private access.
Enabling Telegram commands is not a substitute for API auth.
Existing /backtest and /status commands are unchanged. Use the existing
Docker Compose opt-in Telegram profile and restart only that bot container
after changing its env flag.

Acceptance:

    cd backend
    python -m pytest -q tests/test_telegram_batch.py
    python -m pytest -q


## Explicit cloud Worker invocation after Telegram registration

Telegram's `/batch register` command saves the plan but **does not run**
historical calculations. An authorized operator with access to the private
Docker Compose host can start exactly one saved job using:

```bash
bash ops/research_batch_once.sh YOUR_64_CHARACTER_BATCH_SHA256
```

This runs one transient process using the existing API image and the same
`emergenzix-data:/data` SQLite storage, without publishing a new port or
introducing an always-on scheduler. To run further weeks, invoke the command
again manually. Use Telegram `/batch status BATCH_SHA256` and
`/batch report BATCH_SHA256` to view only verified saved results.
Do not expose the Docker host or unauthenticated API to the public internet.

