# 05 portfolio 持仓与资金系统设计 v0.1

> 本文档是“天才交易员”的第五步细化设计。  
> 目标是把真实持仓、模拟盘持仓、本金、现金、成本、可卖数量、资金流水和每日快照纳入决策闭环。  
> 本文档只做设计，不写代码。

---

## 1. 设计目标

05 要解决四个问题：

1. 当前到底持有什么。
2. 每只股票成本、仓位、可卖数量和原买入逻辑是什么。
3. 当前还有多少可用资金，今天最多还能买多少。
4. 每一次建议、执行、持仓变化和资金变化能否追溯。

本模块对应总架构中的两个部分：

```text
portfolio  持仓模块
accounts   资金与账户模块
```

第一版必须支持：

- 实盘账户资金快照。
- 模拟盘账户资金账本。
- 单账户持仓。
- A 股股票持仓。
- T+1 可卖数量。
- 成本价和浮盈亏。
- 买入逻辑和原证伪点。
- 止损价和计划仓位。
- 每日持仓快照。
- 每日账户快照。
- 资金流水和持仓流水。

第一版不做：

- 自动实盘下单。
- 券商账户自动同步。
- 多账户合并风控。
- ETF、基金、融资融券。
- 复杂组合优化。
- 多股票触发时的优先级分配。

多股票同时触发时“买谁、买多少”的排序和分配，放到 06 组合构建中设计。

---

## 2. 模块边界

### 2.1 portfolio 职责

`portfolio` 负责维护持仓事实。

它回答：

- 持有哪些股票。
- 每只股票持有多少。
- 哪些数量今天可以卖。
- 平均成本是多少。
- 当前市值和浮盈亏是多少。
- 当前仓位比例是多少。
- 当初为什么买。
- 原证伪点是什么。
- 止损价和计划仓位是什么。
- 今天是否发生持仓变化。

`portfolio` 不回答：

- 这只股票现在值不值得买。
- 多只股票同时触发时优先买哪只。
- 是否超过行业集中度。
- 组合整体是否应该降仓。

这些分别由 `strategy_engine`、`portfolio_construction` 和 `risk_control` 处理。

### 2.2 accounts 职责

`accounts` 负责维护资金事实和资金约束。

它回答：

- 初始本金是多少。
- 当前可用现金是多少。
- 冻结资金是多少。
- 持仓市值是多少。
- 总资产是多少。
- 今日可买额度是多少。
- 单票最多还能投入多少钱。
- 单日最多还能新增买入多少钱。
- 现金保留比例是多少。
- 已占用风险预算是多少。

`accounts` 不负责判断股票好坏，也不直接生成买卖结论。

### 2.3 与 04 模拟盘的关系

04 设计订单、成交、撮合、交易成本和绩效验证。

05 设计账户和持仓账本。

关系：

```text
paper_trade
  ↓
position_ledger
  ↓
cash_ledger
  ↓
positions
  ↓
account_snapshot
```

模拟盘成交后，必须通过 05 更新持仓和资金。

### 2.4 与 06 组合构建的关系

05 提供事实和约束：

```text
可用现金
单票当前仓位
总权益仓位
当前持仓列表
计划仓位
风险预算占用
```

06 基于这些输入决定：

```text
多只股票同时触发时买谁
每只买多少
是否行业过度集中
是否组合整体过仓
```

05 不做排序，只保证数据准确。

---

## 3. 核心原则

### 3.1 真实资金不存放在系统中

真实资金只存在券商账户或银行账户中。

本系统只保存：

- 实盘账户资金快照。
- 实盘持仓快照。
- 人工录入或后续券商接口同步的成交记录。
- 计划投入额度。
- 用户确认后的操作记录。

第一版不允许自动实盘下单。

### 3.2 模拟盘和实盘必须分开

账户必须区分：

```text
REAL    实盘账户，只记录快照和人工确认记录
PAPER   模拟盘账户，可完整模拟现金、持仓、订单和成交
```

禁止：

