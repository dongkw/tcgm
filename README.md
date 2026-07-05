# 天才交易员项目

这是一个面向 A 股买入/持仓评估的本地决策辅助项目。当前阶段重点是：抓取单只股票的客观数据，生成结构化 JSON，再按文档中的决策引擎进行人工或 AI 辅助判断。

> 注意：本项目用于研究和辅助决策，不构成投资建议。后续接入自动交易前，需要补齐风控、回测、审计和人工确认流程。

## 目录结构

```text
.
├── README.md
├── requirements.txt
├── stock_decision_data.py              # 兼容入口：单票数据抓取
├── fetch_stock_codes.py                # 兼容入口：股票代码列表抓取
├── build_decision.py                   # 兼容入口：第一轮决策链路
├── paper_trading.py                    # 兼容入口：第二轮模拟盘账本
├── portfolio_plan.py                   # 兼容入口：第三轮组合计划
├── workflow.py                         # 兼容入口：第四轮盘前/盘中/盘后工作流
├── historical_replay.py                # 兼容入口：第五轮历史回放
├── strategy_iteration.py               # 兼容入口：策略迭代记录
├── database.py                         # 兼容入口：SQLite 数据库导入、校验、备份
├── web_dashboard.py                    # 兼容入口：本地 Web 工作台
├── src/
│   └── ai_trader/
│       ├── __init__.py
│       ├── build_decision.py           # 第一轮：策略快照和决策结果
│       ├── paper_trading.py            # 第二轮：模拟盘信号、订单、成交
│       ├── portfolio_plan.py           # 第三轮：组合计划 CLI
│       ├── workflow.py                 # 第四轮：盘前、盘中、盘后、研究工作流
│       ├── intraday_trigger.py         # 第四轮：盘中触发扫描
│       ├── historical_replay.py        # 第五轮：历史回放 CLI
│       ├── strategy_iteration.py       # 策略回放/模拟盘调参记录
│       ├── database.py                 # SQLite 数据库 CLI
│       ├── db/                         # 数据库连接、迁移、导入、校验
│       ├── web/                        # 本地 Web Dashboard
│       ├── historical_data.py          # 第五轮：历史日线读取和指标计算
│       ├── replay_clock.py             # 第五轮：历史时间上下文
│       ├── replay_snapshot_builder.py  # 第五轮：历史可见快照构建
│       ├── performance.py              # 第五轮：绩效指标
│       ├── risk_control.py             # 第三轮：风控检查
│       ├── portfolio_construction.py   # 第三轮：候选评分和资金分配
│       ├── portfolio.py                # 第二轮：账户、持仓、T+1、流水
│       ├── cost_model.py               # 第二轮：佣金、印花税、滑点
│       └── stock_decision_data.py      # 核心：行情/财务/技术面/公告数据抓取
├── scripts/
│   └── fetch_stock_codes.py            # 工具脚本：生成 A 股代码池
├── data/
│   ├── 沪深A股代码（不含创业板）.csv
│   └── stock_json/
│       ├── stock_data_000969.json
│       ├── stock_data_600398.json
│       ├── stock_data_601138.json
│       └── stock_data_601857.json
└── docs/
    ├── agent.md
    ├── strategy_engine_实现设计.md
    ├── 股票投资决策引擎（AI执行版 v2.0）.md
    ├── 股票投资分析自查手册（逻辑修订版 v2.0）.md
    ├── 股票决策数据底稿_JSON通用模板.md
    ├── 股票决策数据底稿_字段说明与规则映射.md
    └── 自选股买入筛选报告_2026-07-02.md
```

## 快速开始

安装依赖：

```powershell
pip install -r requirements.txt
```

抓取单只股票数据：

```powershell
python .\stock_decision_data.py 601138 --period middle
```

默认输出：

```text
data/stock_json/stock_data_601138.json
```

刷新股票代码池：

