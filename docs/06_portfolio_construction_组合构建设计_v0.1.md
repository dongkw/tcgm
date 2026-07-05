# 06 portfolio_construction 组合构建设计 v0.1

> 本文档是“天才交易员”的第六步细化设计。  
> 目标是解决多只股票同时触发时，有限本金应该买谁、买多少、先买什么、放弃什么。  
> 本文档只做设计，不写代码。

---

## 1. 设计目标

06 要解决的问题：

1. 多只股票同时出现买入信号时如何排序。
2. 本金有限时如何分配资金。
3. 当前已有持仓时，是否还能继续买同一只股票。
4. 行业、主题、资产类型是否过度集中。
5. 买入计划如何输出给模拟盘和后续人工确认。

06 的输出不是策略结论，而是组合层面的资金分配方案。

```text
strategy_engine 判断：这只股票是否值得买
risk_control 判断：这笔交易是否允许
portfolio_construction 判断：在所有可买股票里，优先买谁、买多少
```

---

## 2. 模块边界

### 2.1 06 负责

- 汇总多个 `decision_result`。
- 读取当前账户、现金、持仓和仓位。
- 按优先级排序候选买入。
- 计算每只股票建议买入金额和数量。
- 控制单票、行业、总仓位的组合约束。
- 输出 `allocation_plan` 和 `order_intent`。

### 2.2 06 不负责

- 不重新判断股票基本面好坏。
- 不重新生成买入或卖出结论。
- 不绕过风控模块。
- 不直接写成交。
- 不调用 AI 做黑盒排序。

如果策略结论是 `WAIT`、`DO_NOT_BUY`、`DATA_BLOCKED`，组合构建不能把它升级为买入。

---

## 3. 输入输出

### 3.1 输入

```text
candidate_decisions      多只股票的 decision_result
portfolio_context        当前账户资金和持仓
risk_check_results       风控结果
market_context           市场环境
watchlist_metadata       自选股标签、行业、用户优先级
strategy_version         策略版本
trade_date               交易日
```

`portfolio_context` 至少包含：

```text
account_id
available_cash
total_assets
cash_reserve_pct
equity_position_pct
today_buy_used
max_daily_buy_amount
positions
```

单个候选至少包含：

```text
symbol
name
asset_type
industry
final_action
confidence
data_quality_score
reference_price
position_plan
risk_check_result
```

### 3.2 输出

```text
allocation_plan
order_intents
deferred_candidates
rejected_candidates
portfolio_warnings
```

`allocation_plan` 示例：

```json
{
  "allocation_id": "alloc_20260706_000001",
  "account_id": "paper_default",
  "trade_date": "2026-07-06",
  "strategy_version": "strategy_v0.1",
  "cash_before": 100000.0,
  "cash_reserved": 20000.0,
  "buy_budget": 30000.0,
  "planned_buy_amount": 12000.0,
  "planned_position_count": 3,
  "status": "READY_FOR_CONFIRM"
}
```

`order_intent` 示例：

```json
{
  "intent_id": "oi_20260706_000001",
  "allocation_id": "alloc_20260706_000001",
  "symbol": "002563",
  "side": "BUY",
  "rank": 1,
  "score": 82.5,
  "planned_cash_amount": 5000.0,
  "planned_quantity": 900,
  "reference_price": 5.55,
  "reason": "WATCH_SMALL, high data quality, no current exposure",
  "status": "READY_FOR_CONFIRM"
}
```

---

## 4. 核心原则

### 4.1 本金有限优先

系统不能只输出“可以买”，必须输出：

- 买多少。
- 买入后剩余现金是多少。
- 买入后单票仓位是多少。
- 哪些候选因为本金不够被放弃。

### 4.2 先保命再收益

买入预算必须先扣除现金保留：

```text
usable_cash = available_cash - total_assets * cash_reserve_pct
```

如果 `usable_cash <= 0`：

```text
allocation_plan.status = NO_BUY_BUDGET
```

所有买入候选只记录，不下单。

### 4.3 卖出和买入分开处理

卖出类信号优先进入风险处理和人工确认，不在 06 中为了腾出现金而假设一定能成交。

第一版保守规则：

- 不使用“预计卖出回款”安排当日买入。
- 只有卖出真实成交后，现金才进入可用现金。
- 后续可增加 `use_same_day_sell_cash = true` 配置，但必须经过风控确认。

