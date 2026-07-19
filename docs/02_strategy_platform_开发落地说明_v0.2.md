# 02 strategy_platform 开发落地说明 v0.2

> 本文档把《02 strategy_platform 多策略与评分设计 v0.2》拆成可以直接执行的开发步骤。
> 本轮目标是先完成多策略平台骨架、三条样例策略、透明汇总、数据库记录和技术报告，再逐条扩展真实业务策略。
> 不要求保留 v0.1 的输出兼容；但重构期间必须先完成新链路验证，再一次性切换调用入口，避免系统长期处于双轨状态。

---

## 1. 当前代码基线

当前策略链路主要是：

```text
stock JSON
  -> snapshot_builder.py
  -> strategy_engine.py
  -> decision_result
  -> build_decision.py / workflow.py / web/commands.py
  -> risk_control.py / paper_trading.py / report
```

当前关键事实：

- `strategy_engine.py` 同时处理数据闸门、买入和持仓判断。
- `build_decision.py` 同时负责输入、策略调用、文件写入、数据库同步和报告生成。
- `workflow.py`、`replay_snapshot_builder.py` 和 `web/commands.py` 直接调用旧引擎。
- `paper_trading.py` 将部分决策动作直接映射为买卖信号。
- SQLite 已有 `strategy_snapshots`、`decision_results`、`risk_checks` 和 `ai_calls`。
- 当前项目没有自己的自动化测试目录。

本轮首先解决策略边界和测试基础，不同时重构数据采集、工作流页面和模拟成交细节。

---

## 2. 本轮交付范围

### 2.1 必须交付

1. 策略平台目录和统一协议。
2. 标准分析快照和特征读取接口。
3. 策略注册、启停、依赖检查和运行器。
4. 每条策略独立的评分器和 JSON 配置。
5. 三条样例策略：趋势买入、技术退出、AI 研究占位策略。
6. 不生成伪综合分的透明汇总器。
7. 买入和持仓两条独立管线。
8. 新的数据库迁移、Repository 和校验规则。
9. 中文多策略技术报告。
10. CLI、历史回放和 Web 决策入口切换。
11. 固定样本自动化测试。
12. README 使用说明。

### 2.2 本轮不做

- 不一次实现完整价值、成长、周期和事件策略。
- 不接自动实盘。
- 不做自动学习权重。
- 不做复杂插件热加载。
- 不做跨进程并行策略执行。
- 不把所有规则明细立即拆成独立数据库行。
- 不实现真正的 AI 模型调用；先完成可替换的接口和确定性测试替身。

---

## 3. 关键技术决策

### 3.1 使用标准库模型

第一版使用：

```text
dataclasses
Enum
typing.Protocol
json
unittest
```

理由：

- 符合项目当前风格。
- 不为协议模型新增运行依赖。
- 足够支持确定性策略和序列化。

所有外部输入仍必须经过显式验证函数，不能因为使用 `dataclass` 就跳过类型和枚举检查。

### 3.2 策略显式注册

第一版使用显式注册表，不扫描目录、不使用 Python entry point。

新增策略需要：

1. 新增独立策略目录。
2. 新增元数据和评分配置。
3. 在内置策略注册函数中注册一次。

这不算修改总引擎分支。运行器不需要知道新策略的内部逻辑。

### 3.3 评分配置使用 JSON

第一版使用 `scoring.json`，暂不引入 YAML 解析依赖。

### 3.4 同进程顺序执行

第一版策略数量少，按注册顺序执行并记录耗时。后续只有在实际耗时证明需要时，才增加线程、进程或任务队列。

### 3.5 先保存 JSON 明细

策略规则、证据和反证先保存在 `payload_json`。数据库只结构化高频查询字段，避免在业务尚未稳定时过早拆出大量表。

---

## 4. 目标目录

```text
src/ai_trader/
  analysis_snapshot.py
  feature_store.py
  strategy_platform/
    __init__.py
    contracts.py
    validation.py
    registry.py
    scoring.py
    runner.py
    aggregation.py
    pipeline.py
    report.py
    repositories.py
  strategies/
    __init__.py
    trend_following/
      __init__.py
      strategy.py
      metadata.json
      scoring.json
    technical_exit/
      __init__.py
      strategy.py
      metadata.json
      scoring.json
    ai_research/
      __init__.py
      strategy.py
      adapter.py
      metadata.json
      scoring.json
tests/
  strategy_platform/
    fixtures/
    test_contracts.py
    test_registry.py
    test_scoring.py
    test_runner.py
    test_aggregation.py
    test_pipeline.py
    test_ai_adapter.py
    test_reproducibility.py
```