```powershell
python .\fetch_stock_codes.py
```

默认输出：

```text
data/沪深A股代码（不含创业板）.csv
```

生成第一轮决策：

```powershell
python .\build_decision.py 002563 --task buy
```

初始化模拟盘：

```powershell
python .\paper_trading.py init --account paper_default --cash 100000
```

盘前换日：

```powershell
python .\paper_trading.py rollover --account paper_default --trade-date 2026-07-06
```

把第一轮决策应用到模拟盘：

```powershell
python .\paper_trading.py apply .\data\decision_results\decision_result_002563_buy_evaluation_xxx.json --account paper_default
```

生成模拟盘快照和报告：

```powershell
python .\paper_trading.py snapshot --account paper_default
```

生成第三轮组合计划：

```powershell
python .\portfolio_plan.py build --account paper_default --trade-date 2026-07-06 --decision-dir data/decision_results
```

执行第四轮盘前工作流：

```powershell
python .\workflow.py pre-market --account paper_default --trade-date 2026-07-06 --symbols 002563,600398,601138
```

执行第四轮盘中触发扫描：

```powershell
python .\workflow.py intraday --account paper_default --trade-date 2026-07-06
```

执行第四轮盘后工作流：

```powershell
python .\workflow.py post-market --account paper_default --trade-date 2026-07-06
```

执行第五轮历史回放：

```powershell
python .\historical_replay.py run --symbols 002563 --start 2025-01-01 --end 2025-12-31 --cash 100000
```

记录一次策略迭代：

```powershell
python .\strategy_iteration.py record --source-type replay --source-path .\data\replay\replay_xxx --hypothesis "验证当前买入规则" --rule-changes "无，本次作为基线" --next-action "扩大样本继续回放"
```

初始化并导入 SQLite 数据库：

```powershell
python .\database.py init
python .\database.py import-json
python .\database.py summary
python .\database.py validate
python .\database.py reconcile
python .\database.py backup
```

数据库存在时，`python .\build_decision.py ...` 生成新的决策快照、决策结果和报告后，会自动同步索引到 SQLite。`import-json` 仍保留为修复和重建索引手段。

启动本地 Web 工作台：

```powershell
python .\web_dashboard.py
```

浏览器打开：

```text
http://127.0.0.1:8000
```

## 当前能力

- 抓取实时行情、估值、K 线技术指标、财务摘要、公告标题、股东户数、融资余额、北向持股和限售解禁。
- 生成单只股票的结构化 JSON 数据底稿。
- 生成机械判断标记，例如均线趋势、短期涨幅、扣非利润连续为负、经营现金流质量等。
- 配合 `docs/股票投资决策引擎（AI执行版 v2.0）.md` 执行买入或持仓评估。
- 读取第一轮 `decision_result`，记录模拟盘信号、订单、成交、持仓、资金流水和每日快照。
- 批量读取多个买入决策，执行风控检查、候选排序和资金分配，生成组合计划报告。
- 编排盘前和盘后手动工作流，记录工作流运行状态，输出盘前计划、触发价列表和盘后复盘报告。
- 读取盘前触发价列表和当前行情，生成盘中触发事件和中文提醒报告；第一版只提醒，不自动成交。
- 读取历史日线 JSONL，执行 `REPLAY_LITE` 历史回放，输出每日记录、模拟盘账本、绩效指标和中文回放报告。
- 将历史回放或模拟盘结果记录为策略调参记录，输出策略迭代 JSONL 和中文汇总报告。
- 将现有 JSON/JSONL 账本导入 SQLite，支持摘要查询、基础对账校验、文件索引对账和数据库备份。
- 第一轮决策生成后自动把 `strategy_snapshot`、`decision_result` 和报告索引同步到 SQLite。
- 模拟盘账户、持仓、现金流水、信号、订单、成交和快照写入后自动同步到 SQLite；JSON/JSONL 仍是事实源。
- 提供本地 Web 工作台，读取 SQLite 展示总览、账户、持仓、决策、报告、回放和数据健康状态。

