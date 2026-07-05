# data_catalog 有效数据目录设计 v0.1

> 本文档是“天才交易员”的第一步细化设计。  
> 目标是先定义哪些数据值得采集、为什么采集、如何处理、如何判断有效，以及缺失或异常时如何降级。  
> 本文档只做设计，不写代码。

---

## 1. 设计目标

有效数据目录要解决三个问题：

1. 不乱抓数据。
2. 不使用错误数据。
3. 不让 AI 或策略凭空补数据。

第一版只服务 A 股股票，优先支持：

- 单票买入评估。
- 持仓卖出评估。
- 自选股池扫描。
- 模拟盘验证。
- 盘前计划。
- 盘后复盘。

本设计的核心分层是：

```text
raw_data
  原始数据层：保存抓到的事实和证据
  ↓
normalized_data
  标准化数据层：统一代码、单位、日期、口径
  ↓
strategy_snapshot
  策略输入层：生成少量、稳定、可直接用于规则判断的半结构化数据
  ↓
decision_result
  决策结果层：保存策略输出、风控结果、执行价位和报告材料
```

核心约束：

```text
策略引擎不能直接读取 raw_data。
策略引擎只能读取通过质量检查和时间生效判断后的 strategy_snapshot。
```

暂不服务：

- 高频交易。
- 全市场复杂因子库。
- ETF 和场外基金策略。
- 社交媒体情绪分析。
- 新闻舆情自动交易。

---

## 2. 核心原则

### 2.1 字段必须有用途

每个字段进入系统前，必须说明它用于：

- 买入规则。
- 卖出规则。
- 风控规则。
- 仓位计算。
- 模拟盘。
- 复盘归因。
- 报告展示。

如果一个字段暂时无法说明用途，就不进入第一版。

### 2.2 数据不能越多越好

数据太多会带来：

- 抓取成本。
- 存储成本。
- 接口失败率。
- 噪音信号。
- AI 误读风险。
- 策略过拟合风险。

第一版只采集能直接服务规则和验证闭环的数据。

### 2.3 时间必须正确

每个数据都要区分：

- 数据对应的交易日。
- 数据产生时间。
- 数据披露时间。
- 系统抓取时间。
- 策略允许使用时间。

不能用未来数据做历史决策。

### 2.4 缺失必须明说

数据缺失时只能：

- 标注缺失。
- 降低信心。
- 降级结论。
- 暂停评估。

禁止：

- AI 推测缺失字段。
- 用历史值假装当前值。
- 用其他字段随意替代。
- 在报告里隐瞒缺口。

---

## 3. 数据分级

### 3.1 重要性分级

```text
REQUIRED   必需数据，缺失则不能给完整结论
IMPORTANT  重要数据，缺失则降低信心或仓位
OPTIONAL   辅助数据，只用于解释和提示
NOISE      暂无明确用途，不采集或不入库
```

### 3.2 可信度分级

```text
PRIMARY      官方公告、交易所、财报原文、券商账户数据
DERIVED      由可信基础数据计算出的指标
THIRD_PARTY  第三方接口数据，需要记录来源和时间
STALE        日期滞后，只能参考
UNKNOWN      来源或口径不清，不作为硬规则依据
```

### 3.3 使用权限分级

```text
HARD_RULE    可以参与硬规则判断
SOFT_RULE    只能参与降级、提示或信心调整
REPORT_ONLY  只用于报告展示
IGNORE       不使用
```

---

## 4. 数据生命周期

第一版数据流按下面顺序处理：

```text
数据源
  ↓
raw_data 原始数据层
  ↓
normalized_data 标准化数据层
  ↓
quality_checked 质量检查
  ↓
effective_data 生效时间判断
  ↓
derived_features 指标计算
  ↓
strategy_snapshot 策略输入层
  ↓
策略引擎 / 风控 / 报告
  ↓
decision_result 决策结果层
```

### 4.1 raw_data 原始数据层

保留原始接口返回的关键内容，方便排查。

要求：

- 记录来源。
- 记录抓取时间。
- 记录接口状态。
- 记录原始字段口径。
- 尽量保留原始值，不在这一层做主观判断。
- 可以有冗余字段，但必须可追溯。
- 不允许被策略引擎直接使用。

