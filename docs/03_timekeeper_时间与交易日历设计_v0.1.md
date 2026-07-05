# 03 timekeeper 时间与交易日历设计 v0.1

> 本文档是“天才交易员”的第三步细化设计。  
> 目标是保证系统在正确的时间使用正确的数据，避免交易日错误、T+1 错误、公告生效错误、财报提前使用和回放未来函数。  
> 本文档只做设计，不写代码。

---

## 1. 为什么时间模块必须独立

交易系统里，时间错了，后面的判断基本都会错。

常见错误：

- 用盘后公告解释盘中交易。
- 用尚未披露的财报做历史回测。
- 把自然日当交易日处理 T+1。
- 周末或节假日错误解锁持仓。
- 把盘中价格当收盘价。
- 回放时用到了未来数据。
- 模拟盘当日买入后又当日卖出。

所以时间不能散落在各个模块里临时判断，必须有独立模块统一处理。

---

## 2. 核心目标

`timekeeper` 负责回答这些问题：

- 今天是不是 A 股交易日。
- 当前属于盘前、盘中、午休、盘后还是非交易时间。
- 某条数据在某个决策时点是否已经生效。
- 某个持仓今天是否可卖。
- 某个回放时点能看到哪些数据。
- 某个盘后数据能不能影响当天决策。
- 模拟盘成交是否违反 T+1。

---

## 3. 核心原则

### 3.1 所有数据必须带时间语义

不能只用模糊的 `date`。

必须区分：

```text
calendar_date       自然日
trade_date          交易日
session_name        交易时段
source_time         数据源时间
announce_time       公告披露时间
report_period       财报报告期
observed_at         系统抓取时间
effective_from      数据允许被策略使用的时间
decision_time       策略决策时间
order_time          委托时间
trade_time          成交时间
snapshot_time       持仓或账户快照时间
available_from      持仓可卖时间
```

### 3.2 所有策略必须站在当时视角

策略在 `decision_time` 只能使用：

```text
effective_from <= decision_time
```

的数据。

晚于 `decision_time` 的数据一律不能参与策略判断。

### 3.3 A 股时间默认使用北京时间

第一版默认：

```text
timezone = Asia/Shanghai
```

所有时间字段必须明确是否带时区。若外部接口没有时区，系统按 `Asia/Shanghai` 解释，并记录该假设。

### 3.4 交易日不能用自然日替代

T+1、盘前任务、盘后复盘、持仓解锁都必须按交易日历处理。

例如：

```text
周五买入
周六不可卖
周日不可卖
下一个交易日才可卖
```

---

## 4. A 股交易时段

第一版使用固定时段，后续可接交易所日历。

```text
PRE_MARKET      08:30 - 09:25
CALL_AUCTION    09:15 - 09:25
MORNING_TRADE   09:30 - 11:30
LUNCH_BREAK     11:30 - 13:00
AFTERNOON_TRADE 13:00 - 15:00
POST_MARKET     15:00 - 18:00
AFTER_HOURS     18:00 以后
NON_TRADING     非交易日
```

说明：

- 第一版盘中只做简单触发提醒。
- 第一版策略主判断优先在盘前和盘后执行。
- 盘中不做复杂基本面重算。

---

## 5. 交易日历

### 5.1 交易日历职责

交易日历必须能回答：

- 某天是不是交易日。
- 上一个交易日是哪天。
- 下一个交易日是哪天。
- 某个日期是否节假日或周末。
- 某个持仓什么时候 T+1 解锁。

### 5.2 第一版交易日历来源

第一版可以先使用本地交易日历表。

后续再考虑自动更新。

交易日历必须保守处理：

- 不确定是否交易日时，不执行交易动作。
- 交易日历缺失时，模拟盘暂停成交。
- T+1 解锁不能靠自然日推断。

### 5.3 trading_calendar 表

建议字段：

```text
exchange
calendar_date
is_trading_day
trade_date
previous_trade_date
next_trade_date
holiday_name
source_name
updated_at
```

---

## 6. 数据生效时间规则

### 6.1 行情数据

行情分三类：

```text
REALTIME_QUOTE    实时或近实时行情
DAILY_CLOSE       日收盘行情
HISTORICAL_BAR    历史 K 线
```

