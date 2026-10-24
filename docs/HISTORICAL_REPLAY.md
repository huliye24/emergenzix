# BTC Historical Replay Records

## Purpose

This workflow creates reproducible, point-in-time BTC research records from an already registered, content-addressed OHLCV dataset. It does not place orders, change the dataset, or enable live trading.

The first version deliberately accepts BTC quote pairs only. ETH, SOL, portfolio logic, spot/perpetual hedging, leverage, and live execution are outside this task.

## Select a replay mode

The replay API supports exactly one of two anchor modes:

1. **Random mode** — provide a non-negative \`seed\`. The same dataset, protocol version, seed, lookback, and horizon choose the same anchor.
2. **Timestamp mode** — provide a timezone-aware \`anchor_at\` and omit \`seed\`. The timestamp must exactly match a candle's opening timestamp after conversion to UTC. This is intended for replaying a moment selected from TradingView.

For a chart whose displayed timezone is UTC+08:00, send the offset explicitly. For example, a chart label of \`2024-03-17 04:00\` becomes \`2024-03-17T04:00:00+08:00\`, not a timezone-naive timestamp. The API converts it to UTC before matching the dataset.

The selected candle is the final candle visible to the scenario analyzer. The next \`horizon_bars\` candles are held out for outcome evaluation. The anchor must leave enough preceding bars for the lookback and enough subsequent bars for the requested outcome horizon. If the exact candle is absent or insufficient history remains on either side, the request is rejected rather than silently choosing a nearby candle.

## API

Register the candles first through the existing dataset endpoint. The dataset should have a BTC symbol such as BTC/USDT, a declared timeframe, timezone-aware ordered candles, and enough history for the selected lookback and forward horizon.

Example random replay request:

~~~json
{
  "dataset_id": "64-character-dataset-sha256",
  "seed": 42,
  "horizon_bars": 12,
  "lookback": 20
}
~~~

Example timestamp-anchored replay request:

~~~json
{
  "dataset_id": "64-character-dataset-sha256",
  "anchor_at": "2024-03-17T04:00:00+08:00",
  "horizon_bars": 12,
  "lookback": 20
}
~~~

POST to **/api/v1/research/historical-replays**. The default horizon is 12 bars and the default lookback is 20 bars.

- **POST /api/v1/research/historical-replays** runs and stores a replay. The first insert returns HTTP 201; repeating the same dataset/configuration returns the existing record with HTTP 200.
- **GET /api/v1/research/historical-replays** lists recent records. Optional dataset_id and limit query parameters filter the list.
- **GET /api/v1/research/historical-replays/{record_id}** retrieves one record and verifies its stored fingerprint.

## Record provenance and integrity

Each record includes:

- The registered dataset ID and SHA-256 fingerprint, source, exchange, symbol, timeframe, date range, and bar count.
- The replay protocol version, random seed or explicit anchor timestamp, lookback, and outcome horizon.
- The chosen anchor index, the timestamp visible to the scenario analyzer, the hidden outcome interval, the scenario snapshot, and the subsequently measured outcome.
- Explicit safety fields showing that no order was created and live trading was not enabled.
- A deterministic record ID derived from the dataset fingerprint, protocol version, replay mode, seed or timestamp, lookback, and horizon.
- A record SHA-256 fingerprint over the canonical result payload. Creation time is metadata and is excluded from that reproducibility fingerprint.

Before replay, the API recalculates the registered dataset fingerprint from its canonical metadata and candle payload. If the payload no longer matches the stored fingerprint, the request is rejected. SQLite triggers reject updates and deletes of replay records. Re-running the same configuration is idempotent; if the same protocol identity unexpectedly produces a different result, the API returns a conflict instead of silently replacing the prior record.

When the replay algorithm or the meaning of its result changes, increment REPLAY_PROTOCOL_VERSION in the replay engine before creating records under the new semantics. Do not reinterpret older records in place.

## Interpretation limits

Scenario labels and probabilities are heuristic classifications, not calibrated forecasts. The outcome fields describe the selected future window; they do not establish profitability, account for trading costs, or simulate a hedge. A historical replay record is research evidence only and must not directly authorize an order.

Tests are intentionally left for the repository's Codex/CI verification workflow.