典型内容：

- 行情接口原始返回。
- K 线原始记录。
- 财报接口原始字段。
- 公告标题、链接和披露时间。
- 股东户数原始记录。
- 融资融券原始记录。
- 北向持仓原始记录。
- 账户或模拟盘原始成交记录。

### 4.2 normalized_data 标准化数据层

把不同来源字段统一成系统字段。

要求：

- 股票代码统一。
- 金额单位统一。
- 百分比单位统一。
- 日期格式统一。
- 缺失值统一。
- 股票代码和交易所统一。
- 交易日和自然日字段区分。
- 原始字段和标准字段建立映射。

这一层仍然不直接给策略使用，只负责清洗和统一口径。

### 4.3 quality_checked 质量检查层

判断数据是否可用。

检查内容：

- 是否缺字段。
- 是否过期。
- 是否异常。
- 是否和其他来源冲突。
- 是否晚于决策时间。
- 是否符合 A 股交易规则。
- 是否能进入下一层。

质量检查输出必须包含：

- 数据完整性。
- 数据新鲜度。
- 数据可信度。
- 数据一致性。
- 数据时间正确性。
- 缺失字段列表。
- 异常字段列表。
- 是否允许进入 `strategy_snapshot`。

### 4.4 effective_data 生效时间层

判断数据在某个决策时点是否已经允许使用。

要求：

- 财报必须按披露时间生效。
- 公告必须按披露时间生效。
- 收盘数据只能用于盘后和下一交易日。
- 盘中数据必须标记抓取时间。
- T+1 可卖数量必须按交易日历生效。

这一层用于防止未来函数。

### 4.5 derived_features 指标计算层

只计算第一版规则需要的指标。

例如：

- 20 日线。
- 60 日线。
- 近 20 日高低点。
- ATR。
- 20 日涨幅。
- 60 日涨幅。
- 经营现金流 / 净利润。
- 估值分位。

不做大量无用途技术指标堆砌。

### 4.6 strategy_snapshot 策略输入层

这是策略引擎唯一允许读取的数据层。

它不是原始数据，也不是完整数据库，而是面向某次决策生成的半结构化快照。

特点：

- 字段少。
- 结构稳定。
- 单位统一。
- 时间正确。
- 已通过质量检查。
- 已标注缺失字段。
- 已标注风险事件。
- 已标注数据质量评分。

典型结构：

```text
symbol
name
decision_time
trade_date
price
ma20
ma60
above_ma20
above_ma60
high_20d
low_20d
change_20d_pct
pe_ttm
pb
latest_financial_summary
financial_red_flags
announcement_risk_flags
position_summary
cash_summary
data_quality_score
missing_fields
effective_data_notes
```

策略引擎只根据 `strategy_snapshot` 做判断，不能回头读 raw 接口数据。

### 4.7 decision_result 决策结果层

这是策略和风控跑完之后的结果。

用途：

- 报告展示。
- 模拟盘执行。
- 持仓调整。
- 复盘归因。
- AI 解释。
- 插件输出。

典型结构：

```text
decision_id
symbol
decision_time
decision_type
suggested_action
confidence
rule_hits
rule_blocks
risk_blocks
trigger_prices
position_plan
data_quality_summary
execution_constraints
human_review_required
```

`decision_result` 必须引用生成它的 `strategy_snapshot`，方便回放和复盘。

---

## 5. 第一版有效字段目录

### 5.1 标的基础信息

| 字段 | 用途 | 级别 | 可信度 | 使用权限 | 缺失处理 |
|---|---|---|---|---|---|
| `symbol` | 股票唯一标识 | REQUIRED | PRIMARY/THIRD_PARTY | HARD_RULE | 无法评估 |
| `name` | 报告展示、确认标的 | REQUIRED | THIRD_PARTY | REPORT_ONLY | 标注缺失 |
| `exchange` | 交易规则、涨跌幅、交易日历 | REQUIRED | THIRD_PARTY | HARD_RULE | 无法评估 |
| `asset_type` | 区分股票/ETF/基金 | REQUIRED | SYSTEM | HARD_RULE | 无法评估 |
| `is_active` | 是否可交易 | IMPORTANT | THIRD_PARTY | HARD_RULE | 降级并提示 |
| `is_st` | ST 风险 | REQUIRED | THIRD_PARTY/PRIMARY | HARD_RULE | 买入评估暂停 |

