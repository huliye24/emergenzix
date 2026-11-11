# BRLA-G2 阶段一数据集审计

审计协议 `emergenzix-brla-g2-stage1-dataset-audit-v1` 在训练编码器之前检查：六阶段覆盖、类别失衡、O/N边界饱和、相邻180天历史窗口重叠、相邻7天标签窗口重叠、三个分区的样本量、场景隔离和用途权限。

首版门槛为：六阶段全部出现，最大类别比不超过10，任一强度边界饱和率不超过35%，历史窗口最大重叠不超过97%，标签窗口不得重叠，每个分区至少30条样本，三个分区使用不同场景且用途匹配。

`ops/audit_brla_g2_real_btc_dataset.py`读取公开BTCUSDT 15分钟Parquet，默认每7天取一个锚点以避免未来标签重叠。真实历史不足以构造三个独立分区时，报告必须输出 `NOT_READY_FOR_ENCODER_TRAINING`；不得从同一时间区间复制开发集或封存集来满足形式上的覆盖率。

审计报告是研究证据，没有仓位、订单、自动晋级或LIVE权限。

## 2026-11-11真实BTC首轮结果

Binance BTCUSDT 15分钟公开历史覆盖2025-11-01至2026-11-03。按7天步长得到26条样本，标签窗口重叠为0，历史窗口最大重叠为96.11%，O/N没有边界饱和。六阶段中 `OLD_ORDER_FAILURE` 为0条；同时没有独立的DEVELOPMENT和SEALED_TEST分区。因此结论为 `NOT_READY_FOR_ENCODER_TRAINING`。完整摘要保存在 `research/training_runs/20261111-brla-g2-real-btc-dataset-audit-v2.json`。