规则：

- 盘中实时行情只能用于盘中触发，不等于收盘价。
- 当日收盘价只能在 15:00 后用于盘后复盘。
- 日线技术指标默认在收盘后生效。
- 盘前计划只能使用上一个交易日及以前已经生效的数据。

示例：

```text
2026-07-04 盘前计划
只能使用 2026-07-03 收盘及之前已生效数据
不能使用 2026-07-04 收盘数据
```

### 6.2 财务数据

财务数据必须按公告披露时间生效，不按报告期生效。

字段：

```text
report_period
notice_date
announce_time
effective_from
```

规则：

- 有 `announce_time`：`effective_from = announce_time`。
- 只有 `notice_date` 没有具体时间：保守设置为下一交易日盘前生效。
- 回测时不能使用当时尚未披露的财报。
- 财报更正公告必须生成新的生效版本。

示例：

```text
2026 一季报报告期是 2026-03-31
但公告日是 2026-04-29
那么 2026-04-29 前的决策不能使用这份一季报
```

### 6.3 公告数据

公告按披露时间生效。

规则：

- 盘中披露：可以用于盘中风险提醒。
- 盘后披露：只能用于盘后复盘和下一交易日盘前计划。
- 只有标题没有原文：只能作为风险提示，不做强硬结论。
- 重大公告必须保留链接和披露时间。

### 6.4 持仓数据

持仓数据按账户或模拟盘快照时间生效。

字段：

```text
position_date
snapshot_time
total_quantity
available_quantity
locked_quantity
available_from
```

规则：

- 当日买入形成 `locked_quantity`。
- 下一交易日才转为 `available_quantity`。
- 卖出数量不能超过 `available_quantity`。

### 6.5 策略快照

`strategy_snapshot` 必须记录：

```text
decision_time
trade_date
effective_data_cutoff
data_as_of
```

含义：

- `decision_time`：本次决策发生时间。
- `effective_data_cutoff`：策略允许使用的数据截止时间。
- `data_as_of`：快照中各类数据对应的时间摘要。

策略引擎只能使用 `effective_from <= decision_time` 的数据。

---

## 7. T+1 规则

### 7.1 买入后锁定

当日买入：

```text
total_quantity += bought_quantity
locked_quantity += bought_quantity
available_quantity 不增加
available_from = next_trade_date
```

下一交易日盘前：

```text
available_quantity += locked_quantity
locked_quantity = 0
```

### 7.2 卖出校验

卖出前必须检查：

```text
sell_quantity <= available_quantity
```

如果卖出触发但无可卖数量：

```text
final_action = NO_SELL_T_PLUS
```

### 7.3 模拟盘限制

模拟盘必须遵守 T+1。

禁止：

- 当日买入后当日止损卖出。
- 用自然日解锁。
- 非交易日解锁。

---

## 8. 盘前、盘中、盘后边界

### 8.1 盘前

盘前任务允许使用：

- 上一交易日收盘行情。
- 已披露并生效的财报。
- 已披露并生效的公告。
- 当前持仓和可卖数量。
- 模拟盘账户快照。

盘前任务不允许使用：

- 当天尚未发生的盘中价格。
- 当天收盘价。
- 当天盘后公告。

盘前产出：

- 今日持仓风险。
- 今日买入观察。
- 今日触发价。
- 今日资金可用额度。

### 8.2 盘中

盘中任务允许使用：

- 实时行情。
- 盘前计划。
- 已生效的风险事件。

盘中任务主要做：

- 价格触发。
- 止损触发。
- 突破触发。
- 跌破关键位触发。
- T+1 可卖校验。

盘中不做：

- 复杂基本面重算。
- 大范围股票池重扫。
- AI 长文本分析。

### 8.3 盘后

盘后任务允许使用：

- 当天收盘数据。
- 当天盘后公告。
- 当天成交记录。
- 当天账户快照。

盘后任务主要做：

- 更新技术指标。
- 记录持仓快照。
- 记录账户快照。
- 生成复盘。
- 为下一交易日盘前计划做准备。

盘后数据不能反向影响当天盘中决策。

---

## 9. 回放时间规则

