# 08 database_schema 数据库设计 v0.1

> 本文档是“天才交易员”的第八步细化设计。  
> 目标是设计 SQLite 初版数据库、JSON/JSONL 迁移路线、索引、版本管理和后续扩展边界。  
> 本文档只做设计，不写代码。

---

## 1. 设计目标

08 要解决的问题：

1. 当前 JSON/JSONL 账本什么时候迁移到数据库。
2. SQLite 第一版应该有哪些表。
3. 哪些字段必须结构化，哪些可以保留 JSON。
4. 如何保证决策、信号、订单、成交、持仓和报告可追溯。
5. 是否需要 Redis、缓存、消息队列。

第一版结论：

```text
先用 SQLite，不部署 Redis、PostgreSQL、消息队列
但 schema、ID、字段类型和访问层从第一天就按 PostgreSQL 可迁移设计
保留 JSON 原始底稿
核心账本进入结构化表
```

优先级：

```text
第一优先级：支撑当前功能稳定运行
第二优先级：未来迁移 PostgreSQL 时少改业务代码
第三优先级：性能和复杂查询优化
```

第一版不追求一次性把所有数据都入库。

必须优先支撑：

- 模拟盘账户、持仓、T+1、资金流水。
- 决策结果、策略快照、风控结果、组合计划。
- 盘前、盘中、盘后工作流运行记录。
- 历史回放结果、绩效指标、策略调参记录。
- 报告文件索引。
- 后续第一版界面查询。

暂时可以继续留在文件中的内容：

- 完整原始 `stock_json`。
- 大字段抓取底稿。
- Markdown 报告正文。
- 调试中间文件。

---

## 2. 存储分层

第一版存储分三层：

```text
raw_files
  原始 JSON、抓取底稿、调试材料

sqlite_core
  决策、账户、持仓、订单、成交、快照、流水

reports
  Markdown 报告和可读输出
```

不要一开始把所有原始字段都拆表。

原则：

- 参与查询、风控、回测、复盘的字段必须结构化。
- 来源复杂、字段变化大的内容可以保留 JSON。
- 原始数据不覆盖，结构化数据可重算。
- 所有表必须带时间字段和版本字段。

---

## 3. 为什么第一版用 SQLite

SQLite 适合当前阶段：

- 单机本地使用。
- 数据量不大。
- 方便备份。
- 不需要服务进程。
- 适合 IDEA 和 CLI 开发。
- 足够支持模拟盘、策略记录和回放验证。

暂不引入 Redis：

- 当前没有高并发。
- 当前没有多进程任务队列。
- 当前没有实时盘口高频缓存。
- 使用文件缓存和 SQLite 表足够。

后续只有出现以下情况才考虑 Redis：

- 多进程任务争抢。
- 大量插件同时读取。
- 盘中高频行情缓存。
- Web Dashboard 需要低延迟实时状态。

---

## 3.1 PostgreSQL 迁移约束

第一版虽然使用 SQLite，但从设计上必须避免把业务逻辑绑死在 SQLite。

必须遵守：

```text
业务代码只访问 db repository / store 层
业务代码不直接拼 SQLite 方言 SQL
所有主键由应用生成，不依赖 AUTOINCREMENT
所有表名和字段名使用小写 snake_case
所有 JSON 字段统一以 _json 结尾
所有金额字段按 NUMERIC 语义设计
所有时间字段保存 ISO 8601，带时区或明确 trade_date
所有状态字段使用受控枚举字符串
所有跨表关系显式保留外键字段
```

禁止在第一版核心逻辑里依赖：

- SQLite `rowid`。
- SQLite `AUTOINCREMENT`。
- SQLite 专属 `INSERT OR REPLACE` 语义。
- SQLite JSON1 函数作为核心查询条件。
- SQLite FTS。
- 无类型约束的随意字段。

允许 SQLite 和 PostgreSQL 使用不同 migration SQL，但逻辑 schema 必须一致。

