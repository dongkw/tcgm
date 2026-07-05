# 07 risk_control 风控分层设计 v0.1

> 本文档是“天才交易员”的第七步细化设计。  
> 目标是把数据风险、时间风险、交易规则风险、资金风险、组合风险和系统风险拆成可执行的风控闸门。  
> 本文档只做设计，不写代码。

---

## 1. 设计目标

07 要解决的问题：

1. 哪些情况必须禁止交易。
2. 哪些情况可以降级为观察或小仓。
3. 哪些情况只需要提示风险。
4. 多模块之间谁有最终拦截权。
5. 用户人工确认能覆盖哪些规则，不能覆盖哪些规则。

风控模块的定位：

```text
strategy_engine 给出策略建议
  ↓
risk_control 判断是否允许进入执行或组合分配
  ↓
portfolio_construction 分配资金
  ↓
paper_trading / planned_order 执行
```

风控模块可以降级或拒绝策略建议。

AI 不能绕过风控。

---

## 2. 风控分层

第一版风控分七层：

```text
R0 数据与时间风控
R1 标的与市场规则风控
R2 策略结论风控
R3 账户资金风控
R4 持仓与 T+1 风控
R5 组合集中度风控
R6 执行与系统风控
```

每层输出统一结构：

```text
risk_status
risk_level
risk_actions
risk_reasons
blocking_rules
warning_rules
```

---

## 3. 风控动作

### 3.1 风控状态

```text
PASS        通过
WARN        通过但提示
DOWNGRADE   降级
REJECT      拒绝
BLOCK       系统阻塞
```

### 3.2 风控动作

```text
ALLOW
ALLOW_SMALL
RECORD_ONLY
DEFER
REDUCE_QUANTITY
REJECT_ORDER
BLOCK_WORKFLOW
REQUIRE_HUMAN_REVIEW
```

### 3.3 人工确认边界

人工确认可以覆盖：

- 低信心提示。
- 普通估值偏高提示。
- 行业集中度轻微超限。
- 小额观察仓。

人工确认不能覆盖：

- 非交易日成交。
- 数据阻塞。
- 价格缺失。
- T+1 不可卖。
- 买入数量不满足 100 股。
- 现金不足。
- 标的停牌。
- 黑名单硬拒绝。
- 系统时间异常。

---

## 4. R0 数据与时间风控

### 4.1 数据阻塞

以下情况直接 `BLOCK`：

- `data_quality.level = BLOCKED`
- 缺少 `symbol`
- 缺少 `trade_date`
- 缺少 `reference_price`
- 价格小于等于 0
- 价格交易日与决策交易日不一致
- 关键财务数据被标记为不可用但策略仍依赖它

输出：

```text
risk_status = BLOCK
risk_action = BLOCK_WORKFLOW
reason = DATA_BLOCKED
```

### 4.2 时间阻塞

以下情况不能成交：

- `time_context.is_trading_day = false`
- `session_name = NON_TRADING`
- 决策时间早于数据生效时间。
- 盘后公告被用于当天盘中决策。
- 回放时使用未来数据。

输出：

```text
risk_status = BLOCK
risk_action = RECORD_ONLY
reason = TIME_INVALID
```

### 4.3 数据新鲜度

第一版规则：

| 数据类型 | 过期处理 |
|---|---|
| 行情价格 | 缺失或交易日不一致则阻塞成交 |
| 财报 | 可滞后，但必须按公告日生效 |
| 公告 | 缺失则提示，不默认阻塞 |
| 持仓 | 缺成本或可卖数量时卖出降级 |

---

## 5. R1 标的与市场规则风控

### 5.1 资产类型

第一版只允许：

```text
asset_type = A_STOCK
```

ETF、基金、可转债后续单独设计。

未知资产类型：

```text
risk_status = REJECT
reason = UNSUPPORTED_ASSET_TYPE
```

### 5.2 A 股交易规则

买入：

- 数量必须是 100 股整数倍。
- 价格必须有效。
- 不能在非交易日成交。

卖出：

- 数量不能超过 `available_quantity`。
- 非清仓卖出按 100 股整数倍。
- 清仓允许带不足 100 股尾数。
- 当日买入不能当日卖出。

### 5.3 涨跌停和停牌

第一版如果缺少涨跌停和停牌数据：

```text
risk_status = WARN
risk_action = REQUIRE_HUMAN_REVIEW
```

后续补齐后：

- 涨停不允许模拟买入成交，除非有更细撮合模型。
- 跌停不允许模拟卖出成交，除非有更细撮合模型。
- 停牌不允许成交。

---

## 6. R2 策略结论风控

### 6.1 不可交易结论

以下结论不能生成订单：

```text
WAIT
DO_NOT_BUY
DATA_BLOCKED
HOLD
NO_SELL_T_PLUS
PRE_EVALUATION
```

处理：

```text
risk_action = RECORD_ONLY
```

### 6.2 低信心降级

规则：

```text
confidence = LOW
```

处理：

- `BUY` 降级为 `WATCH_SMALL`。
- `WATCH_SMALL` 降级为只记录。
- 卖出类信号要求人工确认。

### 6.3 策略版本限制

策略状态：

```text
DRAFT       只允许回放
PAPER_ONLY  只允许模拟盘
ACTIVE      可进入正式计划
DISABLED    不允许使用
```

第一版默认：

```text
strategy_status = PAPER_ONLY
```

---

## 7. R3 账户资金风控

### 7.1 可用现金

买入前必须满足：

```text
estimated_cash_needed <= available_cash
```

不足时：

```text
risk_action = REDUCE_QUANTITY
```

如果降到不足 100 股：

```text
risk_action = REJECT_ORDER
reason = CASH_NOT_ENOUGH_FOR_ONE_LOT
```