- 用模拟盘现金约束实盘建议。
- 用实盘成交污染模拟盘绩效。
- 把两个账户的本金合并计算。

### 3.3 持仓信息缺失时，卖出只能预评估

持仓卖出评估至少需要：

- 成本价。
- 当前仓位。
- 可卖数量。
- 原买入逻辑。
- 原证伪点。

缺少成本、仓位、原买入逻辑或原证伪点时：

```text
final_action 最高只能是 PRE_EVALUATION
```

可以提示风险，但不能给完整可执行卖出数量。

### 3.4 T+1 是持仓系统的一等约束

A 股持仓必须区分：

```text
total_quantity      总数量
available_quantity  今日可卖数量
locked_quantity     当日买入锁定数量
```

卖出建议和模拟盘成交都必须使用 `available_quantity`。

### 3.5 成本和仓位必须可追溯

平均成本、浮盈亏和仓位不是报告里临时算出来的文本，而是账本结果。

每次变化必须能追溯到：

- 哪笔成交。
- 哪条决策。
- 哪次人工修正。
- 哪个账户。
- 哪个交易日。

### 3.6 资金约束必须进入风控

所有买入建议必须经过资金校验。

常见降级：

- 可用现金不足：买入降级为观察或不执行。
- 现金低于保留比例：暂停新增买入。
- 超过单票资金上限：缩小建议仓位。
- 超过单日买入上限：等待下一交易日。

---

## 4. 账户模型

### 4.1 account

账户是资金和持仓的归属对象。

字段：

```text
account_id
account_name
account_type          REAL / PAPER
base_currency         CNY
initial_cash
cash_reserve_pct
max_single_position_pct
max_daily_buy_amount
is_active
created_at
updated_at
```

说明：

- `REAL` 账户的 `initial_cash` 是用户定义的计划本金，不代表系统托管资金。
- `PAPER` 账户的资金完全由系统模拟维护。
- 第一版默认单账户，但字段必须支持后续多账户。

### 4.2 account_state

账户当前状态。

字段：

```text
account_id
as_of_time
trade_date
available_cash
frozen_cash
market_value
total_assets
equity_position_pct
cash_pct
today_buy_used
today_sell_amount
risk_budget_used
source_type           MANUAL / PAPER / BROKER_SYNC
updated_at
```

计算：

```text
total_assets = available_cash + frozen_cash + market_value
equity_position_pct = market_value / total_assets
cash_pct = available_cash / total_assets
```

### 4.3 account_snapshot

每日账户快照用于复盘和绩效计算。

字段：

```text
snapshot_id
account_id
trade_date
snapshot_time
available_cash
frozen_cash
market_value
total_assets
daily_pnl
daily_return_pct
total_return_pct
equity_position_pct
cash_pct
position_count
source_type
created_at
```

生成时点：

- 盘前：用于今日计划。
- 盘后：用于复盘和次日基准。

第一版至少生成盘后快照。

---

## 5. 持仓模型

### 5.0 标的和行情数据归属

股票名称、交易所、资产类型、最新价格、日线行情等基础信息不应该只存在 `position` 里。

数据归属应分清：

```text
instruments        标的基础信息，一只股票一条
market_quotes      每日行情/最新行情，一个交易日一条或一个观察时点一条
positions          当前持仓状态，只保存持仓相关字段
position_snapshot  每日持仓快照，保存当日估值用的行情副本
```

`position.market_price` 和 `position_snapshot.market_price` 是从行情数据取来的估值价格副本，用于当时计算市值、浮盈亏和仓位。

不能把它们当作行情主数据源。

#### instruments

标的基础信息字段：

```text
instrument_id
symbol
name
exchange
asset_type
currency
lot_size
price_tick
t_plus_rule
price_limit_rule
is_active
updated_at
```

第一版只支持：

```text
asset_type = A_STOCK
```

#### market_quotes

行情字段：

```text
quote_id
symbol
trade_date
quote_time
price
open
high
low
close
previous_close
pct_change
volume
amount_yuan
source_name
observed_at
created_at
```

