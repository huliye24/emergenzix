# BTCUSDT 15m S3 data-lake acceptance — 2021 to current

- Observed: 2026-10-30 UTC
- Source code: `423de73`
- Collector: `binance/binance-public-data@d571690eb66760763b0bd6e27e8cd35603c7e244`
- Collection path: Hong Kong collector -> local relay -> Ningxia CPU verifier -> Ningxia S3
- Bucket: `emergenzix-research-382309211832-cn-northwest-1-an`
- Execution mode: `RESEARCH_ONLY`

## Accepted snapshot

- Snapshot ID: `aa8bb7c4146801a117d45304b35dd2924cc50d73e274df108327bc805e8481e1`
- Manifest: `market-data/v1/manifests/snapshot=aa8bb7c4146801a117d45304b35dd2924cc50d73e274df108327bc805e8481e1/manifest.json`
- Base coverage: 2021-01-01 00:00 UTC through 2026-10-30 04:45 UTC
- Base rows: 204,238
- Historical archive packages: 60 monthly ZIPs and 60 checksum files
- Official archive checksum results: 60 passed, 0 failed
- Historical 2021–2025 rows: 175,226 unique rows

## S3 read-back verification

The verifier downloaded the current pointer, manifest, all 413 Parquet
objects, and all 60 historical source ZIPs from S3. It checked every recorded
SHA-256 and Parquet row count. The pointer snapshot, stored manifest snapshot,
and independently recomputed canonical manifest SHA-256 were identical.

| Timeframe | Rows |
| --- | ---: |
| 15m | 204,238 |
| 1h | 51,056 |
| 4h | 12,757 |
| 1d | 2,121 |
| 1w | 297 |
| 1mo | 63 |

- Historical source objects verified: 60
- Historical checksum objects present: 60
- Duplicates: 0
- Off-grid timestamps: 0
- Forming candles excluded: yes

## Explicit source gaps

The checksum-valid official Binance monthly archives contain seven source
gaps, totaling 70 missing 15-minute candles. The lake does not invent prices
or forward-fill them. Silver retains explicit missingness, Gold omits
incomplete aggregation buckets, and the manifest records each gap.

| Previous candle UTC | Next candle UTC | Missing candles |
| --- | --- | ---: |
| 2021-02-11 03:30 | 2021-02-11 05:00 | 5 |
| 2021-03-06 01:45 | 2021-03-06 03:30 | 6 |
| 2021-04-20 01:45 | 2021-04-20 04:30 | 10 |
| 2021-04-25 04:00 | 2021-04-25 08:45 | 18 |
| 2021-08-13 01:45 | 2021-08-13 06:30 | 18 |
| 2021-09-29 06:45 | 2021-09-29 09:00 | 8 |
| 2023-03-24 12:30 | 2023-03-24 14:00 | 5 |

Research and walk-forward consumers must reject or explicitly exclude windows
whose required input intersects these gaps. A checksum-valid source is not the
same as a continuous time series.

## Incremental updater acceptance

The Ningxia CPU host now runs the pinned `423de73` updater every 13 minutes.
After the historical snapshot was published, a manual systemd run read the
gap-aware snapshot successfully and returned `no_change` with exit status 0.
The timer was then restored and observed active. Historical gaps are preserved,
while a new gap in the incremental tail fails closed before publication.

## Boundary

This acceptance completes BTCUSDT from 2021-01-01 through the latest completed
candle at observation time. ETHUSDT and BNBUSDT remain pending, so the parent
three-symbol data-lake milestone is not complete. This dataset grants no PAPER,
TESTNET, or LIVE order permission; LIVE order placement remains disabled.
