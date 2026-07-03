# 股票决策数据底稿（JSON通用模板）

> 本文档定义 `stock_decision_data.py` 生成 JSON 后应整理出的通用数据底稿格式。  
> 适用对象：任意单只 A 股。  
> 用途：作为《股票投资决策引擎（AI执行版 v2.0）》的输入数据层，先收集客观数据，再进入闸门判断。  
> 纪律：本文只记录数据和机械规则，不直接给出“买入 / 观望 / 不买入”。

---

## 0. 数据来源与基本信息

| 字段 | 填写内容 |
|------|----------|
| 原始JSON文件 | `stock_json/stock_data_<股票代码>.json` |
| 数据生成时间 | `<meta.generated_at>` |
| 评估周期 | `<meta.requested_period>`：short / middle / long |
| 数据来源 | `<meta.source>` |
| 股票代码 | `<quote.code>` |
| 股票名称 | `<quote.name>` |
| 市场 | `<quote.market>` |
| 行情日期 | `<quote.trade_date>` |

---

## 1. 行情与估值基础数据

| 字段 | JSON路径 | 单位/口径 | 用途 |
|------|----------|-----------|------|
| 当前价 | `quote.price` | 元 | 输入清单必需项；仓位、止损、估值计算基准 |
| 开盘价 | `quote.open` | 元 | 日内走势参考 |
| 最高价 | `quote.high` | 元 | 日内压力参考 |
| 最低价 | `quote.low` | 元 | 日内风险参考 |
| 昨收价 | `quote.previous_close` | 元 | 涨跌计算基准 |
| 涨跌额 | `quote.change` | 元 | 日内变化 |
| 涨跌幅 | `quote.pct_change` | % | 大跌日禁追规则 |
| 成交量 | `quote.volume_lots` | 手 | 流动性与量能 |
| 成交额 | `quote.amount_yuan` | 元 | 流动性与量能 |
| 总市值 | `quote.market_cap_yuan` | 元 | 公司规模、流动性 |
| PE-TTM | `quote.pe_ttm` | 倍 | 非周期股估值 |
| PB | `quote.pb` | 倍 | 周期股估值参考 |
| 股息率 | `quote.dividend_yield_pct` | % | 回报率公式 |
| 行情口径ROE | `quote.roe_pct_quote` | % | 质量参考 |
| 行情口径净利率 | `quote.net_margin_pct_quote` | % | 质量参考 |
| 交易状态 | `quote.trading_status` | 数据源状态码 | 停复牌/交易状态参考 |

### 行情数据填写表

| 项目 | 数值 |
|------|------|
| 当前价 | |
| PE-TTM | |
| PB | |
| 股息率 | |
| 总市值 | |
| 当日涨跌幅 | |
| 成交额 | |

---

## 2. 技术面数据

| 字段 | JSON路径 | 单位/口径 | 对应规则 |
|------|----------|-----------|----------|
| 最新K线日期 | `technical.last_date` | 日期 | 数据时点 |
| 收盘价 | `technical.close` | 元 | 技术判断基准价 |
| 当日涨跌幅 | `technical.day_pct_change` | % | 大跌日禁追 |
| 成交额 | `technical.amount_yuan` | 元 | 量能辅助 |
| 20日均线 | `technical.ma20` | 元 | 短线/波段买点 |
| 60日均线 | `technical.ma60` | 元 | 中线买点 |
| 120日均线 | `technical.ma120` | 元 | 趋势参考 |
| 20日均线是否向上 | `technical.ma20_slope_up` | true/false | 短线趋势确认 |
| 60日均线是否向上 | `technical.ma60_slope_up` | true/false | 中线趋势确认 |
| 是否站上20日线 | `technical.above_ma20` | true/false | 短线买点是否成立 |
| 是否站上60日线 | `technical.above_ma60` | true/false | 中线买点是否成立 |
| 5日涨跌幅 | `technical.change_5d_pct` | % | 短期强弱 |
| 20日涨跌幅 | `technical.change_20d_pct` | % | A-4追高过滤 |
| 60日涨跌幅 | `technical.change_60d_pct` | % | 追高降级规则 |
| 20日最高价 | `technical.high_20d` | 元 | 平台压力/突破参考 |
| 20日最低价 | `technical.low_20d` | 元 | 风险位参考 |
| ATR14 | `technical.atr14_pct` | % | 止损幅度、仓位计算 |
| 近10日K线 | `technical.recent_10d` | 数组 | 放量下跌、缩量反弹、平台突破 |

### 技术面填写表

| 项目 | 数值 | 初步结论 |
|------|------|----------|
| 收盘价 | | |
| 20日均线 | | |
| 是否站上20日线 | | |
| 20日均线是否向上 | | |
| 60日均线 | | |
| 是否站上60日线 | | |
| 60日均线是否向上 | | |
| 20日涨跌幅 | | |
| 60日涨跌幅 | | |
| ATR14 | | |

---

## 3. 财务数据

### 3.1 最新一期财务