第一版可以暂时不单独落 SQLite 表，先从 `stock_data_<code>.json` 的 `quote` 读取。

但设计上必须承认：

```text
行情主数据属于 market_quotes
持仓和持仓快照只引用或复制行情价格
```

#### 为什么快照要复制价格

每日持仓快照必须保存当日估值价格，而不是只引用最新行情。

原因：

- 后续行情会更新。
- 复权口径可能变化。
- 第三方接口历史数据可能修正。
- 复盘时必须还原当日看到的价格、持仓市值和浮盈亏。

所以 `position_snapshot` 必须保存：

```text
market_price
market_value
unrealized_pnl
unrealized_pnl_pct
position_pct
quote_source
quote_time
```

这些是当日账户快照的事实副本。

### 5.1 position

`position` 表示当前持仓。

字段：

```text
account_id
symbol
name
asset_type
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
last_trade_date
holding_days
buy_logic
invalidation_point
stop_loss_price
planned_position_pct
position_status        ACTIVE / CLOSED / WATCH_REDUCED
updated_at
```

计算：

```text
market_value = total_quantity * market_price
unrealized_pnl = market_value - total_quantity * avg_cost
unrealized_pnl_pct = market_price / avg_cost - 1
position_pct = market_value / account.total_assets
```

说明：

- `buy_logic` 必须保存当初买入的核心逻辑。
- `invalidation_point` 必须保存原证伪点。
- `stop_loss_price` 是执行约束，不是预测价格。
- `planned_position_pct` 是计划最终仓位，不等于当前仓位。

### 5.2 position_snapshot

每日持仓快照用于复盘。

注意：

```text
position      当前持仓表，一只股票一条当前状态
position_snapshot  每日持仓快照表，一个账户每天对每只持仓各记录一条
```

也就是说，持仓快照不是“一个券只存一条数据”，而是“每天都保存当天的持仓状态”。

即使当天没有买卖，只要账户仍持有该股票，盘后也要生成一条当日快照。

字段：

```text
snapshot_id
account_id
symbol
trade_date
snapshot_time
total_quantity
available_quantity
locked_quantity
avg_cost
market_price
market_value
unrealized_pnl
unrealized_pnl_pct
position_pct
buy_logic
invalidation_point
stop_loss_price
data_source
created_at
```

用途：

- 盘后复盘。
- 持仓变化追踪。
- 策略回放。
- 真实执行和模拟盘对比。
- 还原任意交易日的账户持仓状态。

唯一性约束建议：

```text
account_id + symbol + trade_date
```

同一账户、同一股票、同一交易日只保留一条盘后快照。如需保存盘前、盘中、盘后多个版本，应额外增加：

```text
snapshot_type = PRE_MARKET / INTRADAY / POST_MARKET
```

### 5.3 closed_position

清仓后不删除持仓，而是归档。

字段：

```text
account_id
symbol
open_date
close_date
holding_days
avg_buy_cost
avg_sell_price
total_buy_amount
total_sell_amount
realized_pnl
realized_pnl_pct
buy_logic
invalidation_point
close_reason
related_decision_ids
created_at
```

用途：

- 统计胜率。
- 统计盈亏比。
- 复盘买入逻辑。
- 沉淀错误样本。

---

## 6. 资金流水和持仓流水

### 6.1 cash_ledger

资金流水记录现金变化。

字段：

```text
cash_ledger_id
account_id
trade_date
event_time
event_type
amount
cash_before
cash_after
related_order_id
related_trade_id
related_decision_id
note
created_at
```

`event_type`：

```text
INITIAL_CASH
BUY_OUTFLOW
SELL_INFLOW
COMMISSION
STAMP_TAX
TRANSFER_IN
TRANSFER_OUT
MANUAL_ADJUST
FREEZE
UNFREEZE
```

### 6.2 position_ledger

持仓流水记录数量变化。

字段：