现有 `snapshot_builder.py` 和 `strategy_engine.py` 在新入口完成切换后删除或停止被业务代码引用，不保留长期双轨。

---

## 5. 核心协议

### 5.1 枚举

建议在 `contracts.py` 中集中定义：

```text
TaskType:
  BUY
  HOLDING

MarketPhase:
  POST_MARKET
  PRE_MARKET
  INTRADAY
  HISTORICAL_REPLAY
  PAPER_TRADING

ImplementationType:
  RULE_BASED
  AI_BASED
  HYBRID

Maturity:
  DRAFT
  PAPER_ONLY
  ACTIVE
  DISABLED

CalibrationStatus:
  UNCALIBRATED
  CALIBRATING
  CALIBRATED

DataStatus:
  GOOD
  USABLE
  WEAK
  BLOCKED

BuySignal:
  STRONG_SUPPORT
  SUPPORT
  NEUTRAL
  OPPOSE
  STRONG_OPPOSE
  UNKNOWN

HoldingSignal:
  HOLD_SUPPORT
  REDUCE_SUPPORT
  EXIT_SUPPORT
  UNKNOWN
```

禁止在其他模块重复维护字符串集合。

### 5.2 AnalysisSnapshot

新快照替代旧 `strategy_snapshot` 的含义，至少包含：

```text
snapshot_id
schema_version
symbol
name
asset_type
task_type
market_phase
trade_date
decision_time
source_cutoff_time
facts
features
position
account
data_quality
source_refs
feature_set_version
```

要求：

- 快照生成后不可被策略修改。
- 策略只能读取 `facts`、`features` 和明确上下文。
- 快照必须能序列化为稳定 JSON。
- 快照 ID 必须关联股票、任务、决策时间和内容版本。

第一版可以继续从现有 stock JSON 构建快照，但字段映射集中放在 `analysis_snapshot.py`，不能散落到各策略。

### 5.3 StrategyMetadata

```text
strategy_id
name
strategy_family
strategy_version
parameter_version
task_type
implementation_type
maturity
calibration_status
supported_asset_types
supported_market_phases
required_features
optional_features
enabled
```

### 5.4 RuleResult

```text
rule_id
status
severity
message
score_delta
evidence
```

状态使用：

```text
PASS
FAIL
WARN
UNKNOWN
NOT_APPLICABLE
```

### 5.5 StrategyEvidence

策略逻辑先输出未评分的证据对象：

```text
applicable
applicability_reason
data_status
rule_results
supporting_evidence
opposing_evidence
risks
trigger_conditions
invalidation_conditions
used_features
hard_override_signal
```

`StrategyEvidence` 不包含最终分数。评分器只根据该对象和当前策略的 `scoring.json` 生成评分，防止策略逻辑自行维护另一套隐含权重。

### 5.6 StrategyEvaluation

```text
evaluation_id
run_id
snapshot_id
strategy metadata snapshot
applicable
applicability_reason
data_status
raw_score
calibrated_score
signal
confidence
rule_results
supporting_evidence
opposing_evidence
risks
trigger_conditions
invalidation_conditions
used_features
started_at
finished_at
duration_ms
error
```

### 5.7 StrategyAggregation

```text
aggregation_id
run_id
snapshot_id
task_type
conclusion
effective_strategy_count
support_count
oppose_count
neutral_count
unknown_count
family_summary
conflicts
hard_data_blocks
evaluations
aggregator_version
```

第一版没有 `overall_score`。

### 5.8 Strategy 接口

```python
class Strategy(Protocol):
    def metadata(self) -> StrategyMetadata: ...
    def applicable(self, snapshot: AnalysisSnapshot) -> Applicability: ...
    def evaluate(self, snapshot: AnalysisSnapshot) -> StrategyEvidence: ...
```

策略返回证据和规则结果，统一评分器根据 `scoring.json` 生成最终 `StrategyEvaluation`。

---

## 6. 数据和特征读取

### 6.1 第一版不重建完整数据仓库

当前数据结构仍可用于第一阶段。`analysis_snapshot.py` 负责把现有数据映射为统一快照：