第一版只允许 `asset_type = A_STOCK` 进入正式策略。

### 5.2 行情数据

| 字段 | 用途 | 级别 | 可信度 | 使用权限 | 缺失处理 |
|---|---|---|---|---|---|
| `trade_date` | 判断行情日期 | REQUIRED | THIRD_PARTY | HARD_RULE | 无法评估 |
| `price` | 当前价、仓位、止损 | REQUIRED | THIRD_PARTY | HARD_RULE | 无法评估 |
| `open` | 当日走势参考 | OPTIONAL | THIRD_PARTY | REPORT_ONLY | 不影响结论 |
| `high` | 当日波动、触发价 | IMPORTANT | THIRD_PARTY | SOFT_RULE | 降低信心 |
| `low` | 当日波动、止损触发 | IMPORTANT | THIRD_PARTY | SOFT_RULE | 降低信心 |
| `previous_close` | 当日涨跌幅校验 | IMPORTANT | THIRD_PARTY | SOFT_RULE | 降低信心 |
| `pct_change` | 大跌日、大涨日判断 | REQUIRED | DERIVED/THIRD_PARTY | HARD_RULE | 无法完整评估 |
| `volume` | 放量/缩量参考 | IMPORTANT | THIRD_PARTY | SOFT_RULE | 标注缺失 |
| `amount_yuan` | 流动性、成交活跃度 | IMPORTANT | THIRD_PARTY | SOFT_RULE | 标注缺失 |
| `market_cap_yuan` | 规模、仓位风险 | IMPORTANT | THIRD_PARTY | SOFT_RULE | 标注缺失 |
| `trading_status` | 停牌、正常交易 | REQUIRED | THIRD_PARTY | HARD_RULE | 买入评估暂停 |

行情数据必须和交易日历匹配。非交易日不能生成盘中交易信号。

### 5.3 K 线和技术数据

| 字段 | 用途 | 级别 | 可信度 | 使用权限 | 缺失处理 |
|---|---|---|---|---|---|
| `daily_bars` | 技术指标基础 | REQUIRED | THIRD_PARTY | HARD_RULE | 无法计算技术面 |
| `adjust_type` | 复权口径 | REQUIRED | SYSTEM/THIRD_PARTY | HARD_RULE | 技术结论降级 |
| `ma20` | 短线趋势线 | REQUIRED | DERIVED | HARD_RULE | 短线卖出/买入无法完整评估 |
| `ma60` | 中线趋势线 | REQUIRED | DERIVED | HARD_RULE | 中线卖出/买入无法完整评估 |
| `ma20_slope_up` | 短线趋势方向 | IMPORTANT | DERIVED | HARD_RULE | 降级 |
| `ma60_slope_up` | 中线趋势方向 | IMPORTANT | DERIVED | HARD_RULE | 降级 |
| `above_ma20` | 短线趋势状态 | REQUIRED | DERIVED | HARD_RULE | 降级 |
| `above_ma60` | 中线趋势状态 | REQUIRED | DERIVED | HARD_RULE | 降级 |
| `high_20d` | 压力位/突破/止盈参考 | REQUIRED | DERIVED | HARD_RULE | 卖出执行价缺失 |
| `low_20d` | 前低/清仓线 | REQUIRED | DERIVED | HARD_RULE | 卖出执行价缺失 |
| `change_20d_pct` | 追高风险 | REQUIRED | DERIVED | HARD_RULE | 买入评估降级 |
| `change_60d_pct` | 趋势强度 | IMPORTANT | DERIVED | SOFT_RULE | 标注缺失 |
| `atr14_pct` | 波动和止损距离 | IMPORTANT | DERIVED | SOFT_RULE | 仓位降级 |

第一版不采集大量复杂技术指标，例如 MACD、KDJ、RSI、布林带，除非后续策略明确需要。

### 5.4 估值数据