## 阶段定位

当前所有 `v0.1` 功能主要用于跑通流程，不代表策略已经成熟。

后续真正有用的核心是策略迭代闭环：

```text
历史回放快速验证
  -> 模拟盘持续观察
  -> 复盘错误交易和漏掉的机会
  -> 修改买入、卖出、风控和仓位规则
  -> 再回放、再模拟
```

新增功能都应服务于更快验证和修正交易策略，而不是把决策做成 AI 黑盒。

## 后续演进建议

后续如果要实现“天才交易员”，建议按下面顺序扩展：

1. `data_fetching`：稳定化数据源，增加缓存、失败重试、数据质量检查。
2. `strategy_engine`：把文档里的 A-G 闸门规则代码化，输出可复现的评分和结论。
3. `portfolio`：维护持仓、成本、仓位、止损线、交易日志。
4. `backtesting`：用历史数据验证策略胜率、回撤和仓位规则。
5. `risk_control`：加入单票上限、行业集中度、最大回撤、黑名单和硬否决规则。
6. `agent`：让 AI 只负责解释、归纳和复核，关键交易动作必须经过规则和人工确认。

## 主要文档

- [Agent 执行说明](docs/agent.md)
- [第一轮决策链路使用说明](docs/第一轮决策链路使用说明.md)
- [第二轮模拟盘使用说明](docs/第二轮模拟盘使用说明.md)
- [第三轮组合计划使用说明](docs/第三轮组合计划使用说明.md)
- [第四轮工作流使用说明](docs/第四轮工作流使用说明.md)
- [第五轮历史回放使用说明](docs/第五轮历史回放使用说明.md)
- [天才交易员架构设计](docs/天才交易员架构设计_v0.1.md)
- [01-03 开发落地说明](docs/01-03_开发落地说明_v0.1.md)
- [04-05 开发落地说明](docs/04-05_开发落地说明_v0.1.md)
- [06-07 开发落地说明](docs/06-07_开发落地说明_v0.1.md)
- [历史回放开发落地说明](docs/历史回放_开发落地说明_v0.1.md)
- [盘中触发扫描开发落地说明](docs/盘中触发扫描_开发落地说明_v0.1.md)
- [策略迭代闭环开发落地说明](docs/策略迭代闭环_开发落地说明_v0.1.md)
- [08 数据库开发落地说明](docs/08_数据库开发落地说明_v0.1.md)
- [08 数据库主写入改造开发落地说明](docs/08_数据库主写入改造_开发落地说明_v0.1.md)
- [01 data_catalog 有效数据目录设计](docs/01_data_catalog_有效数据目录设计_v0.1.md)
- [02 strategy_engine 策略与信号设计](docs/02_strategy_engine_策略与信号设计_v0.1.md)
- [03 timekeeper 时间与交易日历设计](docs/03_timekeeper_时间与交易日历设计_v0.1.md)
- [04 backtesting 回测与模拟盘设计](docs/04_backtesting_回测与模拟盘设计_v0.1.md)
- [05 portfolio 持仓与资金系统设计](docs/05_portfolio_持仓与资金系统设计_v0.1.md)
- [06 portfolio_construction 组合构建设计](docs/06_portfolio_construction_组合构建设计_v0.1.md)
- [07 risk_control 风控分层设计](docs/07_risk_control_风控分层设计_v0.1.md)
- [08 database_schema 数据库设计](docs/08_database_schema_数据库设计_v0.1.md)
- [09 workflow 盘前盘中盘后设计](docs/09_workflow_盘前盘中盘后设计_v0.1.md)
- [股票投资决策引擎](docs/股票投资决策引擎（AI执行版%20v2.0）.md)
- [数据字段与规则映射](docs/股票决策数据底稿_字段说明与规则映射.md)