```text
quote -> facts.quote
technical -> features.technical
valuation -> features.valuation
financial -> facts.financial + features.financial
announcements -> facts.events
user/account context -> position/account
time_context -> snapshot time fields
```

### 6.2 FeatureStore 接口

第一版 `FeatureStore` 只负责：

- 按路径读取公共特征。
- 检查必需特征是否存在。
- 返回特征值和来源元数据。
- 生成 `used_features` 记录。

不在本轮重做全部指标计算。后续新增特征计算器时仍通过该接口进入策略。

### 6.3 缺失处理

- 缺少必需特征：该策略 `data_status=BLOCKED`、`signal=UNKNOWN`。
- 缺少可选特征：策略可以继续，但必须降低信心或产生 `WARN`。
- 不适用：`applicable=false`，不参与汇总。
- 任何策略不得自行填造默认财务或行情值。

---

## 7. 注册表与运行器

### 7.1 Registry

注册表负责：

- 保证 `strategy_id + strategy_version` 唯一。
- 按任务、市场阶段、成熟度和启用状态筛选策略。
- 拒绝元数据不完整的策略。
- 提供稳定的运行顺序。

同一策略运行失败不得阻断其他策略。运行器必须把错误保存成该策略的失败结果。

### 7.2 Runner

运行顺序：

```text
验证快照
  -> 筛选注册策略
  -> 检查适用性
  -> 检查必需特征
  -> 执行策略规则
  -> 执行独立评分
  -> 验证输出
  -> 保存单策略结果
```

运行器不得：

- 根据策略 ID 写业务分支。
- 修改策略分数。
- 把策略异常转成支持信号。
- 隐藏策略错误。

### 7.3 运行状态

```text
PENDING
RUNNING
COMPLETED
PARTIAL
FAILED
```

某条策略失败但其他策略完成时，整次运行应为 `PARTIAL`，报告必须展示失败项。

---

## 8. 独立评分器

### 8.1 输入输出

输入：

- 策略规则结果。
- `scoring.json`。
- 策略元数据。

输出：

- `raw_score`。
- 评分明细。
- 由阈值映射得到的策略信号。

### 8.2 第一版评分规则

建议统一约束：

- 原始分范围 `0..100`。
- 初始基准分由策略配置决定。
- 每条规则根据状态增加或减少分数。
- 硬否决可以覆盖信号，但不能删除原始评分明细。
- `UNKNOWN` 不默认按失败扣满分，由策略配置明确处理。
- 最终分数裁剪到 `0..100`。

### 8.3 配置校验

启动或注册时检查：

- 所有规则 ID 唯一。
- 阈值连续且无重叠。
- 输出信号属于该任务允许的枚举。
- 参数版本与元数据一致。
- 配置内容计算 SHA-256，保存为 `scoring_config_hash`。

---

## 9. 第一批样例策略

### 9.1 `trend-following`

类型：`RULE_BASED`、买入策略。

用途：验证技术趋势类独立策略，不证明公司基本面优秀。

第一版可使用：

- 价格是否高于 MA20。
- 价格是否高于 MA60。
- MA20 是否向上。
- MA60 是否向上。
- 20 日涨幅是否过高。
- ATR 是否过高。

输出必须明确：

- 趋势支持或反对。
- 关键均线和突破条件。
- 技术证伪价。
- 数据缺口。

不能单独产生正式买入裁决。

### 9.2 `technical-exit`

类型：`RULE_BASED`、持仓策略。

用途：迁移现有 MA20、MA60、20 日低点和短期暴涨规则，但纠正当前优先级和证据表达。

输入至少包括：

- 当前价、成本价。
- MA20、MA60、20 日低点。
- 可卖数量。
- 原技术证伪点（如有）。

注意：

- T+1 不在本策略内部决定，交给执行闸门。
- 缺少原买入逻辑时只影响业务退出策略，不应阻断纯技术退出策略。
- `EXIT_SUPPORT` 是研究信号，不等同已经清仓。

### 9.3 `ai-research`

类型：`AI_BASED`、`DRAFT`。

用途：验证 AI 策略协议，不作为正式交易依据。

第一轮只实现：

- `AIAdapter` 接口。
- 固定 JSON 输入输出协议。
- 测试用 `FakeAIAdapter`。
- AI 未配置时输出明确的不可用状态。
- 非法枚举、缺证据、引用不存在字段时拒绝结果。