| 字段 | 用途 | 级别 | 可信度 | 使用权限 | 缺失处理 |
|---|---|---|---|---|---|
| `pe_ttm` | 非周期股估值 | IMPORTANT | THIRD_PARTY/DERIVED | SOFT_RULE | 买入结论降级 |
| `pb` | 周期股、资产型公司估值 | IMPORTANT | THIRD_PARTY/DERIVED | SOFT_RULE | 买入结论降级 |
| `dividend_yield_pct` | 高股息判断 | OPTIONAL | THIRD_PARTY | SOFT_RULE | 不影响普通策略 |
| `pe_5y_median` | 估值比较 | IMPORTANT | DERIVED | SOFT_RULE | 估值结论降级 |
| `pe_5y_percentile` | 估值分位 | IMPORTANT | DERIVED | SOFT_RULE | 估值结论降级 |
| `pb_5y_percentile` | 周期估值位置 | IMPORTANT | DERIVED | SOFT_RULE | 周期股降级 |
| `future_eps` | 未来回报测算 | OPTIONAL | UNKNOWN/THIRD_PARTY | REPORT_ONLY | 不自动填充 |

估值数据不作为单独买入理由。估值便宜但基本面或趋势不符合规则，仍不能直接买入。

### 5.5 财务数据

| 字段 | 用途 | 级别 | 可信度 | 使用权限 | 缺失处理 |
|---|---|---|---|---|---|
| `report` | 报告期标识 | REQUIRED | PRIMARY/THIRD_PARTY | HARD_RULE | 无法使用财务数据 |
| `notice_date` | 财报生效时间 | REQUIRED | PRIMARY/THIRD_PARTY | HARD_RULE | 财务数据不得用于硬规则 |
| `revenue_yuan` | 收入规模 | REQUIRED | THIRD_PARTY/PRIMARY | HARD_RULE | 基本面无法完整评估 |
| `revenue_yoy_pct` | 增长判断 | IMPORTANT | DERIVED/THIRD_PARTY | SOFT_RULE | 降低信心 |
| `parent_net_profit_yuan` | 盈利能力 | REQUIRED | THIRD_PARTY/PRIMARY | HARD_RULE | 基本面无法完整评估 |
| `parent_np_yoy_pct` | 利润趋势 | IMPORTANT | DERIVED/THIRD_PARTY | SOFT_RULE | 降低信心 |
| `deducted_net_profit_yuan` | 扣非盈利质量 | REQUIRED | THIRD_PARTY/PRIMARY | HARD_RULE | 买入评估暂停 |
| `deducted_np_yoy_pct` | 扣非趋势 | IMPORTANT | DERIVED/THIRD_PARTY | SOFT_RULE | 降低信心 |
| `operating_cashflow_yuan` | 现金流质量 | REQUIRED | THIRD_PARTY/PRIMARY | HARD_RULE | 买入评估暂停 |
| `ocf_to_net_profit` | 现金流/利润 | REQUIRED | DERIVED | HARD_RULE | 买入评估暂停 |
| `eps` | 估值换算 | IMPORTANT | THIRD_PARTY | SOFT_RULE | 估值降级 |
| `roe_pct` | 盈利质量 | IMPORTANT | THIRD_PARTY | SOFT_RULE | 降低信心 |
| `gross_margin_pct` | 商业质量 | OPTIONAL | THIRD_PARTY | REPORT_ONLY | 不影响硬结论 |
| `net_margin_pct` | 盈利质量 | OPTIONAL | THIRD_PARTY | REPORT_ONLY | 不影响硬结论 |
| `debt_asset_ratio_pct` | 财务风险 | IMPORTANT | THIRD_PARTY | SOFT_RULE | 风险提示 |

财务数据必须按 `notice_date` 或公告披露时间生效，不能按报告期提前使用。

### 5.6 公告和风险事件