建议目录：

```text
src/ai_trader/db/
  connection.py
  migrations.py
  repositories.py
  dialects.py

migrations/
  sqlite/
  postgres/
```

迁移 PostgreSQL 时，优先替换：

```text
connection / migration / repository adapter
```

不应该大改：

```text
strategy_engine
paper_trading
workflow
historical_replay
strategy_iteration
```

---

## 4. 数据库文件

建议：

```text
data/ai_trader.sqlite3
```

备份：

```text
data/backups/ai_trader_YYYYMMDD.sqlite3
```

数据库文件不建议提交 Git。

`.gitignore` 应包含：

```text
data/*.sqlite3
data/*.sqlite
data/backups/
```

---

## 5. 通用字段规范

所有核心表建议包含：

```text
id / xxx_id
created_at
updated_at
source_type
schema_version
```

涉及交易日的表必须包含：

```text
trade_date
calendar_date
observed_at
```

涉及策略和决策的表必须包含：

```text
strategy_version
decision_id
snapshot_id
```

涉及外部数据的表必须包含：

```text
source_name
source_url
source_payload_hash
data_observed_at
effective_from
```

时间统一使用 ISO 8601 字符串，A 股默认 `Asia/Shanghai`。

---

## 5.1 逻辑类型映射

后续代码应按“逻辑类型”写，不直接把业务绑定到 SQLite 类型。

| 逻辑类型 | SQLite 第一版 | PostgreSQL 迁移目标 | 说明 |
|---|---|---|---|
| `id` | `TEXT PRIMARY KEY` | `TEXT PRIMARY KEY` 或 `UUID` | 第一版用应用生成字符串 ID，避免 rowid 依赖 |
| `symbol` | `TEXT NOT NULL` | `TEXT NOT NULL` | 股票代码保留前导 0 |
| `trade_date` | `TEXT` | `DATE` | SQLite 存 `YYYY-MM-DD` |
| `timestamp` | `TEXT` | `TIMESTAMPTZ` | ISO 8601，必须带时区或可推导时区 |
| `money` | `NUMERIC` | `NUMERIC(20,4)` | 代码层后续应逐步使用 Decimal |
| `price` | `NUMERIC` | `NUMERIC(20,6)` | 支持基金、ETF 和未来扩展 |
| `quantity` | `INTEGER` | `BIGINT` | 股票数量、成交量 |
| `ratio_pct` | `NUMERIC` | `NUMERIC(12,6)` | 百分比字段，不存 0-1 混合口径 |
| `bool` | `INTEGER` | `BOOLEAN` | SQLite 使用 0/1，repository 转换 |
| `status` | `TEXT` | `TEXT` 或 enum/domain | 第一版用受控字符串 |
| `json` | `TEXT` | `JSONB` | 字段统一命名为 `xxx_json` |
| `hash` | `TEXT` | `TEXT` | 文件、payload、导入去重 |

金额字段第一版可以继续兼容现有 float 结果，但数据库边界要按 `NUMERIC` 语义设计。

后续迁 PG 前，需要统一处理：

```text
float -> Decimal
TEXT timestamp -> TIMESTAMPTZ
TEXT json -> JSONB
INTEGER bool -> BOOLEAN
```

---

## 6. 表分组

### 6.1 元数据与迁移

```text
schema_migrations
runtime_tasks
audit_logs
file_artifacts
import_batches
```

### 6.2 标的和行情

```text
instruments
market_quotes
technical_indicators
valuation_snapshots
```

### 6.3 财务和事件

```text
financial_reports
announcements
risk_events
```

### 6.4 策略和决策

```text
strategy_snapshots
decision_results
decision_rule_results
```

### 6.5 账户和持仓

```text
accounts
account_states
positions
position_locks
account_snapshots
position_snapshots
cash_ledger
position_ledger
closed_positions
```

### 6.6 模拟盘

