# 04 backtesting 回测与模拟盘设计 v0.1

> 本文档是“天才交易员”的第四步细化设计。  
> 目标是设计如何验证策略对错，如何用模拟盘测试决策质量，以及后续如何扩展为严谨回测系统。  
> 本文档只做设计，不写代码。

---

## 1. 设计目标

04 要解决三个问题：

1. 策略到底有没有用。
2. AI/规则建议和实际执行差在哪里。
3. 策略失效、数据错误、执行错误能不能复盘出来。

验证体系分两层：

```text
模拟盘 paper_trading
  用真实时间往前走，测试每天决策和执行

历史回测 backtesting
  用历史数据回放，测试策略长期表现
```

第一版优先做模拟盘。

严谨历史回测等数据、时间和策略快照稳定后再做。

---

## 2. 为什么先做模拟盘

模拟盘比回测更适合当前阶段。

原因：

- 现有数据还不是严格点时数据。
- 财报和公告精确披露时间还不完整。
- 策略规则还在调整。
- 持仓和资金系统还没完成。
- 先验证每日决策链路更重要。

第一版目标：

```text
每天按当前真实数据生成决策
  ↓
用虚拟账户执行
  ↓
记录结果
  ↓
观察策略是否长期有效
```

---

## 3. 核心原则

### 3.1 不允许未来函数

模拟盘和回测都只能使用当时已经生效的数据。

规则：

- 决策只能读取对应时点的 `strategy_snapshot`。
- `strategy_snapshot` 必须经过 `timekeeper` 检查。
- 未来收益只能用于事后评估，不能进入决策输入。
- 财报和公告必须按披露时间生效。

### 3.2 策略建议和执行必须分开

系统必须区分：

```text
decision_result   策略建议
order             模拟委托
trade             模拟成交
position          持仓变化
asset_snapshot    账户结果
```

策略建议正确，不代表执行正确。

执行正确，也不代表策略有正期望。

### 3.3 模拟盘必须遵守 A 股规则

必须遵守：

- T+1。
- 100 股整数买入。
- 卖出不能超过可卖数量。
- 非交易日不成交。
- 涨停可能买不到。
- 跌停可能卖不出。
- 佣金、印花税、滑点。

### 3.4 绩效不能只看赚钱

必须同时看：

- 收益。
- 回撤。
- 胜率。
- 盈亏比。
- 持仓天数。
- 连续亏损。
- 资金利用率。
- 相对基准收益。

---

## 4. 模拟盘范围

第一版模拟盘支持：

- 单账户。
- A 股股票。
- 日频决策。
- 收盘后或下一交易日执行。
- 买入、卖出、减仓、清仓、持有。
- 交易成本。
- T+1。
- 每日资产快照。

第一版模拟盘不支持：

- 分钟级撮合。
- tick 级成交。
- 多账户。
- 融资融券。
- 打新。
- ETF/基金。
- 实盘券商对接。

---

## 5. 模拟盘数据流

```text
decision_result
  ↓
order_intent
  ↓
risk_check
  ↓
paper_order
  ↓
fill_simulation
  ↓
paper_trade
  ↓
position_update
  ↓
cash_update
  ↓
daily_asset_snapshot
  ↓
performance_metrics
```

### 5.1 decision_result

来自 02 策略引擎。

模拟盘只消费结构化字段：

- `final_action`
- `position_plan`
- `trigger_prices`
- `execution_constraints`
- `decision_time`
- `snapshot_id`

模拟盘不重新做买卖判断。

### 5.2 order_intent

把策略建议转成模拟委托意图。

例如：

```text
WATCH_SMALL -> 买入观察仓
REDUCE_HALF -> 卖出可卖数量的 50%
CLEAR -> 卖出全部可卖数量
HOLD -> 不操作
```

### 5.3 paper_order

模拟委托。

不一定成交。

### 5.4 paper_trade

模拟成交。

只有成交后才更新持仓和现金。

---

## 6. 成交时点设计

第一版支持两种成交模式。

### 6.1 NEXT_OPEN

策略在盘后或盘前生成，下一交易日开盘模拟成交。

优点：

- 更接近真实执行。
- 避免用收盘后信息在当日成交。

缺点：

- 需要下一交易日开盘价。

### 6.2 SAME_CLOSE

策略在收盘前生成，当日收盘价成交。

第一版不推荐默认使用。

适用：

- 只有历史日线数据时做粗略研究。

风险：

- 容易隐含未来函数。
- 真实盘中不一定能按收盘价成交。

### 6.3 第一版默认

第一版默认：

```text
execution_mode = NEXT_OPEN
```

如果没有下一交易日开盘价，则订单保持待成交，不强行成交。

---

## 7. 交易成本

第一版成本模型：

```text
commission_rate = 0.0003
min_commission = 5
stamp_tax_rate_sell = 0.0005
slippage_rate = 0.001
```

说明：

