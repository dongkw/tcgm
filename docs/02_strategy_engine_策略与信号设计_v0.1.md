# 02 strategy_engine 策略与信号设计 v0.1

> 本文档是“天才交易员”的第二步细化设计。  
> 目标是把买入、卖出、持仓、仓位、止损和证伪规则设计成可执行、可追溯、可验证的策略引擎。  
> 本文档借鉴现有《股票投资决策引擎》和《股票投资分析自查手册》，但不机械照搬；不适合工程化、数据不可得或过度主观的部分会做降级或后移。  
> 本文档只做设计，不写代码。

---

## 1. 设计目标

策略引擎要解决四个问题：

1. 把用户想法和数据转成明确规则。
2. 把买卖判断从 AI 黑盒变成可追溯结果。
3. 把错误、缺失、未知和风险显式暴露。
4. 支持后续回放、模拟盘和策略迭代。

第一版支持三类策略任务：

```text
BUY_EVALUATION     单票买入评估
HOLDING_REVIEW     持仓卖出/持有评估
WATCHLIST_SCAN     自选股池扫描
```

第一版不支持：

- 高频交易策略。
- 机器学习预测股价。
- 自动实盘下单。
- ETF 和场外基金策略。
- 多因子全市场选股。
- 复杂行业轮动模型。

---

## 2. 核心原则

### 2.1 策略不直接读原始数据

策略引擎只允许读取：

```text
strategy_snapshot
```

不允许直接读取：

- 原始接口数据。
- 未标准化数据。
- 未通过质量检查的数据。
- 未完成时间生效判断的数据。

### 2.2 规则结果必须结构化

每条规则都必须输出结构化结果。

规则状态：

```text
PASS            通过
FAIL            失败
WARN            风险提示或降级
UNKNOWN         数据不足，无法判断
NOT_APPLICABLE  不适用
```

规则严重级别：

```text
BLOCKER     硬否决
MAJOR       重大风险，通常降级
MINOR       一般风险，提示或轻微降仓
INFO        信息展示
```

### 2.3 硬规则和软规则分离

硬规则用于拦截。

例如：

- ST。
- 退市风险。
- 立案调查。
- 扣非净利润连续为负。
- 关键财务数据缺失。
- 持仓无可卖数量。

软规则用于调整信心和仓位。

例如：

- 估值偏高。
- 筹码数据不完整。
- 北向数据滞后。
- 近 20 日涨幅偏大。
- ATR 过高。

### 2.4 卖出优先于买入

系统先保护已有本金，再寻找新机会。

每日评估顺序：

```text
持仓风险
  ↓
卖出 / 减仓触发
  ↓
现金和仓位
  ↓
新买入机会
```

如果持仓有清仓或减仓风险，新增买入建议必须更保守。

### 2.5 不预测股价

策略引擎不预测未来股价。

策略引擎只回答：

- 当前是否符合规则。
- 触发什么条件才买。
- 触发什么条件该卖。
- 仓位是否合理。
- 风险是否可接受。

### 2.6 AI 规则必须先待验证

AI 可以辅助生成候选规则，但不能直接进入正式策略。

规则成熟度：

```text
DRAFT       草案，只能讨论
PAPER_ONLY  只跑模拟盘和回放
ACTIVE      正式策略，可参与实盘建议
DISABLED    停用
```

AI 生成的规则默认是 `DRAFT`，最多进入 `PAPER_ONLY`，必须经过回放和模拟盘验证后才能变成 `ACTIVE`。

---

## 3. 输入与输出

### 3.1 输入：strategy_snapshot

策略引擎输入是 `strategy_snapshot`。

它至少包含：

```text
snapshot_id
symbol
name
asset_type
decision_time
trade_date
task_type
quote
technical
valuation
financial
events
ownership
position
cash
data_quality
effective_data_notes
missing_fields
```

第一版只处理：

```text
asset_type = A_STOCK
```

### 3.2 输出：decision_result

策略引擎输出是 `decision_result`。

必须包含：

```text
decision_id
snapshot_id
symbol
decision_time
task_type
final_action
confidence
rule_results
blocking_rules
warning_rules
position_plan
trigger_prices
invalidation_points
data_quality_summary
human_review_required
explanation_summary
```

### 3.3 行为输出枚举