```text
position_ledger_id
account_id
symbol
trade_date
event_time
event_type
quantity_change
total_before
total_after
available_before
available_after
locked_before
locked_after
avg_cost_before
avg_cost_after
related_order_id
related_trade_id
related_decision_id
note
created_at
```

`event_type`：

```text
BUY
SELL
T_PLUS_UNLOCK
MANUAL_ADJUST
CORPORATE_ACTION
CLOSE_POSITION
```

第一版可以先不处理分红送转等复杂公司行为，但必须预留 `CORPORATE_ACTION` 类型。

### 6.3 audit_log

人工修正必须有审计记录。

字段：

```text
audit_id
account_id
target_type          ACCOUNT / POSITION / CASH_LEDGER / POSITION_LEDGER
target_id
operation
before_value
after_value
reason
operator
created_at
```

禁止无记录地修改成本、现金、可卖数量。

---

## 7. T+1 可卖数量设计

### 7.1 买入后锁定

当日买入成交后：

```text
total_quantity += bought_quantity
locked_quantity += bought_quantity
available_quantity 不增加
```

生成持仓流水：

```text
event_type = BUY
```

### 7.2 下一交易日解锁

下一交易日盘前，由 `timekeeper` 提供 `next_trade_date`，执行：

```text
available_quantity += locked_quantity
locked_quantity = 0
```

生成持仓流水：

```text
event_type = T_PLUS_UNLOCK
```

### 7.3 卖出前校验

任何卖出计划必须满足：

```text
sell_quantity <= available_quantity
```

如果策略触发卖出但无可卖数量：

```text
final_action = NO_SELL_T_PLUS
```

并写入报告和决策记录。

### 7.4 非交易日不解锁

T+1 解锁必须按交易日历。

禁止：

- 按自然日加一天。
- 周末自动解锁。
- 节假日自动解锁。

---

## 8. 成本计算

### 8.1 买入后平均成本

买入成交后：

```text
new_avg_cost =
  (old_quantity * old_avg_cost + buy_quantity * fill_price + buy_costs)
  / (old_quantity + buy_quantity)
```

`buy_costs` 包含佣金和买入滑点成本。

### 8.2 卖出后成本

卖出不改变剩余持仓平均成本。

卖出产生已实现盈亏：

```text
realized_pnl =
  sell_quantity * sell_fill_price
  - sell_quantity * avg_cost
  - sell_costs
```

`sell_costs` 包含佣金、印花税和卖出滑点成本。

### 8.3 清仓

当 `total_quantity = 0`：

- 当前 `position` 状态变为 `CLOSED`。
- 写入 `closed_position`。
- 保留全部流水。
- 不删除历史记录。

### 8.4 实盘成本修正

实盘成本可能因为券商口径、分红、手续费或人工录入误差需要修正。

允许人工修正，但必须：

- 写入 `audit_log`。
- 说明原因。
- 保留修正前后值。
- 不影响已归档历史快照，除非显式重算。

---

## 9. 资金约束设计

### 9.1 可用现金

买入前必须检查：

```text
estimated_cash_needed <= available_cash
```

估算金额：

```text
estimated_cash_needed =
  quantity * estimated_price
  + estimated_commission
  + estimated_slippage
```

不足时：

- 调小数量。
- 调整为 100 股整数倍。
- 仍不足 100 股时拒绝买入。

### 9.2 现金保留比例

账户必须配置：

```text
cash_reserve_pct
```

新增买入后必须满足：

```text
available_cash_after >= total_assets * cash_reserve_pct
```

否则买入建议降级或暂停。

### 9.3 单票资金上限

账户层保存默认上限：

```text
max_single_position_pct
```

买入后单票仓位不得超过：

```text
min(account.max_single_position_pct, risk_control_adjusted_limit)
```

超限时，缩小建议数量。

### 9.4 单日买入上限

为避免一天内集中买入，账户可配置：

```text
max_daily_buy_amount
```

盘中和盘后都必须累计：

```text
today_buy_used
```

超过上限后，新增买入信号只进入观察或次日计划。

### 9.5 资金不足时的输出规则

资金不足不等于股票不好。

