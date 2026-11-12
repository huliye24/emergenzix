# 反身性模型流水线接口契约 v1

**状态：FROZEN / PAPER ONLY**

规范源位于 `backend/emergenzix_api/reflexive_pipeline_contracts.py`。它定义八个严格 JSON 契约：BRMG、BRLA、BRPM、BRGC 各自的输入和输出。所有对象拒绝未知字段，固定 `mode=PAPER`、`automatic_promotion=false` 和 `order_permission=false`，并携带输入哈希、时区时间和链路追踪 ID。

## 权限边界

| 模块 | 输出权限 | 明确禁止 |
| --- | --- | --- |
| BRMG | 生成带来源和哈希的市场路径引用 | 订单、仓位、晋级 |
| BRLA | 输出 WAIT/试探/持有/增减仓/退出意图 | 最终仓位、订单 |
| BRPM | 输出风险预算和建议目标仓位 | 超预算仓位、下单权限 |
| BRGC | 拒绝、限仓、强制退出或批准 PAPER 目标 | LIVE/TESTNET 订单、放宽硬上限 |

BRGC 的批准结果仍然只是 PAPER 目标，不是订单。订单创建、交易所凭证和策略自动晋级不属于本契约。

## 替换规则

任一实现都可以被替换，只要其输出能通过相应版本的严格契约。消费者只能读取契约字段，不得依赖某个模型的私有字段。契约改变必须创建新版本，旧版本不得原地改变语义。

`contract_schema_bundle()` 可导出八份标准 JSON Schema 和整体 SHA-256，用于隔离容器、GPU 节点和实验报告校验。导出目标必须是新文件，防止静默覆盖已冻结的契约证据。

## 当前限制

本轮只冻结接口，没有接入现有 PPO、Chronos、Qlib、仓位引擎或 PAPER 执行器。跨模块的运行时编排、数据新鲜度实际计算和 BRGC 硬上限裁决属于后续微任务。