真实模型调用放到后续 `AI interface` 开发，不在本轮偷偷绑定具体厂商。

---

## 10. 汇总器

### 10.1 输入过滤

不参与有效结论统计：

- `applicable=false`。
- `data_status=BLOCKED`。
- 策略执行失败。
- `maturity=DISABLED`。

`DRAFT` 和 `PAPER_ONLY` 可以展示，但必须单独统计，不能影响未来正式交易裁决。

### 10.2 家族去重

第一版不做数学去相关。相同 `strategy_family` 的多条策略：

- 各自结果全部展示。
- 家族汇总只算一个家族意见。
- 家族内部冲突必须显示。

### 10.3 综合结论

第一版输出：

```text
FAVORABLE
MIXED
UNFAVORABLE
INSUFFICIENT
```

建议的透明规则：

- 无有效 `ACTIVE` 或 `PAPER_ONLY` 结果：`INSUFFICIENT`。
- 支持和反对家族同时存在：`MIXED`。
- 只有支持或支持明显占优且无强反对：`FAVORABLE`。
- 只有反对或反对明显占优：`UNFAVORABLE`。
- 不能判断时保守返回 `MIXED` 或 `INSUFFICIENT`。

具体阈值放在 `aggregation.json` 并记录版本，不散落在代码中。

### 10.4 不输出综合分

在单策略完成历史校准、家族相关性评估和汇总权重验证前，不增加 `overall_score`。

---

## 11. 交易裁决边界

策略平台只输出技术研究结论。后续风险和资金层生成 `DecisionVerdict`：

买入：

```text
BUY_ALLOWED
WATCH_ONLY
REJECTED
DATA_BLOCKED
```

持仓：

```text
HOLD_ALLOWED
REDUCE_ALLOWED
EXIT_ALLOWED
EXECUTION_BLOCKED
DATA_BLOCKED
```

再由组合和执行层生成 `TradeIntent`。

需要同步改造的旧行为：

- `WATCH_SMALL` 不再自动转成买单。
- 持仓策略不处理 T+1 最终动作。
- `paper_trading.py` 后续只接受批准后的 `TradeIntent`。
- `risk_control.py` 不再反向解释策略内部评分。

这些改动在策略平台核心稳定后单独执行，不能只改枚举名称而保留旧语义。

---

## 12. 数据库落地

### 12.1 迁移文件

新增：

```text
migrations/sqlite/014_strategy_platform_v02.sql
```

不要修改已经应用的 `001_core_schema.sql`。

### 12.2 第一版新增表

#### `analysis_snapshots`

高频字段：

```text
snapshot_id TEXT PRIMARY KEY
symbol TEXT NOT NULL
name TEXT
task_type TEXT NOT NULL
market_phase TEXT NOT NULL
trade_date TEXT NOT NULL
decision_time TEXT NOT NULL
source_cutoff_time TEXT
feature_set_version TEXT NOT NULL
data_status TEXT NOT NULL
payload_json TEXT NOT NULL
created_at TEXT NOT NULL
```

#### `strategy_runs`

```text
run_id TEXT PRIMARY KEY
snapshot_id TEXT NOT NULL
symbol TEXT NOT NULL
task_type TEXT NOT NULL
market_phase TEXT NOT NULL
status TEXT NOT NULL
registry_version TEXT NOT NULL
started_at TEXT NOT NULL
finished_at TEXT
duration_ms INTEGER
error_json TEXT
```

#### `strategy_evaluations`

```text
evaluation_id TEXT PRIMARY KEY
run_id TEXT NOT NULL
snapshot_id TEXT NOT NULL
strategy_id TEXT NOT NULL
strategy_version TEXT NOT NULL
parameter_version TEXT NOT NULL
strategy_family TEXT NOT NULL
implementation_type TEXT NOT NULL
maturity TEXT NOT NULL
applicable INTEGER NOT NULL
data_status TEXT NOT NULL
raw_score NUMERIC
calibrated_score NUMERIC
signal TEXT NOT NULL
confidence TEXT NOT NULL
duration_ms INTEGER
payload_json TEXT NOT NULL
created_at TEXT NOT NULL
```

唯一约束：

```text
UNIQUE(run_id, strategy_id, strategy_version, parameter_version)
```

#### `strategy_aggregations`

