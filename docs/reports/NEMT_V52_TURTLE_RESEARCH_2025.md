# NEMT v5.2：公开趋势研究、海龟规则与2025实验

## 设计依据

公开研究显示，时间序列动量在多类期货市场的一至十二个月尺度存在持续性，
但更长期会出现部分反转。长期历史研究也发现趋势跟踪在不同经济环境中
具有正偏特征；这意味着系统不应依赖高胜率，而应控制小亏损并允许少数
趋势单充分延伸。

海龟规则提供了一个比v5.1的KNN预测器更简单、可证伪的执行结构：Donchian
突破确认趋势、ATR统一风险单位、2N硬止损、较短反向通道退出。公开Python
实现也普遍保留Donchian、ATR仓位和趋势退出结构。

需要保留的反证是：研究发现时间序列动量的部分表现来自波动率缩放本身，
而不完全是方向预测。因此v5.2把ATR仓位视为风险控制，不将其包装为NEMT
预测能力。

## v5.2规则

- 方向：仅由NEMT `critical_up` / `critical_down`决定；
- System 1：突破过去120根4小时K线（20天），60根反向通道退出；
- System 2：突破过去330根4小时K线（55天），120根反向通道退出；
- N：120根K线ATR；
- 初始止损：2N；
- 单笔风险：账户权益1%；
- 杠杆上限：3倍；
- 不设固定止盈；
- 本轮不加仓，以隔离首仓与退出规则的真实贡献。

## AWS 2025全年结果

| 指标 | 结果 |
| --- | ---: |
| 初始/期末权益 | 100 / 93.6679 USDT |
| 总收益率 | **-6.3321%** |
| 最大回撤 | 11.2840% |
| 交易数 | 18 |
| 盈利/亏损 | 3 / 15 |
| 胜率 | 16.67% |
| 盈利因子 | 0.5820 |
| 盈亏比 | 2.9101 |
| 平均持仓 | 7.9722天 |
| 多/空 | 8 / 10 |

## 解释

v5.2解决了v5.1的两个结构问题：多空恢复对称，平均持仓超过一周；实际
盈亏比也从无法估计提高到2.91。但18笔交易中15笔触发2N止损，三个盈利
趋势不足以覆盖假突破损失，盈利因子仅0.582。

所有交易都被记录为20日系统，说明当前优先级让快速系统遮蔽了55日系统，
也说明BTC单市场缺少原始海龟策略依赖的跨市场分散。下一步应做严格消融：

1. 只运行55日突破，检验是否减少假突破；
2. 将NEMT从方向生成器改为突破后的状态确认器；
3. 比较20日、55日和双速组合，不共享调参数据；
4. 在不加仓条件下先证明首仓具有正期望，再研究浮盈加仓；
5. 保留2025为固定样本外区间，不继续针对该年度反复优化。

## 来源

- Moskowitz, Ooi, and Pedersen, *Time Series Momentum*, Journal of Financial Economics (2012): https://fairmodel.econ.yale.edu/ec439/mosk.pdf
- Hurst, Ooi, and Pedersen, *A Century of Evidence on Trend-Following Investing* (2017): https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/AQR-JPM-Fall-2017.pdf
- Kim, Tse, and Wald, *Time Series Momentum and Volatility Scaling* (2016): https://doi.org/10.1016/j.finmar.2016.05.003
- Original Turtle rules archive: https://tradingblox.com/originalturtles/originalturtlerules.htm
- Public Turtle Python implementation: https://github.com/JustinJuYue/Turtle_Trading_System
- Public Donchian/ATR strategy example: https://github.com/JustACoder279/Donchian-Breakout-ATR-Risk-TradingView

本研究使用AI辅助检索与综合；所有策略结论仍以可复现历史实验为准。历史
模拟不代表未来收益，也没有创建任何真实订单。