买入评估：

```text
BUY             买入
WATCH_SMALL     观察仓 / 小仓试错
WAIT            观望
DO_NOT_BUY      不买入
DATA_BLOCKED    数据不足，暂停评估
```

持仓评估：

```text
HOLD            持有
REDUCE_HALF     减仓一半
REDUCE_TO_WATCH 减到观察仓
CLEAR           清仓
NO_SELL_T_PLUS  触发卖出但 T+1 不可卖
DATA_BLOCKED    数据不足，暂停完整评估
PRE_EVALUATION  缺持仓关键信息，只能预评估
```

自选股扫描：

```text
BUY_CANDIDATE       买入候选
WATCH_CANDIDATE     观察候选
RISK_CANDIDATE      风险候选
IGNORE              暂不关注
DATA_BLOCKED        数据不足
```

---

## 4. 策略执行管线

策略引擎按固定顺序执行：

```text
加载 strategy_snapshot
  ↓
基础数据检查
  ↓
任务类型分流
  ↓
规则组执行
  ↓
硬否决处理
  ↓
软规则降级
  ↓
仓位和资金约束
  ↓
生成触发价和证伪点
  ↓
生成 decision_result
```

所有规则执行结果必须保存到 `rule_results`。

---

## 5. 规则组设计

第一版规则组：

```text
R0_DATA_QUALITY       数据质量
R1_MARKET_RULES       交易规则
R2_HARD_BLOCKS        前置硬否决
R3_FUNDAMENTAL        基本面
R4_VALUATION          估值与性价比
R5_EVENT_RISK         事件风险
R6_TECHNICAL          技术面
R7_LOGIC              买入逻辑和证伪
R8_POSITION_CASH      仓位和资金
R9_HOLDING_SELL       持仓卖出
```

### 5.1 R0 数据质量规则

职责：

- 判断数据是否足够进入策略。
- 防止策略在缺字段时给强结论。

关键规则：

| 编号 | 规则 | 失败处理 |
|---|---|---|
| R0-1 | `strategy_snapshot` 存在且结构完整 | `DATA_BLOCKED` |
| R0-2 | `data_quality_score >= 60` | `DATA_BLOCKED` |
| R0-3 | 当前价、交易日、股票代码存在 | `DATA_BLOCKED` |
| R0-4 | 财务核心字段缺失 | 买入评估 `DATA_BLOCKED` |
| R0-5 | 持仓评估缺成本/仓位/买入逻辑 | `PRE_EVALUATION` |
| R0-6 | 数据晚于决策时间 | `DATA_BLOCKED` |

### 5.2 R1 交易规则

职责：

- 处理 A 股交易限制。

关键规则：

| 编号 | 规则 | 失败处理 |
|---|---|---|
| R1-1 | 当前是有效交易日或允许盘后复盘 | 非交易动作暂停 |
| R1-2 | 标的是 A 股股票 | 非 A 股暂不支持 |
| R1-3 | 买入数量必须满足 100 股整数倍 | 调整数量或不买 |
| R1-4 | 卖出数量不能超过可卖数量 | 调整数量或 `NO_SELL_T_PLUS` |
| R1-5 | 当日买入不可当日卖出 | `NO_SELL_T_PLUS` |
| R1-6 | 涨停可能买不到、跌停可能卖不出 | 执行计划加风险提示 |

### 5.3 R2 前置硬否决

借鉴原 A 闸门，但更工程化。

硬否决：

| 编号 | 规则 | 动作 |
|---|---|---|
| R2-1 | ST、退市风险、暂停上市风险 | `DO_NOT_BUY` |
| R2-2 | 被监管立案调查或重大财务造假风险 | `DO_NOT_BUY` |
| R2-3 | 最近两个会计年度扣非净利润均为负 | `DO_NOT_BUY`，困境反转策略除外 |
| R2-4 | 近 1 个月涨幅 > 80% 且无基本面验证 | `DO_NOT_BUY` |
| R2-5 | 大股东/实控人无合理解释大幅减持 | `DO_NOT_BUY` 或人工复核 |

追高降级：

| 编号 | 规则 | 动作 |
|---|---|---|
| R2-6 | 近 1 个月涨幅 50%-80% | 最高 `WATCH_SMALL` |
| R2-7 | 近 60 日涨幅 > 80% | 最高 `WATCH_SMALL` |