```text
paper_signals
paper_orders
paper_trades
performance_metrics
```

### 6.7 组合和风控

```text
risk_checks
allocation_plans
order_intents
```

### 6.8 AI 和报告

```text
ai_calls
reports
plugin_outputs
```

### 6.9 工作流和盘中触发

```text
workflow_runs
pre_market_plans
trigger_price_items
intraday_scans
trigger_events
```

### 6.10 历史回放和策略迭代

```text
replay_runs
replay_daily_records
performance_metrics
strategy_tuning_records
```

---

## 7. 核心表设计

### 7.0 schema_migrations / file_artifacts / import_batches

`schema_migrations` 用途：记录数据库版本。

```text
version
name
checksum
applied_at
execution_time_ms
```

主键：

```text
version
```

`file_artifacts` 用途：记录本地 JSON、JSONL、Markdown、报告、原始底稿文件。

```text
artifact_id
artifact_type
source_module
relative_path
absolute_path
content_hash
mime_type
record_count
trade_date
symbol
account_id
strategy_version
created_at
indexed_at
metadata_json
```

索引：

```text
artifact_type + created_at
symbol + trade_date
account_id + trade_date
content_hash
```

`import_batches` 用途：记录 JSON/JSONL 导入数据库的批次。

```text
batch_id
source_type
source_path
started_at
finished_at
status
record_count
success_count
failed_count
error_message
metadata_json
created_at
```

说明：

- 第一版导入 JSON/JSONL 时必须写入 `import_batches`。
- 每条导入记录建议带 `source_artifact_id`，方便回溯到原始文件。

### 7.1 instruments

用途：统一管理股票、ETF、基金。

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
industry
is_active
created_at
updated_at
```

唯一键：

```text
symbol + exchange
```

第一版只支持：

```text
asset_type = A_STOCK
```

### 7.2 market_quotes

用途：行情主数据。

```text
quote_id
symbol
trade_date
quote_time
open
high
low
close
price
previous_close
pct_change
volume
amount_yuan
source_name
observed_at
created_at
```

索引：

```text
symbol + trade_date
trade_date
```

### 7.3 technical_indicators

用途：技术指标。

```text
indicator_id
symbol
trade_date
ma20
ma60
high_20d
low_20d
change_20d_pct
change_60d_pct
atr14_pct
source_quote_version
created_at
```

### 7.4 strategy_snapshots

用途：保存策略输入快照。

```text
snapshot_id
symbol
task_type
trade_date
decision_time
strategy_version
data_quality_level
data_quality_score
payload_json
source_file
created_at
```

说明：

- `payload_json` 保存完整 `strategy_snapshot`。
- 常用查询字段单独列出。

### 7.5 decision_results

用途：保存策略输出。

```text
decision_id
snapshot_id
symbol
task_type
trade_date
decision_time
strategy_version
final_action
confidence
action_reason
human_review_required
payload_json
created_at
```

索引：

```text
symbol + trade_date
final_action
snapshot_id
```

### 7.6 accounts

```text
account_id
account_name
account_type
base_currency
initial_cash
cash_reserve_pct
max_single_position_pct
max_daily_buy_amount
is_active
created_at
updated_at
```

### 7.7 account_states

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
last_rollover_trade_date
updated_at
```

### 7.8 positions

```text
account_id
symbol
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
buy_logic
invalidation_point
stop_loss_price
planned_position_pct
position_status
updated_at
```

主键：

```text
account_id + symbol
```

### 7.9 position_locks

```text
lock_id
account_id
symbol
buy_trade_id
buy_trade_date
unlock_trade_date
locked_quantity
remaining_locked_quantity
status
created_at
released_at
```

必须满足：

```text
sum(OPEN.remaining_locked_quantity) == positions.locked_quantity
```

### 7.10 paper_signals