```text
aggregation_id TEXT PRIMARY KEY
run_id TEXT NOT NULL UNIQUE
snapshot_id TEXT NOT NULL
conclusion TEXT NOT NULL
effective_strategy_count INTEGER NOT NULL
support_count INTEGER NOT NULL
oppose_count INTEGER NOT NULL
neutral_count INTEGER NOT NULL
unknown_count INTEGER NOT NULL
aggregator_version TEXT NOT NULL
payload_json TEXT NOT NULL
created_at TEXT NOT NULL
```

### 12.3 暂不新增的表

以下内容先放在 JSON 中：

- 规则明细。
- 证据和反证。
- 触发和证伪条件。
- 家族冲突明细。

只有页面查询或统计出现明确需求后再规范化。

### 12.4 旧表处理

`strategy_snapshots` 和 `decision_results` 暂不删除，避免破坏已有历史记录，但新策略平台不再向其写入。

这属于保留历史数据，不属于运行时兼容。确认新页面和回放不再查询旧表后，再单独设计归档或删除迁移。

### 12.5 PostgreSQL 迁移约束

- 主键全部由应用生成。
- 布尔值在 Repository 层转换。
- 时间统一 ISO 8601 且带时区。
- SQL 不依赖 SQLite 隐式类型转换。
- JSON 字段通过 Repository 统一序列化，未来可迁移为 PostgreSQL `jsonb`。

---

## 13. Repository 与事务

一次策略运行使用一个事务保存：

```text
analysis_snapshot
strategy_run
N 条 strategy_evaluation
1 条 strategy_aggregation
```

如果策略运行部分失败：

- 保存成功的策略结果。
- 保存失败策略的错误结果。
- `strategy_run.status=PARTIAL`。
- 仍允许生成报告，但报告必须醒目标记不完整。

数据库写入失败时，不能报告运行成功。

---

## 14. 报告落地

新增多策略报告，不继续在 `build_decision.py` 中拼接大量 Markdown。

建议输出：

```text
data/reports/strategy_report_<symbol>_<task>_<timestamp>.md
```

报告顺序：

1. 时间、股票和任务。
2. 数据状态和时间警告。
3. 综合研究结论，不显示综合分。
4. 有效、阻断、失败和不适用策略数量。
5. 每条策略的原始分、信号、证据和反证。
6. 策略家族一致性和冲突。
7. 触发条件和证伪条件。
8. 风险与资金裁决（已运行时）。
9. AI 策略和未校准结果提示。
10. 数据、策略、参数、评分和汇总版本。

报告生成器只读取结构化结果，不重新计算策略逻辑。

---

## 15. 调用入口迁移

### 15.1 新入口

新增统一入口：

```python
run_strategy_pipeline(snapshot, registry, context) -> PipelineResult
```

### 15.2 切换顺序

1. 固定样本 CLI。
2. 单票 `build_decision.py` 和技术报告。
3. 风险、资金和 `TradeIntent` 新协议。
4. Web 单票决策和持仓报告。
5. 盘前/盘后工作流。
6. 历史回放。
7. 模拟盘执行入口。

每切换一项都先补测试，再删除该入口对 `run_strategy()` 的调用。

### 15.3 持仓页面

当前持仓合并决策不再手工拼接两份旧 `decision_result`。新页面展示：

- 持仓策略汇总。
- 如需加仓，再独立展示买入策略汇总。
- 风险和执行限制。

### 15.4 历史回放

回放必须记录每个历史日实际启用的：

- 策略版本。
- 参数版本。
- 评分配置哈希。
- 汇总器版本。
- 数据快照 ID。

不能在回放结束后用当前版本重新解释旧结果。

---

## 16. 自动化测试

### 16.1 测试工具

第一版使用标准库：

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

### 16.2 固定样本

测试数据必须是人工构造或固定脱敏样本，不能在测试时访问网络。

至少准备：

- 完整上涨趋势样本。
- 趋势破坏样本。
- 缺 MA60 样本。
- 高波动样本。
- 持仓跌破技术证伪点样本。
- T+1 不可卖但策略支持退出样本。
- AI 合法输出样本。
- AI 缺证据和引用不存在字段样本。

### 16.3 必测内容