### 5.4 R3 基本面规则

基本面只做两件事：

- 排除明显差公司。
- 识别是否存在基本面证伪。

硬规则：

| 编号 | 规则 | 动作 |
|---|---|---|
| R3-1 | 年归母净利润过小且主营无稳定增长证据 | `DO_NOT_BUY` |
| R3-2 | 经营现金流/净利润连续 3 年 < 0.5 | `DO_NOT_BUY` |
| R3-3 | 扣非净利润连续恶化且无解释 | `DO_NOT_BUY` 或降级 |
| R3-4 | 最新季度收入、利润、现金流明显恶化 | 降级或风险提示 |
| R3-5 | 应收、存货、商誉异常但数据缺失 | 标注缺口，不强判 |

第一版优化：

- 应收、存货、商誉暂不作为硬规则，除非数据可靠。
- 毛利率、净利率、ROE 先作为质量提示，不单独触发买入。
- 基本面好不能替代买点和风控。

### 5.5 R4 估值与性价比

估值在第一版中定位为：

```text
风险过滤 + 性价比提示 + 仓位调整
```

不把主观未来 EPS 预测作为硬买入依据。

规则：

| 编号 | 规则 | 动作 |
|---|---|---|
| R4-1 | PE/PB 明显高于历史分位 | 降低信心和仓位 |
| R4-2 | 估值数据缺失 | 买入最高 `WAIT` 或 `WATCH_SMALL` |
| R4-3 | 周期股 PB 高位且利润高位 | `DO_NOT_BUY` |
| R4-4 | 高股息策略缺分红可持续性数据 | 不进入正式买入 |
| R4-5 | 未来 EPS 缺失 | 不预测，改为反算触发条件 |

输出要求：

- 如果估值不支持买入，必须给出等待条件。
- 如果估值只能粗略判断，必须标注“估值信心低”。

### 5.6 R5 事件风险

事件风险分三类：

```text
BLOCK       直接拦截
WARN        降级或人工复核
INFO        报告展示
```

BLOCK：

- 立案调查。
- 退市风险。
- 重大财务造假。
- 重大诉讼可能影响持续经营。

WARN：

- 减持计划。
- 异常波动公告。
- 解禁。
- 监管问询。
- 股权质押风险。

INFO：

- 回购。
- 股权激励。
- 分红。
- 一般经营公告。

第一版约束：

- 公告标题只能初筛。
- 重大事件必须提示原文复核。
- AI 摘要不能替代公告事实。

### 5.7 R6 技术面规则

第一版只保留简单可验证指标。

短线核心：

- 当前价是否站上 20 日线。
- 20 日线是否向上。
- 是否跌破近 20 日低点。
- 近 20 日涨幅是否过大。
- ATR 是否过高。

中线核心：

- 当前价是否站上 60 日线。
- 60 日线是否向上。
- 是否跌破 60 日线后收不回。

买点分类：

```text
PULLBACK    回踩买点
BREAKOUT    突破买点
REPAIR      修复买点
LEFT_TRY    左侧试错
NO_BUYPOINT 无买点
```

第一版暂不引入：

- 周线 MACD。
- 月线均线。
- KDJ。
- RSI。
- 布林带。
- 复杂形态识别。

### 5.8 R7 逻辑和证伪

借鉴三段式逻辑，但改成结构化。

必须包含：

```text
core_thesis           核心逻辑
validation_catalyst   验证催化剂
invalidation_points   证伪信号
```

证伪信号至少包含：

```text
fundamental_invalidation  基本面证伪
technical_invalidation    技术证伪
event_invalidation        事件证伪
```

规则：

| 编号 | 规则 | 动作 |
|---|---|---|
| R7-1 | 写不出核心逻辑 | 最高 `WAIT` |
| R7-2 | 写不出证伪点 | `DO_NOT_BUY` |
| R7-3 | 证伪点缺类别 | 最高 `WAIT` |
| R7-4 | 逻辑为 AI 新生成且未验证 | 只能 `WATCH_SMALL` 或模拟盘 |

### 5.9 R8 仓位和资金

仓位由规则计算，不由 AI 拍脑袋。

基础公式：