- 佣金买卖都收。
- 印花税只卖出收。
- 滑点买入加价、卖出减价。
- 参数后续进入配置文件。

成交价格：

```text
buy_fill_price = reference_price * (1 + slippage_rate)
sell_fill_price = reference_price * (1 - slippage_rate)
```

---

## 8. A 股成交限制

### 8.1 买入数量

买入必须满足：

```text
quantity % 100 == 0
```

如果资金只能买不足 100 股：

```text
订单拒绝
```

### 8.2 卖出数量

卖出数量不能超过：

```text
available_quantity
```

可以卖出不足 100 股的剩余股数。

### 8.3 T+1

买入当天：

```text
locked_quantity += bought_quantity
available_quantity 不增加
```

下一交易日盘前：

```text
available_quantity += locked_quantity
locked_quantity = 0
```

### 8.4 涨跌停

第一版简化：

- 涨停买入：标记成交风险，可选择不成交。
- 跌停卖出：标记成交风险，可选择不成交。

默认保守规则：

```text
涨停不买入
跌停不卖出
```

---

## 9. 账户模型

### 9.1 paper_account

字段：

```text
account_id
account_name
initial_cash
available_cash
frozen_cash
market_value
total_assets
created_at
updated_at
```

### 9.2 paper_position

字段：

```text
account_id
symbol
name
total_quantity
available_quantity
locked_quantity
avg_cost
market_price
market_value
unrealized_pnl
unrealized_pnl_pct
position_pct
first_buy_date
holding_days
buy_logic
invalidation_point
```

### 9.3 paper_order

字段：

```text
order_id
account_id
decision_id
snapshot_id
symbol
side
order_type
requested_quantity
limit_price
status
reject_reason
created_at
```

状态：

```text
PENDING
FILLED
PARTIALLY_FILLED
REJECTED
CANCELLED
```

### 9.4 paper_trade

字段：

```text
trade_id
order_id
account_id
symbol
side
quantity
fill_price
gross_amount
commission
stamp_tax
slippage_cost
net_amount
trade_time
```

### 9.5 daily_asset_snapshot

字段：

```text
account_id
trade_date
available_cash
market_value
total_assets
daily_pnl
daily_return_pct
total_return_pct
max_drawdown_pct
position_count
equity_position_pct
```

---

## 10. 从 decision_result 到订单

### 10.1 买入类

| final_action | 模拟盘动作 |
|---|---|
| `BUY` | 按 `position_plan` 买入 |
| `WATCH_SMALL` | 按观察仓规则小仓买入 |
| `WAIT` | 不操作 |
| `DO_NOT_BUY` | 不操作 |
| `DATA_BLOCKED` | 不操作 |

### 10.2 卖出类

| final_action | 模拟盘动作 |
|---|---|
| `HOLD` | 不操作 |
| `REDUCE_HALF` | 卖出可卖数量的 50% |
| `REDUCE_TO_WATCH` | 卖到观察仓比例 |
| `CLEAR` | 卖出全部可卖数量 |
| `NO_SELL_T_PLUS` | 不操作，记录受限 |
| `PRE_EVALUATION` | 不操作 |
| `DATA_BLOCKED` | 不操作 |

### 10.3 第一版保守规则

第一版只有 `ACTIVE` 策略才能驱动模拟盘自动下单。

如果规则仍是 `DRAFT` 或 `PAPER_ONLY`：

- 可以记录虚拟信号。
- 不一定生成真实模拟订单。

当前第一轮策略还未验证，建议默认只记录信号，不自动买入。

---

## 11. 绩效指标

必须统计：

```text
total_return_pct
annualized_return_pct
max_drawdown_pct
win_rate
profit_loss_ratio
average_win_pct
average_loss_pct
largest_win_pct
largest_loss_pct
consecutive_losses
turnover_rate
average_holding_days
cash_usage_pct
benchmark_return_pct
excess_return_pct
```

### 11.1 胜率

```text
win_rate = 盈利交易数 / 已平仓交易数
```

### 11.2 盈亏比

```text
profit_loss_ratio = 平均盈利 / 平均亏损绝对值
```

### 11.3 最大回撤

```text
drawdown = 当前总资产 / 历史最高总资产 - 1
```

### 11.4 策略有效性

不能只看总收益。

策略有效至少要看：

- 是否跑赢基准。
- 回撤是否可接受。
- 亏损是否集中在少数票。
- 是否靠单笔运气贡献收益。
- 是否交易成本过高。

---

## 12. 错误归因

每次交易结束或复盘时，需要归因。

归因类型：

```text
STRATEGY_ERROR     策略规则错误
DATA_ERROR         数据错误或缺失
TIME_ERROR         时间或未来函数错误
EXECUTION_ERROR    成交假设或执行错误
USER_OVERRIDE      用户手动覆盖
MARKET_NOISE       市场随机波动
RISK_CONTROL       风控拦截或仓位控制问题
```