回放是为了验证策略在历史当时能否做出正确判断。

回放时必须固定：

```text
replay_decision_time
```

策略只能看到：

```text
effective_from <= replay_decision_time
```

禁止：

- 使用未来财报。
- 使用未来公告。
- 使用未来 K 线。
- 使用未来持仓状态。
- 用最终复权结果误导历史买卖。

回放结果必须记录：

```text
replay_id
strategy_version
replay_decision_time
data_cutoff
snapshot_id
decision_result
future_return_for_evaluation
```

注意：

`future_return_for_evaluation` 只能用于事后评估，不能进入当时的 `strategy_snapshot`。

---

## 10. 时间校验规则

每次生成 `strategy_snapshot` 前，必须执行时间校验。

规则：

| 编号 | 检查 | 失败处理 |
|---|---|---|
| T0-1 | `decision_time` 存在且带时区 | 暂停生成快照 |
| T0-2 | `trade_date` 能从交易日历确认 | 暂停交易动作 |
| T0-3 | 所有数据 `effective_from <= decision_time` | 剔除未生效数据 |
| T0-4 | 财报 `notice_date/announce_time` 存在 | 财务数据降级或剔除 |
| T0-5 | 公告披露时间存在 | 事件规则降级 |
| T0-6 | 持仓可卖数量按交易日历计算 | 卖出动作暂停 |
| T0-7 | 回放数据不晚于回放时点 | 回放失败 |
| T0-8 | 收盘数据不用于当天盘中决策 | 剔除数据 |

---

## 11. timekeeper 输入输出

### 11.1 输入

```text
current_time
exchange
raw_data_time_fields
account_position_time_fields
task_type
```

### 11.2 输出

```text
time_context
```

建议结构：

```text
timezone
calendar_date
trade_date
is_trading_day
session_name
previous_trade_date
next_trade_date
decision_time
effective_data_cutoff
allowed_data_types
blocked_data_reasons
t_plus_one_unlocks
time_warnings
```

所有 `strategy_snapshot` 都必须引用本次 `time_context`。

---

## 12. 数据表设计口径

### 12.1 trading_calendar

```text
exchange
calendar_date
is_trading_day
trade_date
previous_trade_date
next_trade_date
holiday_name
source_name
updated_at
```

### 12.2 market_sessions

```text
exchange
session_name
start_time
end_time
timezone
is_trading_session
```

### 12.3 data_effective_times

```text
source_table
source_id
symbol
data_type
source_time
observed_at
announce_time
effective_from
effective_rule
time_check_status
time_check_message
```

### 12.4 time_audit_logs

```text
audit_id
task_type
decision_time
snapshot_id
check_name
check_status
message
created_at
```

---

## 13. 第一版 MVP

第一版必须实现：

- 交易日判断。
- 上一个/下一个交易日。
- 盘前、盘中、盘后识别。
- 财报按公告日生效。
- 公告按披露时间生效。
- T+1 可卖数量。
- `strategy_snapshot` 时间校验。
- 回放时点数据过滤。

第一版暂不实现：

- 分钟级完整回放。
- tick 级成交模拟。
- 多市场跨时区交易。
- 自动交易所日历订阅。
- 夜间外盘联动策略。

---

## 14. 验收标准

03 完成后，后续开发应满足：

- 非交易日不生成交易动作。
- 盘前不能使用当天收盘数据。
- 盘后数据不能影响当天盘中决策。
- 财报不能在披露日前被策略使用。
- 公告不能在披露前被策略使用。
- 周五买入不能周末卖出。
- 当日买入不能当日卖出。
- 回放不能使用未来 K 线、公告和财报。
- 每个 `strategy_snapshot` 都有 `decision_time` 和 `effective_data_cutoff`。
- 每个时间异常都能在 `time_audit_logs` 中追溯。

---

## 15. 与 01 和 02 的衔接

01 负责数据分层。

03 负责告诉 01：

```text
哪些数据在当前 decision_time 可以进入 strategy_snapshot
```

02 负责策略判断。

03 负责保证 02 看到的数据：

```text
不是未来数据
不是未生效数据
不是错误交易日数据
不是违反 T+1 的持仓数据
```