| 字段 | 用途 | 级别 | 可信度 | 使用权限 | 缺失处理 |
|---|---|---|---|---|---|
| `announcement_title` | 风险事件识别 | IMPORTANT | PRIMARY/THIRD_PARTY | SOFT_RULE | 标注未核验 |
| `announcement_time` | 事件生效时间 | REQUIRED | PRIMARY/THIRD_PARTY | HARD_RULE | 事件不得用于硬规则 |
| `announcement_url` | 人工复核 | IMPORTANT | PRIMARY/THIRD_PARTY | REPORT_ONLY | 标注缺失 |
| `reduction_plan` | 减持风险 | IMPORTANT | PRIMARY/THIRD_PARTY | SOFT_RULE | 标注未核验 |
| `regulatory_inquiry` | 监管风险 | IMPORTANT | PRIMARY/THIRD_PARTY | SOFT_RULE | 标注未核验 |
| `investigation_case` | 立案风险 | REQUIRED | PRIMARY/THIRD_PARTY | HARD_RULE | 买入评估暂停 |
| `delisting_risk` | 退市风险 | REQUIRED | PRIMARY/THIRD_PARTY | HARD_RULE | 不买入 |
| `abnormal_volatility` | 异动风险 | IMPORTANT | PRIMARY/THIRD_PARTY | SOFT_RULE | 风险提示 |
| `unlock_schedule` | 解禁风险 | IMPORTANT | THIRD_PARTY | SOFT_RULE | 风险提示 |

公告标题只能做初筛。涉及减持、监管、立案、退市、重大诉讼时，必须提示“公告原文待复核”。

### 5.7 筹码和资金面数据

| 字段 | 用途 | 级别 | 可信度 | 使用权限 | 缺失处理 |
|---|---|---|---|---|---|
| `shareholder_count` | 筹码分散/集中参考 | OPTIONAL | THIRD_PARTY/STALE | REPORT_ONLY | 不影响硬结论 |
| `holder_num_change_pct` | 股东户数变化 | OPTIONAL | THIRD_PARTY/STALE | REPORT_ONLY | 不影响硬结论 |
| `margin_balance_yuan` | 杠杆资金参考 | OPTIONAL | THIRD_PARTY | SOFT_RULE | 不影响硬结论 |
| `financing_net_buy_5d_yuan` | 短期融资变化 | OPTIONAL | THIRD_PARTY | REPORT_ONLY | 不影响硬结论 |
| `northbound_holding` | 外资持仓参考 | OPTIONAL | STALE/THIRD_PARTY | REPORT_ONLY | 不影响硬结论 |

第一版筹码数据只做辅助说明，不作为买入硬条件。

### 5.8 持仓和资金数据

| 字段 | 用途 | 级别 | 可信度 | 使用权限 | 缺失处理 |
|---|---|---|---|---|---|
| `account_id` | 区分模拟盘/实盘 | REQUIRED | SYSTEM | HARD_RULE | 无法执行 |
| `account_type` | PAPER/REAL | REQUIRED | SYSTEM | HARD_RULE | 无法执行 |
| `available_cash` | 买入资金约束 | REQUIRED | PRIMARY/SYSTEM | HARD_RULE | 不允许买入 |
| `total_assets` | 仓位计算 | REQUIRED | PRIMARY/SYSTEM | HARD_RULE | 不允许给仓位 |
| `cash_reserve_pct` | 现金保留规则 | IMPORTANT | SYSTEM | HARD_RULE | 使用默认保守值 |
| `symbol` | 持仓标识 | REQUIRED | SYSTEM | HARD_RULE | 无法评估持仓 |
| `total_quantity` | 总持仓 | REQUIRED | PRIMARY/SYSTEM | HARD_RULE | 无法评估持仓 |
| `available_quantity` | T+1 可卖数量 | REQUIRED | PRIMARY/SYSTEM | HARD_RULE | 不允许生成卖出执行 |
| `locked_quantity` | 当日买入锁定 | REQUIRED | SYSTEM | HARD_RULE | 不允许生成卖出执行 |
| `avg_cost` | 成本和盈亏 | REQUIRED | PRIMARY/SYSTEM | HARD_RULE | 卖出只能预评估 |
| `buy_logic` | 原买入逻辑 | REQUIRED | USER/SYSTEM | HARD_RULE | 卖出只能预评估 |
| `invalidation_point` | 原证伪点 | REQUIRED | USER/SYSTEM | HARD_RULE | 卖出只能预评估 |

