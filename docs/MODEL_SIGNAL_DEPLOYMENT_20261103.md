# Verified model-signal deployment — 2026-11-03

## Status

Deployed and verified on the Hong Kong product host from pinned commit
`68aebed` on branch `codex/trade-decision-456`.

This deployment adds fail-closed Chronos/Qlib inference-artifact validation and
a price-only NEMT market-phase observer. It does not add an order path, change
risk limits, or authorize PAPER, TESTNET, or LIVE execution.

## Artifacts and runtime

- API image: `ghcr.io/huliye24/emergenzix-api:68aebed`
- API release worktree: `/opt/emergenzix-releases/68aebed`
- Web image retained: `ghcr.io/huliye24/emergenzix-web:d11e7ba`
- Watch image retained: `ghcr.io/huliye24/emergenzix-api:paper-v2-9d2122f`
- Model artifact mount: `/data/model-signals.json`
- Chronos record SHA-256:
  `051f708028cf74c721f4f8ac3e8cdacdb6a015c3c48921c19836650b307b52fb`
- Chronos model-file SHA-256:
  `06a6a19bbe74bc10a9cd193bd4bf2bf638ae07f7e0d51653ae7ab8ea968a21dd`

The inference JSON contains public BTC/USDT market metadata and model hashes,
not exchange credentials, visitor codes, private balances, or order payloads.

## Observed verification

- Focused local tests: 12 passed.
- Ruff check: passed.
- Python bytecode compilation: passed.
- Pinned API image build: passed.
- Disposable container `/ready`: HTTP 200.
- Deployed API container: healthy.
- Deployed `/ready`: mode `PAPER`, `live_trading_enabled=false`.
- Deployed `/api/v1/overview`: HTTP 200 with the existing paper account state.
- Deployed internal market-intelligence response:
  - symbol: `BTC/USDT`
  - NEMT observer: `D_TREND`
  - Chronos: `verified`
  - Qlib: `artifact_unavailable`
  - execution decision: `NO_ACTION`
  - PAPER order permission: `false`
  - LIVE order permission: `false`

The Chronos score is fixed at 0.5 because the point forecast is not a calibrated
probability. Its direction is model evidence, not permission or an automatic
strategy selection.

## Known limitations

- The deployed Chronos JSON is a one-shot smoke artifact. The timestamp gate
  will reject it after two bars; continuous publication needs a separately
  reviewed GPU schedule and delivery channel.
- Qlib current inference remains unavailable because its locked training
  features include taker-buy volume and training-only normalization statistics,
  which are not present in the current basic-OHLCV live view.
- The THUML Timer checkpoint was pulled and smoke-tested only in an isolated GPU
  environment. It is not an allowlisted product signal and is not installed in
  the API image.
- The NEMT adapter covers price-only A/D phases. B/Vortex and C/Resonance need
  independently verified OI, funding, on-chain, or event inputs.

## Rollback

Recreate only the API service with the previous pinned image
`ghcr.io/huliye24/emergenzix-api:d11e7ba`, keep the existing data volume and
secret mounts, then re-run `/ready`, `/api/v1/overview`, and market-intelligence
safety checks. The Web and watch containers were not changed by this deployment.