```text
single_trade_risk_amount = total_assets * risk_budget_pct
stop_loss_pct = abs(entry_price - stop_loss_price) / entry_price
risk_position_pct = risk_budget_pct / stop_loss_pct
final_position_pct = min(risk_position_pct, max_single_position_pct)
```

默认约束：

- 单票仓位原则上不超过 30%。
- 高波动、周期、困境反转自动下调。
- 左侧试错只允许计划仓位的 1/3。
- 标准首仓为计划仓位的 1/2。
- 可用现金不足时，买入降级。
- 现金低于保留比例时，暂停新买入。

### 5.10 R9 持仓卖出规则

持仓评估优先级：

```text
原买入逻辑是否失效
  ↓
原证伪点是否触发
  ↓
如果空仓是否还愿意当前价买入
  ↓
技术止损是否触发
  ↓
是否需要暴涨止盈
  ↓
仓位是否过高
```

卖出规则：

| 编号 | 规则 | 动作 |
|---|---|---|
| R9-1 | 原买入逻辑失效 | `REDUCE_HALF` 或 `CLEAR` |
| R9-2 | 原证伪点触发 | `REDUCE_HALF` 或 `CLEAR` |
| R9-3 | 当前价不愿重新买入 | 至少 `REDUCE_HALF` |
| R9-4 | 短线跌破 20 日线且收不回 | `REDUCE_HALF` |
| R9-5 | 跌破近 20 日低点 | `CLEAR` 或继续减仓 |
| R9-6 | 中线跌破 60 日线且收不回 | `REDUCE_HALF` |
| R9-7 | 1 个月涨幅 > 80% 且无基本面改善 | 主动止盈 1/3 |
| R9-8 | 卖出触发但无可卖数量 | `NO_SELL_T_PLUS` |

第一版要求：

- 卖出建议必须给具体价位。
- 缺成本、仓位、买入逻辑时只能 `PRE_EVALUATION`。
- 价格下跌不是自动补仓理由。

---

## 6. 买入评估决策流程

买入评估流程：

```text
R0 数据质量
  ↓
R1 交易规则
  ↓
R2 前置硬否决
  ↓
R3 基本面
  ↓
R4 估值
  ↓
R5 事件风险
  ↓
R7 逻辑和证伪
  ↓
R6 技术面
  ↓
R8 仓位和资金
  ↓
final_action
```

输出优先级：

1. 任一 `BLOCKER FAIL` → `DO_NOT_BUY` 或 `DATA_BLOCKED`。
2. 基本面无硬伤，但估值/逻辑/技术不足 → `WAIT`。
3. 基本面可接受，逻辑可验证，但技术未确认 → `WATCH_SMALL`。
4. 基本面、逻辑、估值、技术、资金均满足 → `BUY`。

第一版对 `BUY` 要保守：

- 用户未明确周期，最高 `WAIT`。
- 未来 EPS 缺失，最高 `WATCH_SMALL`。
- 事件原文未复核，最高 `WATCH_SMALL`。
- 近 20 日涨幅过大，最高 `WATCH_SMALL`。

---

## 7. 持仓评估决策流程

持仓评估流程：

```text
R0 数据质量
  ↓
R1 交易规则
  ↓
R9 持仓卖出
  ↓
R3 基本面证伪
  ↓
R5 事件风险
  ↓
R6 技术面
  ↓
R8 仓位和资金
  ↓
final_action
```

输出优先级：

1. 数据缺成本、仓位、买入逻辑 → `PRE_EVALUATION`。
2. 逻辑证伪或重大事件 → `CLEAR` 或 `REDUCE_HALF`。
3. 技术破位但逻辑未证伪 → `REDUCE_HALF`。
4. 趋势未破且逻辑仍在 → `HOLD`。
5. 暴涨兑现且不愿当前价重新买入 → `REDUCE_HALF` 或止盈 1/3。

---

## 8. 自选股扫描流程

自选股扫描不是直接买入，而是筛候选。

流程：

```text
每只股票生成 strategy_snapshot
  ↓
执行买入评估规则
  ↓
按 final_action 分类
  ↓
输出候选池
```

分类：