```text
signal_id
account_id
decision_id
snapshot_id
symbol
final_action
confidence
signal_action
source_decision_path
source_decision_hash
strategy_version
action_reason
position_plan_json
status
blocked_reason
created_at
```

### 7.11 paper_orders

```text
order_id
account_id
signal_id
decision_id
snapshot_id
symbol
side
order_type
requested_quantity
limit_price
reference_price
status
reject_reason
created_at
updated_at
```

### 7.12 paper_trades

```text
trade_id
order_id
account_id
decision_id
snapshot_id
symbol
side
quantity
reference_price
fill_price
gross_amount
commission
stamp_tax
slippage_cost
net_amount
trade_time
trade_date
quote_source
created_at
```

注意：

- `slippage_cost` 只用于归因展示。
- 现金变化使用 `net_amount`。

### 7.13 cash_ledger

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

### 7.14 position_ledger

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

### 7.15 risk_checks

```text
risk_check_id
account_id
decision_id
symbol
trade_date
risk_status
risk_level
allowed_action
original_action
max_cash_amount
max_quantity
blocking_rules_json
warning_rules_json
human_review_required
created_at
```

### 7.16 allocation_plans

```text
allocation_id
account_id
trade_date
strategy_version
cash_before
cash_reserved
buy_budget
planned_buy_amount
planned_position_count
status
created_at
```

### 7.17 order_intents

```text
intent_id
allocation_id
account_id
decision_id
symbol
side
rank
score
planned_cash_amount
planned_quantity
reference_price
reason
status
created_at
```

### 7.18 workflow_runs

用途：记录盘前、盘中、盘后、研究、未来定时任务的运行状态。

```text
workflow_run_id
workflow_type
account_id
trade_date
calendar_date
session_name
is_trading_day
started_at
finished_at
status
input_params_json
output_refs_json
error_code
error_message
created_at
```

索引：

```text
workflow_type + trade_date
account_id + trade_date
status + started_at
```

### 7.19 pre_market_plans

用途：保存盘前计划的结构化索引。

```text
plan_id
workflow_run_id
account_id
trade_date
session_name
execution_allowed
symbols_json
rollover_json
allocation_id
trigger_price_list_path
payload_json
created_at
```

说明：

- 完整盘前计划保存在 `payload_json`。
- 组合计划、订单意图、触发价拆到对应表后，`pre_market_plans` 只保留索引。

### 7.20 trigger_price_items

用途：保存盘中触发扫描使用的触发价。

```text
trigger_item_id
account_id
trade_date
symbol
task
task_type
decision_id
snapshot_id
final_action
confidence
reduce_trigger_price
clear_trigger_price
middle_trend_price
resistance_price
stop_loss_price
source_decision_path
created_at
```

唯一建议：

```text
account_id + trade_date + symbol + task_type + decision_id
```

### 7.21 intraday_scans

用途：记录每次盘中扫描。

```text
scan_id
account_id
trade_date
calendar_date
session_name
is_trading_day
allow_non_trading
started_at
finished_at
status
symbols_scanned
trigger_count
blocked_count
duplicate_count
input_refs_json
report_artifact_id
error_code
error_message
created_at
```

### 7.22 trigger_events

用途：保存盘中触发提醒。

```text
trigger_event_id
account_id
trade_date
scan_id
symbol
name
event_type
trigger_price
current_price
price_source
quote_trade_date
quote_time
data_time_precision
decision_id
snapshot_id
source_decision_path
source_plan_path
severity
suggested_action
execution_allowed
requires_human_confirm
risk_status
blocked_reason
created_at
```

去重键：

```text
account_id + trade_date + symbol + event_type + decision_id
```

注意：

- 第一版 `execution_allowed` 默认 false。
- `PRICE_TIME_MISMATCH` 必须可查询，不能只写报告。

### 7.23 replay_runs

用途：记录一次历史回放。