持仓数据来自用户、模拟盘或未来券商接口，比第三方行情更重要。卖出评估缺成本、仓位、原买入逻辑时，只能输出预评估。

---

## 6. 数据质量评分

每次生成决策输入前，必须计算数据质量。

### 6.1 评分维度

```text
completeness   完整性
freshness      新鲜度
trust          可信度
consistency    一致性
timeliness     时间正确性
```

### 6.2 建议评分

```text
90-100  数据完整，可正常评估
75-89   数据基本可用，结论可给但需提示缺口
60-74   数据有明显缺口，只能降级
<60     数据不足，暂停完整评估
```

### 6.3 强制降级条件

出现以下情况，不看总分，直接降级或暂停：

- 当前价缺失。
- 交易日不匹配。
- 财报没有公告时间。
- 最新财务核心字段缺失。
- K 线不足以计算 20 日线或 60 日线。
- 持仓卖出缺成本价或可卖数量。
- 买入缺可用资金数据。
- 关键数据晚于决策时间。
- 数据来源冲突且无法判定优先级。

---

## 7. 时间生效规则

### 7.1 行情数据

收盘后数据只能用于盘后复盘和下一交易日盘前计划。

盘中使用实时行情时，必须标注 `observed_at`，不能把盘中价格当成收盘价。

### 7.2 财务数据

财务数据按披露时间生效。

规则：

- 财报公告前，历史回放不能使用该财报。
- 盘后披露的财报，不能影响当天盘中决策。
- 缺少披露时间时，只能按披露日期的下一交易日保守生效。

### 7.3 公告数据

公告按披露时间生效。

规则：

- 盘中披露的重要公告，可以进入盘中风险提示。
- 盘后公告，只能进入盘后复盘和下一交易日盘前计划。
- 只有标题、没有原文链接时，不能作为强硬结论。

### 7.4 持仓数据

持仓以账户快照时间为准。

规则：

- 当日买入锁定数量，下一交易日才变为可卖数量。
- T+1 按交易日历，不按自然日。
- 模拟盘和实盘账户时间必须分开记录。

---

## 8. 数据源优先级

第一版数据源优先级：

```text
官方公告 / 财报原文
  >
交易所 / 券商账户
  >
可信第三方接口
  >
系统计算指标
  >
AI 摘要
```

AI 摘要不能作为原始数据源，只能作为解释材料。

当多个数据源冲突时：

1. 优先高可信来源。
2. 记录冲突。
3. 标注风险。
4. 必要时暂停评估。

---

## 9. 第一版不采集的数据

以下数据第一版不采集，除非后续策略明确需要：

- 社交平台情绪。
- 股吧评论。
- 短视频热度。
- 分钟级和 tick 级历史数据。
- 大量技术指标组合。
- 未核验新闻。
- 研报全文自动打分。
- 主力资金净流入等口径不清指标。
- 题材热度榜。
- 龙虎榜自动交易信号。

这些数据不是永远不用，而是第一版不让它们干扰核心闭环。

---

## 10. 决策降级规则

### 10.1 买入评估

买入评估至少需要：

- 当前价。
- 交易状态。
- 20/60 日技术数据。
- 基础财务数据。
- 扣非净利润。
- 经营现金流。
- 基础估值。
- 风险公告初筛。
- 可用资金。

缺少核心行情、技术或财务数据：

```text
不输出买入，只输出数据不足
```

缺少估值分位、筹码数据、公告原文复核：

```text
结论降级，最高观察或观望
```

### 10.2 持仓卖出评估

卖出评估至少需要：

- 当前价。
- 20 日线。
- 60 日线。
- 近 20 日高低点。
- 持仓成本。
- 可卖数量。
- 当前仓位。
- 原买入逻辑。
- 原证伪点。

缺成本、仓位、买入逻辑、证伪点时：

```text
只能输出预评估
```

缺可卖数量时：

```text
不能输出可执行卖出数量
```

### 10.3 模拟盘

模拟盘至少需要：

- 账户现金。
- 持仓数量。
- 可卖数量。
- 交易日历。
- 成交价格。
- 交易成本。

缺交易日历或 T+1 状态时：

```text
暂停模拟成交
```

---

## 11. data_catalog 表设计口径