### 4.4 排序必须透明

每个候选的排序分数必须能拆解。

禁止：

- AI 直接说“这只更好”但没有分数明细。
- 只按涨幅排序。
- 只按用户主观喜欢排序。
- 只按单一指标排序。

---

## 5. 候选池处理

### 5.1 候选来源

候选来自：

```text
watchlist_scan
holding_review
manual_candidate
historical_replay
```

第一版主要来自：

```text
多个 BUY_EVALUATION decision_result
```

### 5.2 候选状态

```text
ELIGIBLE        可进入排序
DEFERRED        延后观察
REJECTED        不允许买入
RECORD_ONLY     只记录，不参与资金分配
```

映射规则：

| final_action | 组合处理 |
|---|---|
| `BUY` | 进入候选排序 |
| `WATCH_SMALL` | 进入候选排序，但默认小仓 |
| `WAIT` | `RECORD_ONLY` |
| `DO_NOT_BUY` | `REJECTED` |
| `DATA_BLOCKED` | `REJECTED` |
| `PRE_EVALUATION` | `RECORD_ONLY` |
| `HOLD` | `RECORD_ONLY` |

### 5.3 去重规则

同一股票同一交易日可能出现多个信号。

保留优先级：

```text
DATA_BLOCKED / DO_NOT_BUY 硬风险
  ↓
卖出类风险信号
  ↓
最新 BUY / WATCH_SMALL 信号
  ↓
旧信号只归档
```

同一股票同时出现买入和卖出信号时：

```text
status = CONFLICT
不生成买入计划
要求人工复核
```

---

## 6. 评分模型

第一版使用透明加权评分，不做机器学习。

```text
total_score =
  signal_score
  + data_quality_score
  + risk_reward_score
  + portfolio_fit_score
  + liquidity_score
  + user_priority_score
  - penalty_score
```

### 6.1 signal_score

按策略结论给基础分：

| final_action | 分数 |
|---|---:|
| `BUY` | 40 |
| `WATCH_SMALL` | 25 |

信心加分：

| confidence | 加分 |
|---|---:|
| `HIGH` | 15 |
| `MEDIUM` | 8 |
| `LOW` | 0 |

### 6.2 data_quality_score

```text
data_quality_score = data_quality_summary.score * 0.2
```

如果数据质量低于 75：

```text
候选最高只能 WATCH_SMALL
```

如果数据质量低于 60：

```text
REJECTED
```

### 6.3 risk_reward_score

第一版只做粗略风险收益评分：

```text
risk_reward_score = min(expected_upside_pct / stop_loss_pct, 3) * 5
```

如果没有上行空间或止损数据：

```text
risk_reward_score = 0
```

缺少证伪点时不得给高分。

### 6.4 portfolio_fit_score

鼓励组合分散，惩罚过度集中。

加分：

- 当前没有同股持仓。
- 行业当前仓位低。
- 账户现金充足。
- 买入后仍满足现金保留线。

扣分：

- 已有同股较高仓位。
- 行业仓位已高。
- 当前总仓位已高。
- 当天已买入金额接近上限。

### 6.5 user_priority_score

用户可以给自选股设置优先级。

```text
HIGH     +10
NORMAL   +0
LOW      -10
```

用户优先级只能影响排序，不能绕过硬风控。

---

## 7. 资金分配规则

### 7.1 买入预算

```text
cash_reserve = total_assets * cash_reserve_pct
usable_cash = max(available_cash - cash_reserve, 0)
daily_budget_left = max_daily_buy_amount - today_buy_used
buy_budget = min(usable_cash, daily_budget_left)
```

如果 `buy_budget < reference_price * 100 + estimated_cost`：

```text
没有任何买入能力
```

### 7.2 单票上限

每只股票计划买入后必须满足：

```text
position_value_after <= total_assets * max_single_position_pct
```

如果已有持仓接近上限：

```text
planned_cash_amount = 0
status = REJECTED_SINGLE_POSITION_LIMIT
```

### 7.3 默认仓位

第一版默认：

| 候选类型 | 默认目标 |
|---|---:|
| `BUY` | 账户总资产 8%-12% |
| `WATCH_SMALL` | 账户总资产 3%-5% |
| 低信心 | 不超过 3% |
| 数据质量一般 | 不超过 3% |