```text
replay_id
account_id
symbols_json
start_date
end_date
initial_cash
replay_mode
strategy_version
execution_mode
bar_dir
cash_reserve_pct
max_single_position_pct
max_daily_buy_amount
default_watch_cash
started_at
finished_at
status
output_root
report_artifact_id
performance_metric_id
error_code
error_message
created_at
```

### 7.24 replay_daily_records

用途：记录历史回放每日摘要。

```text
daily_record_id
replay_id
trade_date
symbols_scanned
released_quantity
holding_decisions
buy_decisions
risk_checks
ready_intents
orders
trades
account_snapshot_id
total_assets
available_cash
market_value
daily_pnl
blocked_reasons_json
allocation_status
created_at
```

索引：

```text
replay_id + trade_date
trade_date
```

### 7.25 performance_metrics

用途：保存回放和模拟盘绩效指标。

```text
performance_metric_id
source_type
source_id
account_id
strategy_version
start_date
end_date
initial_cash
final_assets
total_return_pct
annualized_return_pct
max_drawdown_pct
max_drawdown_start
max_drawdown_end
trade_count
buy_count
sell_count
win_rate
profit_loss_ratio
average_win_pct
average_loss_pct
largest_win_pct
largest_loss_pct
average_holding_days
cash_usage_pct
turnover_rate
benchmark_return_pct
excess_return_pct
payload_json
created_at
```

### 7.26 strategy_tuning_records

用途：保存策略调参和复盘记录。

```text
iteration_id
created_at
source_type
source_id
source_path
strategy_version
previous_strategy_version
account_id
symbols_json
period_start
period_end
metrics_json
auto_issues_json
manual_issues_json
blocked_reason_counts_json
worst_days_json
error_case_count
closed_position_count
hypothesis
rule_changes
risk_changes
position_changes
next_action
conclusion
tags_json
notes
```

索引：

```text
strategy_version + created_at
source_type + source_id
conclusion
```

### 7.27 reports

用途：索引 Markdown 报告，不把报告正文强制塞进数据库。

```text
report_id
report_type
title
account_id
symbol
trade_date
strategy_version
source_type
source_id
artifact_id
relative_path
created_at
metadata_json
```

### 7.28 ai_calls

用途：后续记录 AI 调用输入输出和成本。

```text
ai_call_id
purpose
model
request_time
response_time
status
input_hash
input_json
output_json
token_usage_json
cost_amount
related_type
related_id
created_at
```

第一版可以先建表但不写入，等 10 AI 接口再启用。

---

## 8. JSON 到 SQLite 的迁移路线

当前已实现 JSON/JSONL：

```text
data/strategy_snapshots/
data/decision_results/
data/paper_trading/
data/portfolio/
data/reports/
```

迁移分四步：

### 8.1 阶段一：继续 JSON 写入

目标：

- 验证字段够不够。
- 验证业务流程。
- 避免过早固化 schema。

### 8.2 阶段二：导入 SQLite

新增导入命令：

```text
python .\database.py import-json
```

把现有 JSON/JSONL 导入 SQLite。

第一版导入范围：

```text
portfolio/accounts.json -> accounts / account_states
portfolio/positions.json -> positions
portfolio/*ledger.jsonl -> cash_ledger / position_ledger / position_locks
paper_trading/*.jsonl -> paper_signals / paper_orders / paper_trades
strategy_snapshots/*.json -> strategy_snapshots
decision_results/*.json -> decision_results
workflows/*.jsonl -> workflow_runs
intraday/*.jsonl -> intraday_scans / trigger_events
replay/<id>/* -> replay_runs / replay_daily_records / performance_metrics
strategy_iterations/*.jsonl -> strategy_tuning_records
reports/*.md -> reports / file_artifacts
```

导入原则：

- 可重复导入。
- 用业务 ID 去重。
- 每次导入写 `import_batches`。
- 导入失败不能改坏原始 JSON/JSONL。

### 8.3 阶段三：双写

同时写：

```text
JSON/JSONL
SQLite
```