后续数据库中至少需要体现四层数据关系。

### 11.1 字段目录表

`data_catalog` 至少包含：

```text
field_name              字段名
field_group             字段分组
description             字段说明
required_level          REQUIRED / IMPORTANT / OPTIONAL / NOISE
trust_level             PRIMARY / DERIVED / THIRD_PARTY / STALE / UNKNOWN
usage_permission        HARD_RULE / SOFT_RULE / REPORT_ONLY / IGNORE
used_by_modules         使用模块
used_by_rules           使用规则
source_name             数据源
refresh_policy          刷新策略
effective_time_rule     生效时间规则
missing_behavior        缺失处理
anomaly_checks          异常检查
conflict_policy         冲突处理
first_version_enabled   是否进入第一版
notes                   备注
```

### 11.2 原始数据表

原始数据表可以按来源拆分，也可以先用统一表承载。

建议命名：

```text
raw_market_quotes
raw_daily_bars
raw_financial_reports
raw_announcements
raw_ownership
raw_margin_trading
raw_account_events
```

原始数据表必须包含：

```text
raw_id
source_name
source_payload
source_time
observed_at
fetch_status
error_message
```

原始数据只做证据保存，不允许策略直接读取。

### 11.3 标准化数据表

建议命名：

```text
normalized_quotes
normalized_daily_bars
normalized_financial_reports
normalized_announcements
normalized_positions
```

标准化数据表必须包含：

```text
normalized_id
raw_id
symbol
trade_date
standard_fields
normalized_at
normalization_status
```

标准化数据仍不能直接给策略使用。

### 11.4 策略快照表

建议命名：

```text
strategy_snapshots
strategy_snapshot_items
```

策略快照表必须包含：

```text
snapshot_id
symbol
decision_time
trade_date
snapshot_type
data_quality_score
missing_fields
effective_data_notes
snapshot_payload
created_at
```

策略引擎只能读取 `strategy_snapshots`。

### 11.5 决策结果表

建议命名：

```text
decision_results
decision_rule_hits
decision_execution_plans
```

决策结果表必须包含：

```text
decision_id
snapshot_id
symbol
decision_time
decision_type
suggested_action
confidence
rule_hits
rule_blocks
risk_blocks
execution_plan
created_at
```

决策结果必须引用 `snapshot_id`。

---

## 12. 数据质量检查清单

每次生成决策前，系统必须检查：

- 股票代码是否有效。
- 是否交易日。
- 行情日期是否正确。
- 当前价是否缺失或异常。
- K 线长度是否足够。
- 均线是否能计算。
- 财报是否已经公告。
- 公告是否晚于决策时间。
- 是否存在 ST、退市、立案风险。
- 是否有关键字段缺失。
- 是否有多个来源冲突。
- 是否使用了滞后数据做硬判断。
- 是否使用了未来数据。
- 持仓数量和可卖数量是否一致。
- 账户资金是否足够。
- 策略引擎是否只读取 `strategy_snapshot`。
- 决策结果是否引用了对应快照。

---

## 13. 第一版验收标准

这份数据目录落地后，应满足：

- 每个第一版字段都有明确用途。
- 每个第一版字段都有缺失处理规则。
- 每个第一版字段都有刷新频率。
- 每个第一版字段都有生效时间规则。
- 每个第一版字段都能标记可信度。
- 策略引擎不能直接读取未通过质量检查的数据。
- 策略引擎不能直接读取原始接口数据。
- 每次策略判断都必须生成 `strategy_snapshot`。
- 每个 `decision_result` 都必须能追溯到 `strategy_snapshot`。
- AI 不能生成或补齐原始数据。
- 无明确用途的数据不会进入第一版采集。
- 任何决策报告都能列出数据缺口。

---

## 14. 后续衔接

本文件确认后，后续应进入第二步：

```text
02_strategy_engine_策略与信号设计_v0.1.md
```

第二步要解决：

- 买入规则如何结构化。
- 卖出规则如何结构化。
- 仓位如何计算。
- 止损如何定义。
- 证伪点如何进入系统。
- AI 如何把用户想法转成候选规则。
- 候选规则如何进入回测和模拟盘。