```text
BUY_CANDIDATE       A-F 大体通过，技术和资金待确认
WATCH_CANDIDATE     基本面无硬伤，有触发条件
RISK_CANDIDATE      有事件、破位、数据问题
IGNORE              无性价比或无逻辑
DATA_BLOCKED        数据不足
```

自选股扫描不直接生成实盘买入，只进入盘前计划。

---

## 9. 策略成熟度管理

每条策略规则必须有成熟度。

```text
DRAFT
PAPER_ONLY
ACTIVE
DISABLED
```

### 9.1 DRAFT

来源：

- 用户想法。
- AI 拆解。
- 复盘发现。

限制：

- 不能影响实盘建议。
- 只能进入研究清单。

### 9.2 PAPER_ONLY

条件：

- 有明确输入字段。
- 有明确规则。
- 有证伪点。
- 能生成回放结果。

限制：

- 只能影响模拟盘。

### 9.3 ACTIVE

条件：

- 通过固定样本回归。
- 模拟盘表现可接受。
- 无明显未来函数。
- 风险边界明确。

权限：

- 可以参与正式 `decision_result`。

### 9.4 DISABLED

触发：

- 回撤超过阈值。
- 规则失效。
- 数据源不可用。
- 复盘发现逻辑错误。

---

## 10. decision_result 结构要求

`decision_result` 必须能被报告、模拟盘、复盘和插件消费。

建议结构：

```text
decision_id
snapshot_id
symbol
name
task_type
final_action
confidence
action_reason
rule_results
blocking_rules
warning_rules
trigger_prices
position_plan
invalidation_points
data_quality_summary
execution_constraints
next_review_time
human_review_required
```

### 10.1 trigger_prices

至少包含：

```text
buy_trigger_price
add_trigger_price
reduce_trigger_price
clear_trigger_price
stop_loss_price
support_price
resistance_price
```

没有对应价位时写 `null`，并说明原因。

### 10.2 position_plan

至少包含：

```text
max_position_pct
initial_position_pct
suggested_cash_amount
suggested_quantity
risk_budget_pct
stop_loss_pct
cash_enough
position_downgrade_reasons
```

### 10.3 execution_constraints

至少包含：

```text
t_plus_one_blocked
available_quantity
lot_size_valid
price_limit_risk
human_review_required
```

---

## 11. AI 在策略引擎中的位置

AI 不参与硬判断。

AI 可以做：

- 把用户想法转成 `DRAFT` 规则。
- 解释规则命中原因。
- 提醒数据缺口。
- 生成反方观点。
- 复盘错误归因。
- 生成报告文本。

AI 不可以做：

- 直接修改 `final_action`。
- 覆盖硬否决。
- 补造缺失数据。
- 把 `DRAFT` 规则直接变成 `ACTIVE`。
- 在无回放验证时建议大仓位。

---

## 12. 第一版 MVP 规则集

第一版只实现最小可用规则。

必须实现：

- R0 数据质量。
- R1 A 股基础交易规则。
- R2 前置硬否决。
- R3 基本面硬伤。
- R5 重大事件风险。
- R6 20/60 日线和近 20 日高低点。
- R7 三段式逻辑和证伪。
- R8 基础仓位和现金约束。
- R9 持仓卖出。

暂不实现：

- 多因子打分。
- 复杂行业轮动。
- 周线/月线技术系统。
- AI 自动生成 ACTIVE 策略。
- 复杂估值预测模型。
- 分钟级交易信号。

---

## 13. 验收标准

02 完成后，后续开发应满足：

- 给定 `strategy_snapshot`，能输出 `decision_result`。
- 每条规则都有 `PASS/FAIL/WARN/UNKNOWN/NOT_APPLICABLE`。
- 任一硬否决能阻止买入。
- 数据缺失不会生成强买入。
- 持仓缺成本和买入逻辑时只输出预评估。
- 卖出规则能给具体价位。
- 仓位建议受现金和止损约束。
- AI 不能覆盖规则结果。
- 策略结果能用于模拟盘和报告。

---

## 14. 与 01 的衔接

01 负责生成：

```text
strategy_snapshot
```

02 只读取：

```text
strategy_snapshot
```

02 输出：

```text
decision_result
```

后续 03 需要保证：

- `decision_time` 正确。
- 财报和公告生效时间正确。
- T+1 可卖数量正确。
- 回放不使用未来数据。

