# 09 workflow 盘前盘中盘后设计 v0.1

> 本文档是“天才交易员”的第九步细化设计。  
> 目标是定义盘前、盘中、盘后、非交易日和历史回放的任务流程。  
> 本文档只做设计，不写代码。

---

## 1. 设计目标

09 要解决的问题：

1. 每天什么时候抓数据。
2. 什么时候生成买入计划。
3. 什么时候扫描持仓卖出风险。
4. 盘中应该做什么，不能做什么。
5. 盘后如何归档、复盘和生成次日准备数据。
6. 周末、节假日和历史回放如何处理。

工作流是系统调度层，不负责重新判断策略。

```text
workflow 负责按正确时间调用各模块
```

---

## 2. 核心原则

### 2.1 时间先行

任何工作流开始前必须先调用：

```text
timekeeper
```

获取：

```text
trade_date
calendar_date
session_name
is_trading_day
effective_data_cutoff
```

如果时间上下文不清楚：

```text
BLOCK_WORKFLOW
```

### 2.2 盘前做计划

盘前只生成计划，不假装已经成交。

输出：

```text
pre_market_plan
planned_order
trigger_price_list
```

### 2.3 盘中只处理触发

盘中不做复杂慢判断。

盘中做：

- 价格触发。
- 止损触发。
- 突破触发。
- 跌破关键线触发。
- 涨跌停提示。
- 人工确认提醒。

盘中不做：

- 大量抓财报。
- 大量重新计算历史指标。
- 反复让 AI 重新判断。
- 用盘后数据影响盘中决策。

### 2.4 盘后做归档和复盘

盘后使用当日收盘数据，生成：

- 当日持仓快照。
- 当日账户快照。
- 模拟盘报告。
- 策略表现记录。
- 次日准备数据。

### 2.5 非交易日不成交

周末、节假日、非交易时段：

- 可以做研究。
- 可以生成观察报告。
- 可以跑历史回放。
- 可以准备下一个交易日计划。
- 不生成模拟成交。

---

## 3. 工作流类型

```text
pre_market
intraday
post_market
non_trading_research
historical_replay
maintenance
```

### 3.1 pre_market

目标：生成今日交易计划。

### 3.2 intraday

目标：处理盘中触发和提醒。

### 3.3 post_market

目标：归档、复盘、生成次日准备数据。

### 3.4 non_trading_research

目标：周末或晚上做研究，不影响真实交易状态。

### 3.5 historical_replay

目标：用历史交易日快速验证策略。

### 3.6 maintenance

目标：备份、清理、数据库一致性检查。

---

## 4. 盘前流程

### 4.1 运行时间

建议：

```text
08:30 - 09:15
```

第一版可以手动执行。

### 4.2 输入

```text
上一交易日收盘数据
昨晚公告
当前账户状态
当前持仓
自选股列表
策略配置
风控配置
```

### 4.3 流程

```text
build_time_context
  ↓
daily_rollover
  ↓
检查上一交易日账户快照
  ↓
更新昨收行情和技术指标
  ↓
更新昨晚公告和风险事件
  ↓
生成 portfolio_context
  ↓
扫描当前持仓
  ↓
扫描自选股 / 观察池
  ↓
生成 decision_result
  ↓
risk_control
  ↓
portfolio_construction
  ↓
生成 pre_market_plan
  ↓
生成 trigger_price_list
  ↓
生成盘前报告
```

### 4.4 输出

```text
pre_market_plan.json
pre_market_report.md
trigger_price_list.json
allocation_plan.json
planned_orders.json
```

盘前报告必须说明：

- 今日可用现金。
- 今日可卖数量。
- 今日禁止交易原因。
- 买入候选排序。
- 持仓卖出风险。
- 关键触发价。
- 数据缺口。

---

## 5. 盘中流程

### 5.1 运行时间

```text
09:15 - 11:30
13:00 - 15:00
```

第一版可手动或低频轮询。

### 5.2 输入

```text
pre_market_plan
trigger_price_list
当前价格
当前持仓可卖数量
账户资金
```

### 5.3 流程

```text
build_time_context
  ↓
检查 session_name
  ↓
刷新候选股票当前价
  ↓
匹配 trigger_price_list
  ↓
生成 trigger_event
  ↓
risk_control 快速校验
  ↓
输出提醒或 planned_order
  ↓
等待人工确认
```

### 5.4 盘中触发类型

```text
BUY_BREAKOUT
BUY_PULLBACK
SELL_STOP_LOSS
SELL_BREAK_MA20
SELL_BREAK_MA60
SELL_BREAK_LOW20
LIMIT_UP_RISK
LIMIT_DOWN_RISK
PRICE_TIME_MISMATCH
```

### 5.5 盘中禁止事项

禁止：

- 非交易日成交。
- 未执行 `daily_rollover` 直接成交。
- 用盘后公告影响盘中。
- 数据缺失时自动补数据。
- AI 绕过规则临时给强结论。
- 同一 `decision_id` 重复执行。

---

## 6. 盘后流程

### 6.1 运行时间

建议：

```text
15:30 - 18:00
```

### 6.2 输入

```text
当日收盘行情
当日成交记录
当日模拟盘订单和成交
当日触发记录
当日用户操作记录
```

### 6.3 流程

```text
build_time_context
  ↓
更新当日收盘行情
  ↓
计算技术指标
  ↓
归档当日 decision_result / trigger_event
  ↓
应用模拟盘成交
  ↓
生成 account_snapshot
  ↓
生成 position_snapshot
  ↓
计算绩效指标
  ↓
归因建议和执行差异
  ↓
生成 post_market_report
  ↓
备份数据库和关键 JSON
```

