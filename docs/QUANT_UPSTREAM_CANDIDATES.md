# Quantitative and AI upstream candidate inventory

- **Status:** official sources inventoried; no runtime integration or source copy
- **Inspection date:** 2026-11-03
- **Product boundary:** research and PAPER evaluation only; this inventory grants
  no exchange, credential, strategy-promotion, TESTNET, or LIVE permission

This catalog records the projects requested for possible Emergenzix reuse. A
catalog entry is not an assertion that the project is production-ready, that
its data is redistributable, or that its strategy results are valid. Each
actual integration must be a separate micro-task with a pinned package or
submodule, isolated dependencies, focused tests, and an adapter owned by
Emergenzix.

## Decision table

| Project | Official source and inspected revision | Observed license | Useful boundary | Repository decision |
| --- | --- | --- | --- | --- |
| Microsoft Qlib | `microsoft/qlib` `54355232463878d2eebb91fe0ee5fa7fa1f5976c` (`main`) | MIT | factor research, model training, experiment recording, offline backtesting | **Approved for isolated evaluation.** Prefer the `pyqlib` package or a pinned submodule in a separate environment. Do not replace Emergenzix data provenance, risk, or execution semantics. |
| TradingAgents | `TauricResearch/TradingAgents` `c4544913a14ed3df5f1ed7b9e8483270ce20682a` (`main`) | Apache-2.0 | compare multi-agent research roles and evidence synthesis | **Approved as a design/reference candidate only.** LLM output must remain advisory; agents cannot approve strategies, change risk limits, hold exchange credentials, or create orders. Provider keys stay outside Git. |
| OpenBB | `OpenBB-finance/OpenBB` `2a392e890f6eb1f0f4e3a71742720af8f056333a` (`develop`) | V5 documentation states Apache-2.0; GitHub metadata for the inspected development head reports no SPDX assertion | normalized research-data access and provider adapters | **Package-level evaluation only.** Do not submodule the multi-gigabyte development tree. Pin a released V5 package and review each provider's data terms before storage or redistribution. |
| FinGPT | `AI4Finance-Foundation/FinGPT` `ac5c5443fbeeafad801e7a3c188e4f24cfaad4d8` (`master`) | MIT for repository code | offline financial-language and sentiment experiments | **Approved for isolated research evaluation.** Model weights, base models, datasets, and APIs have separate terms; none are approved by the repository license alone. Generated sentiment is evidence, never an order signal by itself. |
| FinRL | `AI4Finance-Foundation/FinRL` `7e98df461cbc7772f16a545a2f13b521e5d0b8f4` (`master`) | MIT | offline reinforcement-learning environments and benchmark comparisons | **Approved for isolated research evaluation.** No trained policy can promote itself or reach paper/testnet/live execution. Require leakage controls, deterministic seeds, costs, holdouts, and benchmark evidence. |
| Qbot | `UFund-Me/Qbot` `f0425ae4ae8bd02b79656b8f7039f4cd6874095e` (`main`) | **Conflicting signals:** GitHub metadata reports MIT while the README states CC BY-NC-SA 4.0 and non-commercial use | architecture and UI ideas only | **Do not copy, package, or submodule.** Resolve the repository-wide license and file-level provenance first. Its real-trading and notification paths are outside the current product boundary. |
| vn.py | `vnpy/vnpy` `c6e231caf32b7fc97e6459817fff66458cf7e7c4` (`master`) | MIT | event-engine, gateway, order-state, and adapter design reference | **Approved as a reference candidate.** The core dependency includes desktop/GUI requirements and gateways can trade; do not install it into the API runtime or enable a gateway. A future TESTNET adapter requires its own ADR and acceptance report. |
| vectorbt | `polakowo/vectorbt` `70da8fe7c2a57948ca065ac362fc416bcf31458c` (`master`) | Apache-2.0 with Commons Clause; not standard Apache-2.0 | high-throughput vectorized research/backtest comparison | **Do not vendor or add as a product dependency.** The Commons Clause restricts selling products or services whose value derives substantially from the software. Obtain legal review or a license exception before integration. Concepts and independently implemented benchmarks may be evaluated without copying code. |

## Integration order

1. Evaluate Qlib in an isolated research environment against one immutable
   Emergenzix dataset; record its exact data transformation and compare metrics
   without changing the current engine.
2. Evaluate a released OpenBB V5 package for one public, redistribution-safe
   data provider. Preserve provider provenance and terms.
3. Evaluate FinRL and FinGPT only on the GPU research node with synthetic or
   approved public data, pinned models, deterministic configuration, and no
   exchange credentials.
4. Use TradingAgents and vn.py as architecture references until explicit
   adapter contracts and safety tests exist.
5. Keep Qbot and vectorbt excluded until their licensing questions are resolved.

## Mandatory acceptance for every future adapter

- Pin the upstream release or commit and preserve its license and notices.
- Use a separate environment/container when dependencies overlap the API.
- Use immutable, provenance-bearing inputs; never silently fetch or substitute
  data in an evaluation.
- Test fees, slippage, look-ahead leakage, incomplete bars, duplicate requests,
  timeouts, and non-finite outputs where relevant.
- Treat all model/agent/strategy results as research evidence only.
- Keep LIVE disabled and fail closed on unknown data, mode, balance, order, or
  risk state.
- Record observed verification; popularity or GitHub stars are not evidence of
  correctness, profitability, security, or license compatibility.

## Sources

- https://github.com/microsoft/qlib
- https://github.com/TauricResearch/TradingAgents
- https://github.com/OpenBB-finance/OpenBB
- https://github.com/AI4Finance-Foundation/FinGPT
- https://github.com/AI4Finance-Foundation/FinRL
- https://github.com/UFund-Me/Qbot
- https://github.com/vnpy/vnpy
- https://github.com/polakowo/vectorbt