- 契约序列化和反序列化。
- 非法枚举和缺字段拒绝。
- 策略 ID 和版本唯一。
- 必需特征缺失时阻断单策略。
- 单策略异常不阻断其他策略。
- 评分配置边界和哈希稳定。
- 未校准分数不生成综合分。
- 同家族策略不重复计票。
- 买入和持仓策略不会混跑。
- AI 缺证据时结果无效。
- 相同快照和版本重复运行结果一致。
- 数据库事务和唯一约束。
- 历史回放不读取未来时间数据。

### 16.4 回归基线

不要求新结果与 v0.1 动作一致。回归测试关注：

- 输入是否相同。
- 每条新策略的证据是否正确。
- 差异是否可以解释。
- 新结果是否违反时间、数据或风控原则。

---

## 17. 开发步骤与验收

### 步骤 1：协议和测试骨架

实现：

- `contracts.py`。
- `validation.py`。
- `tests/strategy_platform`。
- 固定样本。

验收：

- 模型可稳定序列化。
- 非法输入被拒绝。
- 测试命令可运行。

### 步骤 2：快照和特征接口

实现：

- `AnalysisSnapshot` 构建。
- `FeatureStore`。
- 时间和数据质量映射。

验收：

- 当前 stock JSON 可以生成新快照。
- 缺失字段和来源可追踪。
- 策略无法修改快照。

### 步骤 3：注册、运行和评分

实现：

- `Registry`。
- `Runner`。
- JSON 评分器。
- 错误隔离。

验收：

- 新增测试策略不修改运行器。
- 一条策略失败时其他策略继续运行。
- 评分明细和配置哈希完整。

### 步骤 4：三条样例策略

实现：

- `trend-following`。
- `technical-exit`。
- `ai-research` 与 `FakeAIAdapter`。

验收：

- 买入和持仓策略按任务隔离。
- AI 非法输出被拒绝。
- 每条策略可独立测试。

### 步骤 5：汇总和报告

实现：

- 家族意见。
- 冲突识别。
- 无综合分的汇总结果。
- Markdown 报告。

验收：

- 报告能解释每条策略结论。
- 未校准分数不会被平均。
- 部分失败会明确显示。

### 步骤 6：数据库

实现：

- 014 迁移。
- Repository。
- 数据库校验。

验收：

- 一次运行完整写入四类记录。
- 唯一约束有效。
- `database.py validate` 无新增问题。

### 步骤 7：风险和模拟盘边界修正

实现：

- `DecisionVerdict`。
- `TradeIntent`。
- 风险和资金裁决接口。
- 模拟盘只接受批准意图。

验收：

- 观察信号不会自动买入。
- T+1 在执行裁决层处理。
- 研究结论不会直接生成成交。

### 步骤 8：入口切换

实现：

- CLI。
- Web 单票和持仓。
- 工作流。
- 历史回放。
- 模拟盘执行入口。

验收：

- 业务入口不再调用旧 `run_strategy()`。
- 页面和报告展示新结构。
- 旧表不再新增策略记录。
- 全部入口使用相同的策略、裁决和交易意图协议。

---

## 18. 风险与控制

### 18.1 范围过大

控制：先完成三条样例策略和完整链路，不同时开发大量业务策略。

### 18.2 分数造成误导

控制：第一版没有综合分；原始分必须标注未校准状态。

### 18.3 AI 输出不稳定

控制：第一轮只做协议和测试替身；真实 AI 默认 `DRAFT`，不进入正式裁决。

### 18.4 重构破坏现有流程

控制：新平台先在固定样本和独立 CLI 验证，再按入口逐项切换；不长期维护双轨。

### 18.5 过早数据库规范化

控制：只结构化高频查询字段，业务细节先保留 JSON，等查询需求稳定后拆表。

### 18.6 策略看似独立但高度重复

控制：使用策略家族、相关性分析和增量回放；没有增量价值的策略不保留。

---

## 19. 完成定义

本轮开发完成必须同时满足：

```text
新策略可独立增加
新策略有独立评分配置
买入和持仓策略分离
未校准分数不被直接汇总
AI 输出受协议和证据约束
单策略失败不会污染其他策略
新结果写入数据库
技术报告可以解释冲突
固定样本测试全部通过
CLI、Web、工作流和回放使用同一策略平台
模拟盘只接受风险批准后的交易意图
```

完成平台骨架后，再根据数据可得性逐条设计价值、成长、周期、量价和事件策略。每增加一条策略，都必须同时补齐业务说明、字段依赖、评分配置、固定样本和回放结果。