用于对账。

必须检查：

- 账户现金一致。
- 持仓数量一致。
- 成交数量一致。
- position_locks 汇总一致。
- workflow_runs 数量一致。
- trigger_events 去重结果一致。
- replay performance 指标一致。

### 8.4 阶段四：SQLite 主存储

稳定后：

```text
SQLite 作为主账本
JSON 作为导出和归档
```

### 8.5 阶段五：PostgreSQL 迁移

只有当出现以下需要时再迁移 PostgreSQL：

- 多设备或多人使用。
- Web Dashboard 长期运行。
- 数据量明显增大。
- 需要更强查询、并发、备份和权限管理。

迁移路线：

```text
1. 保持 repository 接口不变。
2. 增加 PostgreSQL connection adapter。
3. 增加 migrations/postgres。
4. 从 SQLite 导出逻辑数据。
5. 导入 PostgreSQL。
6. 对账账户、持仓、流水、成交、回放指标。
7. 切换配置 DATABASE_URL。
```

不允许在迁移时临时改业务规则来“修数据”。

---

## 9. 索引设计

常用索引：

```text
symbol + trade_date
account_id + trade_date
decision_id
snapshot_id
account_id + symbol
created_at
final_action
risk_status
```

回放和复盘常用查询：

- 某只股票一段时间内所有决策。
- 某个账户每日资产曲线。
- 某个策略版本的所有信号。
- 某次成交对应的决策和规则。
- 某天所有被拒绝的候选。
- 某次历史回放的每日资产和交易数。
- 某个策略版本的所有调参记录。
- 某天盘中触发过哪些事件。
- 某个报告对应的源数据和运行记录。

---

## 10. 数据一致性规则

必须满足：

```text
account_state.total_assets = available_cash + frozen_cash + market_value
position.market_value = total_quantity * market_price
sum(position.market_value) = account_state.market_value
sum(open position_locks.remaining_locked_quantity) = position.locked_quantity
paper_trade.order_id -> paper_orders.order_id
paper_order.signal_id -> paper_signals.signal_id
paper_signal.decision_id -> decision_results.decision_id
decision_result.snapshot_id -> strategy_snapshots.snapshot_id
workflow_run.output_refs_json 中引用的报告必须存在 reports 或 file_artifacts
trigger_event.scan_id -> intraday_scans.scan_id
replay_daily_record.replay_id -> replay_runs.replay_id
strategy_tuning_record.source_id 可追溯到 replay_runs 或 paper account
```

不一致时：

```text
BLOCK_WORKFLOW
REQUIRE_AUDIT
```

---

## 11. 审计与人工修正

人工修改以下内容必须写 `audit_logs`：

- 现金。
- 持仓数量。
- 成本价。
- 可卖数量。
- 锁定数量。
- 买入逻辑。
- 证伪点。

`audit_logs` 字段：

```text
audit_id
account_id
target_type
target_id
operation
before_value_json
after_value_json
reason
operator
created_at
```

禁止无记录地改账本。

---

## 12. 备份和保留策略

建议：

- 每天盘后备份 SQLite。
- 每次 schema migration 前备份。
- 原始 `stock_json` 保留最近 N 份。
- 报告按股票或日期保留最近 N 份。
- 模拟盘账本长期保留。

不应自动删除：

- 成交记录。
- 资金流水。
- 持仓流水。
- 决策结果。
- 风控结果。

如果空间压力大，先压缩归档，不直接删除。

---

## 13. 第一版 MVP

第一版数据库开发按功能优先，建议分两批。

### 13.1 MVP-A：主账本和现有功能查询

必须先做：