### 7.2 现金保留线

新增买入后必须满足：

```text
available_cash_after >= total_assets * cash_reserve_pct
```

否则：

```text
BUY -> WATCH_SMALL 或 REJECT
```

### 7.3 单日买入上限

```text
today_buy_used + estimated_cash_needed <= max_daily_buy_amount
```

超过时：

```text
risk_action = DEFER
reason = MAX_DAILY_BUY_AMOUNT
```

### 7.4 本金有限保护

当账户总资产较小，系统默认更保守：

- 优先少交易。
- 优先减少同时持仓数量。
- 优先保留现金。
- 单票观察仓不应过小到无法覆盖交易成本。

---

## 8. R4 持仓与 T+1 风控

### 8.1 可卖数量

卖出前必须满足：

```text
sell_quantity <= available_quantity
```

否则：

```text
risk_status = REJECT
reason = NO_AVAILABLE_QUANTITY
```

### 8.2 锁定明细一致性

必须满足：

```text
sum(open position_locks.remaining_locked_quantity) == position.locked_quantity
```

不一致：

```text
risk_status = BLOCK
reason = POSITION_LOCK_MISMATCH
```

### 8.3 持仓信息缺失

卖出评估缺少以下任一字段：

- 成本价。
- 可卖数量。
- 当前仓位。
- 原买入逻辑。
- 原证伪点。

处理：

```text
final_action 最高只能 PRE_EVALUATION
```

---

## 9. R5 组合集中度风控

### 9.1 单票上限

```text
position_pct_after <= max_single_position_pct
```

超过时：

```text
REDUCE_QUANTITY 或 REJECT_ORDER
```

### 9.2 总权益仓位

```text
equity_position_pct_after <= max_equity_position_pct
```

默认：

```text
max_equity_position_pct = 80%
```

市场弱势时可降低到 50%-60%。

### 9.3 行业集中度

第一版如果没有行业字段，只提示缺失。

后续有行业字段后：

```text
industry_position_pct_after <= max_industry_position_pct
```

默认：

```text
max_industry_position_pct = 40%
```

### 9.4 相关性和主题拥挤

第一版不做复杂相关性计算。

但需要预留：

- 同行业。
- 同主题。
- 同政策方向。
- 同周期暴露。

---

## 10. R6 执行与系统风控

### 10.1 防重复执行

同一个 `decision_id` 不能重复生成成交。

如果重复：

```text
risk_status = BLOCK
reason = DUPLICATE_DECISION_EXECUTION
```

### 10.2 daily_rollover

每个交易日必须先执行：

```text
daily_rollover
```

否则不能模拟成交。

### 10.3 系统异常

以下情况阻塞：

- 账户总资产小于等于 0。
- 现金为负。
- 持仓数量为负。
- 成本为负。
- JSON/数据库写入失败。
- 系统时间明显异常。

### 10.4 急停开关

后续应支持：

```text
trading_enabled = false
paper_trading_enabled = false
new_buy_enabled = false
```

第一版至少支持配置层禁用新增买入。

---

## 11. 风控结果结构

```json
{
  "risk_check_id": "risk_20260706_000001",
  "account_id": "paper_default",
  "decision_id": "dr_002563_xxx",
  "symbol": "002563",
  "trade_date": "2026-07-06",
  "risk_status": "DOWNGRADE",
  "risk_level": "MEDIUM",
  "allowed_action": "WATCH_SMALL",
  "original_action": "BUY",
  "max_cash_amount": 3000.0,
  "max_quantity": 500,
  "blocking_rules": [],
  "warning_rules": [
    {
      "rule_id": "R3-2",
      "reason": "cash reserve line close"
    }
  ],
  "human_review_required": true,
  "created_at": "2026-07-06T09:00:00+08:00"
}
```

---

## 12. 风控参数

第一版可先放配置文件：

```text
config/risk_rules.yaml
```

建议参数：

```text
max_single_position_pct: 30
max_equity_position_pct: 80
max_industry_position_pct: 40
cash_reserve_pct: 20
max_daily_buy_amount: 30000
allow_low_confidence_buy: false
allow_non_trading_trade: false
allow_duplicate_execution: false
```

敏感配置或账户相关配置不进入 Git。

---

## 13. 第一版 MVP

第一版建议实现：

- 数据和时间阻塞。
- 不可交易 action 拦截。
- 现金不足拦截。
- 现金保留线。
- 单票仓位上限。
- T+1 可卖数量拦截。
- 防重复执行。
- daily_rollover 缺失拦截。
- 输出 `risk_check_result`。

第一版暂不实现：

- 完整行业集中度。
- 市场状态动态仓位。
- 波动率风险预算。
- 相关性矩阵。
- 实盘急停。

---

## 14. 验收标准

07 后续开发完成后，应满足：

- `WAIT`、`HOLD`、`PRE_EVALUATION` 不会生成订单。
- 非交易日不会生成模拟成交。
- 现金不足会拒绝或缩小买入数量。
- 买入后现金不会低于保留线。
- 单票仓位不会超过上限。
- T+1 不可卖时无法卖出。
- 重复 `decision_id` 无法重复成交。
- 风控结果能解释为什么通过、降级或拒绝。
- AI 和人工确认不能覆盖硬风控。

---

## 15. 与前后模块衔接

02 策略引擎输出：

```text
decision_result
```

05 持仓资金输出：

```text
portfolio_context
```

07 输出：

```text
risk_check_result
```

06 消费：

```text
risk_check_result
```

04 模拟盘执行前必须再次校验：

```text
risk_check_result.status != BLOCK / REJECT
```

10 AI 只能解释：

```text
为什么风控通过、降级或拒绝
```

不能覆盖风控。
