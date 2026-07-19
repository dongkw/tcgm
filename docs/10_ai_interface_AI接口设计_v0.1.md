# 10 ai_interface 受约束 AI 研究接口设计 v0.1

## 1. 定位与边界

AI 只作为研究资料处理器，不是交易执行器。允许任务固定为：

- `evidence_extract`：从给定资料提取候选事实。
- `evidence_classify`：对给定证据分类。
- `research_summary`：基于已登记证据生成研究摘要。

AI 禁止生成订单、修改账户或持仓、设置交易数量、绕过策略评分，禁止直接输出买入、卖出、加仓或减仓动作。交易策略只能消费通过 schema 和证据校验的结构化结果。

## 2. Provider 设计

核心接口位于 `src/ai_trader/ai_interface/`：

```python
class AIProvider(Protocol):
    @property
    def name(self) -> str: ...
    def execute(self, request: AIResearchRequest) -> Mapping[str, Any]: ...
```

当前 Provider：

- `CodexProvider`：禁用或注入式边界；本地程序不能调用当前 Codex 对话。
- `CodexCliProvider`：调用本机已登录的 Codex CLI。运行目录为临时空目录，使用只读沙箱、临时会话、固定输出 Schema 和低推理配置，不加载项目规则。
- `ManualProvider`：接收人工准备或测试用的固定结构化响应，不连接模型。
- `ProviderRegistry`：按名称显式注册和替换 Provider，后续可接入其他 AI。

Web 默认配置为 `codex-cli`；普通 `build_builtin_registry()` 仍使用禁用 Provider，避免单测、回放和批量任务意外触发模型调用。策略代码不读取 API Key，Codex CLI 使用自身登录状态。

## 3. 固定协议

请求 schema：`ai_research_request.v1`，包含：

```text
request_id
task
task_version
snapshot_id
symbol
constraints
evidence[]
```

每条输入证据包含稳定的 `evidence_id`、快照路径、原始值和 `source_ids`。AI 不接收订单、账户操作接口。

响应 schema：`ai_research_response.v1`，字段严格限制为：

```text
request_id
task
task_version
provider
summary
confidence
stance
evidence_refs[]
risks[]
```

`evidence_refs` 必须引用本次请求中存在的 `evidence_id`。未知字段、未知枚举、缺证据、请求 ID 不匹配、任务版本不匹配和 Provider 不匹配均拒绝。

响应中递归禁止 `action`、`buy`、`sell`、`order`、`quantity`、`position`、`account`、`signal`、`recommendation`、`decision` 等交易字段。

## 4. 阻断规则

以下情况统一阻断 AI 策略，输出 `data_status=BLOCKED` 和 `signal=UNKNOWN`：

- Codex 或其他 Provider 未配置。
- 未知任务。
- 请求没有证据。
- 响应不符合固定 schema。
- 响应引用不存在的证据 ID。
- 响应包含买卖、订单、持仓或账户字段。
- Provider 超时、异常或返回非法对象。

阻断只影响 `ai-research`，不得中断其他规则策略，也不得降级为猜测结果。

## 5. 策略接入

离线和测试默认构建策略库：

```python
registry = build_builtin_registry()
```

此时使用禁用的 `CodexProvider`，`ai-research` 保持 `BLOCKED`。Web 入口根据 `DashboardSettings.ai_provider` 显式注入 `CodexCliProvider`。

Web 使用方式：

```powershell
# 默认使用 codex-cli
python .\web_dashboard.py

# 临时关闭 AI
python .\web_dashboard.py --ai-provider disabled

# 可选：显式指定 CLI 账户支持的模型
$env:AI_TRADER_AI_MODEL = "模型名"
python .\web_dashboard.py
```

显式替换 Provider：

```python
provider = MyAuthenticatedProvider()
registry = build_builtin_registry(ai_provider=provider)
```

Provider 也可以先注册：

```python
providers = ProviderRegistry()
providers.register(CodexProvider())
providers.register(MyAuthenticatedProvider())
provider = providers.get("my-ai")
```

`ai-research` 当前只执行 `research_summary`。其响应通过校验后，策略才会将证据引用映射为 `supporting_evidence`、`opposing_evidence` 和风险记录，再交给独立评分器。

当前输入只包含事实层及元数据声明的 `technical.ma20`、`technical.ma60`、`valuation.pe_ttm`、`valuation.pb`，不把完整快照、账户、持仓或重复特征交给 AI。请求上限为 256 KB。

## 6. 性能与使用范围

本机真实烟雾测试已通过，单次独立 Codex CLI 调用约 66 秒。当前只适合用户主动触发的单股研究，不允许全市场盘后任务为每只股票同步启动 Codex。

批量启用前必须完成：

- 按请求哈希缓存相同证据和任务版本的结果。
- 后台任务队列和并发上限。
- 每日调用次数、Token 和费用上限。
- 超时、熔断和失败重试策略。
- AI 调用审计表。

## 7. 后续工作

- 建立原始公告和财报的正式证据表，替代当前从分析快照生成的临时证据 ID。
- 增加 AI 调用审计表，保存 Provider、任务版本、请求哈希、响应哈希、耗时和失败原因；凭证和敏感原文不得落库。
- 为真实 Provider 增加速率限制、成本上限和熔断。
- 在历史回放中只读取当时已经生效的证据，AI 结果必须绑定原始请求和策略版本。