| 字段 | JSON路径 | 单位/口径 | 对应规则 |
|------|----------|-----------|----------|
| 报告期 | `financial.latest.report` | 报告名称 | 最近季度验证 |
| 报告类型 | `financial.latest.report_type` | 一季报/中报/三季报/年报 | 财报类型 |
| 公告日期 | `financial.latest.notice_date` | 日期 | 信息时点 |
| 营业收入 | `financial.latest.revenue_yuan` | 元 | 增长判断 |
| 归母净利润 | `financial.latest.parent_net_profit_yuan` | 元 | 盈利规模 |
| 扣非净利润 | `financial.latest.deducted_net_profit_yuan` | 元 | A-2、C-1 |
| 经营现金流 | `financial.latest.operating_cashflow_yuan` | 元 | C-3 |
| 经营现金流/净利润 | `financial.latest.ocf_to_net_profit` | 倍 | 盈利质量 |
| EPS | `financial.latest.eps` | 元 | 估值计算 |
| 扣非EPS | `financial.latest.deducted_eps` | 元 | 估值计算 |
| ROE | `financial.latest.roe_pct` | % | 质量判断 |
| 毛利率 | `financial.latest.gross_margin_pct` | % | 盈利能力 |
| 净利率 | `financial.latest.net_margin_pct` | % | 盈利能力 |
| 资产负债率 | `financial.latest.debt_asset_ratio_pct` | % | 杠杆风险 |
| 自由现金流 | `financial.latest.free_cashflow_yuan` | 元 | 现金流补充规则 |
| 营收同比 | `financial.latest.revenue_yoy_pct` | % | 增长验证 |
| 归母净利润同比 | `financial.latest.parent_np_yoy_pct` | % | 增长验证 |
| 扣非净利润同比 | `financial.latest.deducted_np_yoy_pct` | % | 增长验证 |
| 总股本 | `financial.latest.total_share` | 股 | EPS、估值参考 |

### 3.2 近3年年报

数据来源：`financial.annual_last_3`

| 报告 | 营收 | 归母净利 | 扣非净利 | 经营现金流 | 经营现金流/净利润 | EPS | ROE | 毛利率 | 净利率 | 资产负债率 | 自由现金流 |
|------|------|----------|----------|------------|--------------------|-----|-----|--------|--------|------------|------------|
| 最近年报 | | | | | | | | | | | |
| 前一年报 | | | | | | | | | | | |
| 前二年报 | | | | | | | | | | | |

### 3.3 财务硬规则机械检查

| 检查项 | JSON路径 | 规则 | 结果 |
|--------|----------|------|------|
| 最近两个会计年度扣非净利润均为负 | `financial.checks.deducted_net_profit_negative_latest_2y` | 命中 A-2 硬否决 | |
| 经营现金流/净利润连续3年 < 0.5 | `financial.checks.ocf_to_net_profit_below_0_5_latest_3y` | 命中 C-3 硬否决 | |
| 最近年报经营现金流/净利润 | `financial.checks.latest_annual_ocf_to_net_profit` | 若单年 < 0.5，信心下调 | |

---

## 4. 公告关键词数据

| 检查项 | JSON路径 | 用途 | 结果 |
|--------|----------|------|------|
| 减持公告 | `announcements.减持` | A-1、E筹码风险 | |
| 监管/立案公告 | `announcements.监管` | A-3、C-6、E风险过滤 | |
| 回购公告 | `announcements.回购` | E加分项，不作买入理由 | |

> 说明：公告关键词抓取只做初筛。结果为空不代表绝对没有相关事项；命中结果也必须打开原公告核对原因、比例、时间和影响。

---

## 5. 机械闸门标记

数据来源：`engine_flags`

| 字段 | 对应规则 | true含义 | false含义 | 当前结果 |
|------|----------|----------|-----------|----------|
| `a2_deducted_profit_negative_2y` | A-2 | 最近两年扣非净利润均为负 | 未触发A-2 | |
| `c3_ocf_low_3y` | C-3 | 经营现金流/净利润连续3年 < 0.5 | 未触发C-3 | |
| `short_term_above_ma20_and_rising` | G短线 | 站上20日线且20日线向上 | 短线买点未确认 | |
| `middle_term_above_ma60_and_rising` | G中线 | 站上60日线且60日线向上 | 中线买点未确认 | |
| `chase_high_1m_over_80_pct` | A-4 | 近1个月涨幅 > 80% | 未触发 | |
| `chase_high_1m_50_to_80_pct` | 追高降级 | 近1个月涨幅50%-80% | 未触发 | |
| `big_down_day_over_5_pct` | 大跌日禁追 | 当日跌幅 > 5% | 未触发 | |

---

## 6. 数据缺口

数据来源：`data_gaps_for_engine`

必须逐项列出：

- 近5年PE中位数/分位数是否缺失：
- 未来第3年EPS预估及假设是否缺失：
- 股东户数、融资余额、北向资金、解禁是否缺失：
- 监管/减持公告原文是否已人工复核：
- 应收账款/营收、存货/营收、商誉/净资产是否缺失：

---

## 7. 进入决策引擎前的摘要

| 闸门 | 数据是否足够 | 机械结果 | 需要人工判断的内容 |
|------|--------------|----------|--------------------|
| A 前置否决 | | | 减持原因、监管原文、基本面改善是否对应涨幅 |
| B 周期匹配 | | | 用户真实交易周期、未来催化剂 |
| C 基本面否决 | | | 应收、存货、商誉、重大负面 |
| D 类型与估值 | | | 类型判断、近5年PE中位数、未来EPS假设 |
| E 筹码风险 | | | 股东户数、融资余额、北向、解禁 |
| F 三段式逻辑 | | | 核心逻辑、催化剂、证伪信号 |
| G 技术与仓位 | | | 买点分类、止损价、仓位上限 |

---

## 8. 输出原则

本底稿只能支持以下工作：

- 快速整理输入数据。
- 预先计算机械规则。
- 标出缺失数据。
- 为最终决策节省重复查找时间。

本底稿不能直接替代：

- 公司类型判断。
- 未来EPS假设。
- 核心逻辑和证伪信号。
- 最终“买入 / 观望 / 不买入”建议。