每个亏损样本都应该能记录：

- 当时策略为什么买。
- 当时为什么没卖。
- 哪个证伪点触发。
- 是否应该改规则。
- 是否应加入测试样本。

---

## 13. 历史回测设计

历史回测后续再做，但边界先定。

关键定义：

```text
历史回测的第一阶段 = 模拟盘的历史时间回放版本
```

它不应该另起一套交易逻辑，而是复用模拟盘的账户、订单、成交、持仓、现金、T+1、交易成本和风控逻辑。

区别只在时间来源：

```text
模拟盘：
  时间来自真实今天，每天向后运行

历史回放：
  时间来自用户指定区间，从历史某一天开始逐日回放
```

全流程跑通后，历史回放应作为优先开发项。原因是它可以快速验证策略，不需要等待真实模拟盘运行几个月。

### 13.1 历史回放优先版

第一阶段先做可用于策略验证的历史回放，不追求一开始就做成专业级全市场回测。

最小输入：

```text
stock_code
start_date
end_date
initial_cash
strategy_version
execution_mode
cost_model
```

示例：

```text
股票：002563
区间：2025-01-01 到 2025-12-31
本金：10000
策略：strategy_v1
执行：次日开盘价成交
```

运行方式：

```text
第 1 个交易日：
  构建当日可见 strategy_snapshot
  运行策略，生成 decision_result
  生成模拟委托
  用下一个交易日价格模拟成交
  更新现金和持仓

第 2 个交易日：
  重复同样流程

直到 end_date
```

输出结果：

```text
总收益率
最大回撤
交易次数
胜率
平均盈利
平均亏损
盈亏比
空仓天数
持仓天数
是否跑赢基准
每笔交易的原因和结果
策略失效样本
```

用途：

- 快速验证单个策略是否明显无效。
- 快速比较两个策略版本。
- 找出买点、卖点、止损、仓位规则的问题。
- 发现数据质量问题和时间穿越问题。
- 为后续更严谨回测积累样本。

限制：

- 单只股票半年或一年只能用于初步验证，不能证明策略长期有效。
- 必须逐步扩展到多只股票、不同年份、不同市场环境。
- 不能为了让历史结果好看而反复调参。
- 所有历史回放结果必须记录策略版本和数据版本。

### 13.2 回测必须使用点时数据

回测在某个历史时点只能看到当时已生效数据。

必须处理：

- 财报公告日。
- 公告披露时间。
- 历史成分变化。
- 停牌。
- 涨跌停。
- 复权口径。
- 交易成本。
- T+1。

### 13.3 严谨回测输入

```text
strategy_version
symbol_universe
start_date
end_date
initial_cash
execution_mode
cost_model
```

### 13.4 严谨回测输出

```text
backtest_id
equity_curve
orders
trades
positions
daily_assets
performance_metrics
error_cases
```

### 13.5 回测和模拟盘区别

| 项目 | 模拟盘 | 回测 |
|---|---|---|
| 时间 | 从今天往后跑 | 从历史往现在回放 |
| 数据 | 真实当下数据 | 历史点时数据 |
| 目的 | 验证实际运行 | 验证长期统计表现 |
| 风险 | 时间长 | 容易未来函数 |

---

## 14. 第一版 MVP

第一版建议先实现：

- 模拟账户。
- 持仓表。
- 订单表。
- 成交表。
- 每日资产快照。
- T+1。
- 交易成本。
- 根据 `decision_result` 记录信号。
- 手动确认是否生成模拟订单。

第一版暂不实现：

- 自动从 `BUY` 下单。
- 严谨历史回测。
- 分钟级撮合。
- 多账户。
- 可视化曲线。

全流程跑通后的优先项：

- 历史回放。
- 支持指定一只股票、起止日期、本金、策略版本。
- 复用模拟盘的账户、订单、成交、持仓、现金和 T+1 逻辑。
- 输出收益率、最大回撤、胜率、盈亏比、交易明细和策略失效样本。

---

## 15. 验收标准

04 后续开发完成后，应满足：

- 能创建模拟账户。
- 能记录初始本金。
- 买入不能超过可用现金。
- 买入数量必须是 100 股整数倍。
- 当日买入不能当日卖出。
- 卖出不能超过可卖数量。
- 能记录订单和成交。
- 能更新持仓和现金。
- 能生成每日资产快照。
- 能计算总收益、最大回撤、胜率、盈亏比。
- 能把每笔模拟交易关联到 `decision_id`。
- 能区分策略建议和实际执行。

---

## 16. 与 01-03 的衔接

01 生成：

```text
strategy_snapshot
```

02 生成：

```text
decision_result
```

03 保证：

```text
时间正确、T+1 正确、数据已生效
```

04 消费：

```text
decision_result
```

04 输出：

```text
paper_order
paper_trade
paper_position
daily_asset_snapshot
performance_metrics
```