输出必须区分：

```text
策略结论：符合 / 不符合
资金结论：可买 / 资金不足 / 超仓
最终动作：买入 / 观察 / 不执行
```

---

## 10. 输入与输出

### 10.1 输入

05 接收：

```text
manual_account_snapshot      用户录入实盘资金和持仓
paper_trade                  模拟盘成交
decision_result              策略建议
market_quote                 当前价或收盘价
time_context                 交易日、盘前盘中盘后、T+1 信息
manual_adjustment            人工修正
```

### 10.2 输出

05 输出：

```text
account_state
position
account_snapshot
position_snapshot
cash_ledger
position_ledger
portfolio_context
```

其中 `portfolio_context` 给策略、风控和报告使用。

建议结构：

```text
account_id
account_type
trade_date
available_cash
total_assets
cash_reserve_pct
equity_position_pct
positions
position_count
today_buy_used
cash_constraints
missing_position_fields
```

单个持仓结构：

```text
symbol
total_quantity
available_quantity
locked_quantity
avg_cost
market_price
market_value
unrealized_pnl_pct
position_pct
buy_logic
invalidation_point
stop_loss_price
planned_position_pct
```

---

## 11. 与策略和风控的衔接

### 11.1 买入评估

买入评估需要从 05 获取：

- 可用现金。
- 总资产。
- 现金保留比例。
- 当前是否已有该股票持仓。
- 当前单票仓位。
- 今日已买入金额。

策略引擎输出 `position_plan` 后，05 和风控共同校验：

```text
建议金额是否小于可用现金
买入后是否低于现金保留线
买入后是否超过单票仓位上限
买入数量是否满足 100 股整数倍
```

### 11.2 持仓评估

持仓评估需要从 05 获取：

- 成本价。
- 当前盈亏。
- 当前仓位。
- 可卖数量。
- 原买入逻辑。
- 原证伪点。
- 止损价。

缺失处理：

| 缺失字段 | 处理 |
|---|---|
| `avg_cost` | 卖出评估只能预评估 |
| `position_pct` | 不能给完整仓位建议 |
| `available_quantity` | 不能给可执行卖出数量 |
| `buy_logic` | 只能预评估 |
| `invalidation_point` | 不能判断逻辑证伪 |

### 11.3 风控拦截

风控模块可以基于 05 数据执行：

- 单票仓位上限。
- 总权益仓位上限。
- 现金保留线。
- 当日买入上限。
- T+1 不可卖拦截。
- 连续亏损后暂停新增买入。

05 只提供事实，风控决定是否拦截或降级。

---

## 12. 盘前、盘中、盘后流程

### 12.1 盘前

盘前处理：

```text
加载上一交易日账户快照
  ↓
处理 T+1 解锁
  ↓
刷新持仓当前价或昨日收盘价
  ↓
生成 portfolio_context
  ↓
供盘前持仓扫描和买入计划使用
```

输出：

- 今日可卖数量。
- 今日可用现金。
- 今日新增买入额度。
- 当前持仓风险输入。

### 12.2 盘中

盘中只处理触发和约束，不重算复杂账本。

盘中可更新：

- 当前价。
- 当前市值。
- 浮盈亏。
- 触发提醒所需的仓位信息。

盘中不应随意改：

- 平均成本。
- 原买入逻辑。
- 原证伪点。

除非发生已确认成交或人工修正。

### 12.3 盘后

盘后处理：

```text
更新收盘价
  ↓
更新持仓市值和浮盈亏
  ↓
处理当日模拟盘成交
  ↓
记录持仓快照
  ↓
记录账户快照
  ↓
记录建议和执行差异
```

盘后快照是次日盘前计划的基准。

盘后快照必须覆盖账户当日全部仍在持仓的股票，而不是只记录当天发生交易的股票。

如果当天没有任何交易，也仍然要写入：

- 一条 `account_snapshot`。
- 每只当前持仓各一条 `position_snapshot`。

---

## 13. 数据表设计口径

第一版建议表：

