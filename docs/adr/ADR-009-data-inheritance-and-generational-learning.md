# ADR-009: Data inheritance and generational learning

- Status: Accepted for incremental implementation
- Date: 2026-11-04
- Scope: Research data and evidence; no order permissions

## Context

EAMI models are expected to change or be retired. Raw observations, experiment
inputs, predictions, outcomes, and failed hypotheses must remain reusable by
later model generations. A model-specific feature file is not a durable source
of truth because future models may define different states or recompute features.

ADR-004 already defines immutable S3/Parquet market snapshots. The missing
contract is a model-independent catalog that links raw facts, canonical data,
derived features, and research memory without treating any model generation as
the owner of historical data.

## Decision

Emergenzix adopts this invariant:

> Models are temporary. Data is durable. Evidence is inherited.

Research assets are classified into four layers:

1. `RAW` preserves source facts and has no derived-data parent.
2. `CANONICAL` normalizes raw facts and records source assets plus producer code.
3. `FEATURE` records a feature contract/version and can be recomputed from its
   source assets with pinned producer code.
4. `RESEARCH_MEMORY` preserves predictions, outcomes, hypotheses, experiments,
   and failures with their exact source lineage.

Large payloads remain in content-addressed S3/Parquet or another approved
storage system. SQLite initially stores the immutable catalog and fingerprints;
it does not become the data lake. PostgreSQL remains a future scaling decision.

Every derived asset must cite already registered parents and a producer-code
SHA-256. Every feature asset must also cite a feature version. Credentials and
signed query parameters are forbidden in catalog URIs. Catalog records are
append-only and integrity checked when read.

Model training and experiments must pin asset IDs, not moving `latest` pointers.
New generations may recompute and reinterpret history, but must publish new
derived assets rather than overwrite old results.

## Safety boundary

Data registration, inheritance, and model status never grant PAPER, TESTNET, or
LIVE order permission. Training schedules and execution policies remain separate
reviewed capabilities. LIVE remains disabled.

## Incremental implementation

1. Register reusable data assets and resolve complete lineage.
2. Link model experiments and prediction/outcome records to pinned asset IDs.
3. Add deterministic training jobs that consume pinned lineage.
4. Add 24-hour scheduling only after replay, idempotency, stale-data, and failure
   recording gates are tested.

The first increment implements only step 1.