- `schema_migrations`
- `file_artifacts`
- `import_batches`
- `accounts`
- `account_states`
- `positions`
- `position_locks`
- `paper_signals`
- `paper_orders`
- `paper_trades`
- `cash_ledger`
- `position_ledger`
- `account_snapshots`
- `position_snapshots`
- `strategy_snapshots`
- `decision_results`
- `risk_checks`
- `allocation_plans`
- `order_intents`
- `workflow_runs`
- `intraday_scans`
- `trigger_events`
- `replay_runs`
- `replay_daily_records`
- `performance_metrics`
- `strategy_tuning_records`
- `reports`

MVP-A 目标：

- 能初始化 SQLite。
- 能导入现有 JSON/JSONL。
- 能支撑第一版界面查询。
- 能从数据库查账户、持仓、成交、回放、调参记录。

### 13.2 MVP-B：数据底稿结构化增强

后续再做：

- `instruments`
- `market_quotes`
- `technical_indicators`
- `valuation_snapshots`
- `financial_reports`
- `announcements`
- `risk_events`
- `ai_calls`
- `plugin_outputs`

MVP-B 目标：

- 提高策略回放和查询效率。
- 为 AI 接口、数据质量和自动化工作流提供更完整上下文。

第一版暂不做：

- 部署 PostgreSQL。
- Redis。
- 任务队列。
- 分布式锁。
- 多用户权限。
- 实盘券商对账。

但第一版写代码时必须保留 PostgreSQL 迁移边界。

---

## 14. 验收标准

08 后续开发完成后，应满足：

- 能初始化 SQLite。
- 能记录 schema 版本。
- 能把现有 JSON/JSONL 导入数据库。
- 能从数据库重建账户状态。
- 能查询某笔成交对应的决策、快照、规则。
- 能查询某天账户资产和持仓快照。
- 能校验现金、持仓、锁定数量一致性。
- 能备份数据库。
- 数据库异常不会导致无记录地丢失账本。

---

## 15. 与前后模块衔接

01-03 生成：

```text
strategy_snapshot
decision_result
```

04-05 当前写：

```text
JSON/JSONL 账本
```

08 后续迁移为：

```text
SQLite 主账本
JSON 归档
```

09 工作流消费：

```text
数据库中的任务状态、账户状态、决策记录和报告索引
```

10 AI 消费：

```text
结构化输入和可追溯上下文
```

---

## 16. 第一版界面查询需求

数据库第一版必须直接服务后续本地界面。

优先支持以下查询：

### 16.1 首页摘要

```text
账户总资产
可用现金
持仓市值
当日盈亏
累计收益率
最大回撤
当前持仓数
最近一次工作流状态
最近一次盘中触发数量
最近一次回放收益
最近一条策略调参记录
```

主要表：

```text
account_states
account_snapshots
workflow_runs
intraday_scans
trigger_events
performance_metrics
strategy_tuning_records
reports
```

### 16.2 持仓页

```text
当前持仓
可卖数量
T+1 锁定数量
成本价
现价
浮盈亏
仓位占比
止损价
买入逻辑
最近一次持仓复核
```

主要表：

```text
positions
position_locks
position_ledger
decision_results
paper_trades
```

### 16.3 决策页

```text
某只股票的历史决策
策略版本
最终动作
信心
触发价
数据质量
关联报告
关联成交
```

主要表：

```text
strategy_snapshots
decision_results
risk_checks
paper_signals
paper_orders
paper_trades
reports
```

### 16.4 回放页

```text
回放列表
回放配置
收益率
最大回撤
交易次数
每日资产曲线
阻塞原因
错误样本
```

主要表：

```text
replay_runs
replay_daily_records
performance_metrics
paper_trades
strategy_tuning_records
```

### 16.5 策略迭代页

```text
策略版本
假设
规则改动
风险改动
仓位改动
自动识别问题
人工记录问题
下一步动作
结论
```

主要表：

```text
strategy_tuning_records
performance_metrics
replay_runs
reports
```

第一版可以先用 repository 查询实现，不急着做数据库视图。  
如果后续迁 PostgreSQL，再考虑把稳定查询固化为 view 或 materialized view。