实际金额取以下最小值：

```text
min(
  策略建议金额,
  默认目标金额,
  单票剩余额度,
  buy_budget 剩余额度
)
```

### 7.4 100 股整数

买入数量：

```text
raw_quantity = planned_cash_amount / reference_price
quantity = floor(raw_quantity / 100) * 100
```

如果不足 100 股：

```text
status = DEFERRED_CASH_NOT_ENOUGH_FOR_ONE_LOT
```

### 7.5 分配算法

第一版采用简单排序分配：

```text
候选过滤
  ↓
计算评分
  ↓
按 score 降序排序
  ↓
逐个分配资金
  ↓
资金不足则缩小或跳过
  ↓
输出 allocation_plan
```

后续可扩展为：

- 等权分配。
- 风险预算分配。
- 波动率倒数分配。
- 行业中性分配。
- 最大化收益风险比。

---

## 8. 集中度约束

### 8.1 单票集中度

默认：

```text
max_single_position_pct = 30%
```

实际值由账户和风控共同决定。

### 8.2 行业集中度

第一版如果缺行业数据，可先只记录警告。

后续有行业数据后：

```text
max_industry_position_pct = 40%
```

超过时：

```text
降级买入金额
或直接 DEFERRED_INDUSTRY_LIMIT
```

### 8.3 总仓位

默认：

```text
max_equity_position_pct = 80%
```

市场环境差时由 07 风控降低。

---

## 9. 输出给模拟盘

06 不直接成交，只输出：

```text
order_intent
```

`order_intent` 再进入 04 模拟盘或未来实盘人工确认。

```text
allocation_plan
  ↓
order_intent
  ↓
risk_check
  ↓
paper_order / planned_order
```

第一版可先不写代码，但后续开发时应避免让 `paper_trading apply` 直接处理多个买入候选。多候选应先经过 06。

---

## 10. 异常处理

必须拒绝或延后：

- 缺少价格。
- 价格交易日不一致。
- 数据质量阻塞。
- 风控拒绝。
- 买入不足 100 股。
- 现金不足。
- 超过单票上限。
- 超过总仓位上限。
- 同一股票买卖信号冲突。
- 非交易日。

异常状态：

```text
PRICE_MISSING
PRICE_TIME_MISMATCH
DATA_BLOCKED
RISK_REJECTED
CASH_NOT_ENOUGH
CASH_NOT_ENOUGH_FOR_ONE_LOT
SINGLE_POSITION_LIMIT
INDUSTRY_LIMIT
TOTAL_POSITION_LIMIT
CONFLICT_SIGNAL
NON_TRADING_DAY
```

---

## 11. 第一版 MVP

第一版实现时建议支持：

- 从多个 `decision_result` 读取候选。
- 只处理 A 股股票。
- 只处理买入候选。
- 按透明评分排序。
- 按现金、单票上限、100 股整数分配。
- 输出 `allocation_plan.json`。
- 输出 Markdown 组合计划报告。

第一版暂不支持：

- 行业精细约束。
- 多策略资金池。
- 波动率优化。
- 协方差矩阵。
- 自动实盘下单。
- 复杂组合优化。

---

## 12. 验收标准

06 后续开发完成后，应满足：

- 多只股票同时触发时能生成排序。
- 本金不足时能明确放弃哪些候选。
- `BUY` 优先级通常高于 `WATCH_SMALL`。
- 数据差或风控拒绝的候选不能进入买入计划。
- 买入金额不超过可用预算。
- 买入后不突破单票上限。
- 买入数量满足 100 股整数倍。
- 输出能追溯到每个 `decision_id`。
- 能解释每只股票为什么买、为什么少买、为什么不买。

---

## 13. 与前后模块衔接

02 提供：

```text
decision_result
```

05 提供：

```text
portfolio_context
available_cash
current_positions
```

07 提供：

```text
risk_check_result
risk_limits
```

06 输出：

```text
allocation_plan
order_intent
deferred_candidates
```

04 模拟盘消费：

```text
order_intent 或人工确认后的 decision_result
```

09 工作流负责：

```text
盘前批量扫描
调用 06 生成今日计划
盘后复盘 allocation_plan 的执行结果
```