### 6.4 输出

```text
post_market_report.md
account_snapshots
position_snapshots
performance_metrics
execution_diff_report
next_day_prepare_list
```

盘后报告必须包含：

- 今日账户资产变化。
- 今日持仓变化。
- 今日策略建议。
- 用户是否执行。
- 模拟盘是否执行。
- 盈亏变化。
- 风控触发。
- 明日关注点。

---

## 7. 非交易日研究流程

### 7.1 适用时间

- 周末。
- 节假日。
- 晚上。
- 盘后非执行时间。

### 7.2 可以做

- 生成研究报告。
- 修改候选策略。
- 跑固定样本。
- 跑历史回放。
- 整理自选股。
- 学习和解释财报。
- 复盘历史错误。

### 7.3 不可以做

- 生成模拟成交。
- 伪造当日成交。
- 把未来数据写入历史决策输入。
- 覆盖真实交易日快照。

非交易日输出必须标注：

```text
execution_allowed = false
report_type = RESEARCH_ONLY
```

---

## 8. 历史回放流程

历史回放是模拟盘的历史时间版本。

### 8.1 输入

```text
symbol_list
start_date
end_date
initial_cash
strategy_version
execution_mode
cost_model
```

### 8.2 流程

```text
初始化 replay_account
  ↓
for trade_date in historical_trade_dates:
    固定 time_context = trade_date
    构建当日可见 strategy_snapshot
    运行 strategy_engine
    运行 risk_control
    运行 portfolio_construction
    生成 paper_order
    按 execution_mode 模拟成交
    更新持仓和现金
    生成每日快照
  ↓
输出 replay_report
```

### 8.3 禁止未来函数

历史回放必须保证：

- 只能使用当日已公开数据。
- 财报按公告披露日生效。
- 公告按披露时间生效。
- 当日收盘后数据不能用于当日盘中决策。
- 下一日开盘成交不能反向影响前一日决策。

---

## 9. 任务状态机

每个工作流任务都应记录状态：

```text
PENDING
RUNNING
SUCCESS
FAILED
SKIPPED
BLOCKED
```

任务记录字段：

```text
workflow_run_id
workflow_type
trade_date
session_name
started_at
finished_at
status
input_params_json
output_refs_json
error_code
error_message
created_at
```

---

## 10. 幂等性

同一交易日同一工作流重复执行时，不能造成重复成交或重复快照污染。

规则：

- `daily_rollover` 同一账户同一交易日只能生效一次。
- 同一 `decision_id` 不能重复成交。
- 同一账户同一交易日默认只保留一条盘后账户快照。
- 同一账户同一股票同一交易日默认只保留一条盘后持仓快照。
- 重跑报告可以覆盖 Markdown，但必须记录生成时间。

---

## 11. 失败处理

### 11.1 数据抓取失败

处理：

- 标记数据缺失。
- 降级策略结论。
- 报告中说明。
- 不编造数据。

### 11.2 时间异常

处理：

```text
BLOCK_WORKFLOW
```

### 11.3 账本不一致

处理：

```text
BLOCK_TRADING
REQUIRE_AUDIT
```

### 11.4 AI 调用失败

处理：

- 不影响结构化决策。
- 报告降级为规则报告。
- 记录 AI 调用失败。

---

## 12. 第一版命令设计

当前已有：

```powershell
python .\build_decision.py 002563 --task buy
python .\paper_trading.py init --account paper_default --cash 100000
python .\paper_trading.py rollover --account paper_default --trade-date 2026-07-06
python .\paper_trading.py apply <decision_path> --account paper_default --confirm
python .\paper_trading.py snapshot --account paper_default
```

当前第四轮已经实现：

```powershell
python .\workflow.py pre-market --account paper_default --trade-date 2026-07-06 --symbols 002563,600398,601138
python .\workflow.py post-market --account paper_default --trade-date 2026-07-06
python .\workflow.py research --symbols 002563,600398,601138
```

后续可新增：

```powershell
python .\workflow.py intraday --account paper_default
python .\workflow.py replay --symbol 002563 --start 2025-01-01 --end 2025-12-31
```

---

## 13. 第一版 MVP

第一版建议实现：

- 手动 `pre-market`。
- 手动 `post-market`。
- 盘前调用 `rollover`。
- 批量生成第一轮 `decision_result`。
- 输出盘前 Markdown 计划。
- 盘后生成账户和持仓快照。
- 盘后输出模拟盘报告。

第一版暂不实现：

- 自动定时调度。
- 实时行情高频监控。
- Web Dashboard。
- 消息推送。
- 自动实盘下单。

---

## 14. 验收标准

09 后续开发完成后，应满足：

- 非交易日工作流不会生成成交。
- 盘前能生成今日计划。
- 盘前会执行 T+1 解锁。
- 盘中只处理触发，不重算复杂逻辑。
- 盘后能生成账户和持仓快照。
- 盘后能生成复盘报告。
- 工作流任务有状态记录。
- 重复执行不会造成重复成交。
- AI 失败不影响规则链路。
- 历史回放和真实模拟盘使用同一套交易账本逻辑。

---

## 15. 与前后模块衔接

01 数据抓取：

```text
提供行情、财报、公告和技术指标
```

02 策略引擎：

```text
生成 decision_result
```

03 时间系统：

```text
决定当前能运行哪类工作流
```

04-05 模拟盘和账户：

```text
执行 paper_trading、持仓和快照
```

06 组合构建：

```text
生成 allocation_plan
```

07 风控：

```text
拦截不允许执行的动作
```

08 数据库：

```text
记录任务、决策、账本和报告索引
```

10 AI：

```text
生成解释、摘要、复盘，不改变结构化结论
```