```text
accounts
account_states
account_snapshots
positions
position_snapshots
closed_positions
cash_ledger
position_ledger
portfolio_audit_logs
```

与 04 共用或关联：

```text
orders
trades
daily_assets
performance_metrics
```

与 02 关联：

```text
decisions
decision_rules
```

关键外键关系：

```text
positions.account_id -> accounts.account_id
cash_ledger.account_id -> accounts.account_id
position_ledger.account_id -> accounts.account_id
position_ledger.related_decision_id -> decisions.decision_id
cash_ledger.related_trade_id -> trades.trade_id
position_snapshots.account_id + symbol -> positions.account_id + symbol
```

第一版实现时可以先用 SQLite，不引入复杂服务。

---

## 14. 异常和降级

### 14.1 账户资金缺失

如果 `available_cash` 缺失：

```text
不允许输出可执行买入数量
```

可以买入评估仍可给策略观察结论，但必须标注资金缺口。

### 14.2 持仓数量不一致

如果出现：

```text
available_quantity + locked_quantity > total_quantity
```

或数量为负：

```text
暂停卖出建议
要求人工修正
写入数据质量问题
```

### 14.3 成本价异常

如果：

```text
avg_cost <= 0
```

则：

- 不计算盈亏。
- 卖出评估降级为预评估。
- 要求补齐成本。

### 14.4 账户总资产异常

如果：

```text
total_assets <= 0
```

则：

- 暂停仓位计算。
- 暂停买入建议。
- 要求人工确认账户快照。

### 14.5 快照缺失

如果上一交易日快照缺失：

- 可以从当前账户状态重建今日基准。
- 报告中标注快照缺口。
- 不强行计算完整日收益。

---

## 15. 第一版 MVP

第一版建议实现：

- 创建账户。
- 录入初始本金。
- 录入或导入当前持仓。
- 维护可用现金。
- 维护总数量、可卖数量、锁定数量。
- 维护平均成本。
- 维护买入逻辑和证伪点。
- 根据成交更新持仓和现金。
- 盘前处理 T+1 解锁。
- 盘后生成持仓快照。
- 盘后生成账户快照。
- 输出 `portfolio_context` 给策略和风控。

第一版暂不实现：

- 券商自动同步。
- 实盘自动下单。
- 分红送转自动处理。
- 多账户合并。
- 多币种。
- 融资融券。
- 复杂税费。

---

## 16. 验收标准

05 后续开发完成后，应满足：

- 能创建 `REAL` 和 `PAPER` 账户。
- 能记录初始本金和可用现金。
- 能录入当前持仓、成本和原买入逻辑。
- 能区分总数量、可卖数量和锁定数量。
- 当日买入不会增加可卖数量。
- 下一交易日盘前能按交易日历解锁。
- 卖出数量不能超过可卖数量。
- 能根据成交更新现金和持仓。
- 能计算当前市值、浮盈亏和仓位比例。
- 能生成每日账户快照。
- 能生成每日持仓快照。
- 能记录资金流水和持仓流水。
- 能把流水关联到 `decision_id` 或 `trade_id`。
- 持仓缺成本、买入逻辑或证伪点时，卖出评估只能预评估。
- 资金不足时，买入建议能被降级或拒绝。
- 模拟盘和实盘账户不会混用。
- 人工修正有审计记录。

---

## 17. 与前后模块衔接

01 生成：

```text
strategy_snapshot
```

02 生成：

```text
decision_result
```

03 提供：

```text
time_context
交易日历
T+1 解锁日期
```

04 生成：

```text
paper_order
paper_trade
```

05 维护：

```text
account_state
position
cash_ledger
position_ledger
account_snapshot
position_snapshot
portfolio_context
```

06 消费：

```text
portfolio_context
账户资金约束
当前持仓和仓位
```

07 消费：

```text
账户风险暴露
单票仓位
现金保留比例
T+1 可卖状态
```

09 消费：

```text
盘前可用资金
盘中可卖数量
盘后账户和持仓快照
```
