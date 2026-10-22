# ADR-0002: 技术选型——OpenAI Agents SDK（首页 loop）与 Pydantic AI（DIY 运行时）

> 状态：**已接受**（2026-09）
> 修订：2026-09 —— **D2、D3 已被 [ADR-0003](0003-统一运行时与平台集成修订.md) 撤销**：DIY 运行时统一为 OpenAI Agents SDK + 自研 AgentSpec 薄层；"双运行时共存纪律"由"单运行时纪律"取代。**D1 与备选结论仍有效。**
> 上游文档：`docs/智能体平台架构方案.md` §2

## 背景

平台需要两个 agent 运行时：

1. **首页通用智能体**：平台自有的强 agent，需要 loop、subagent 扇出、流式。
2. **智能体页 DIY**：用户定义的 agent，需要声明式配置、构造期校验、预算控制。

约束条件：模型 fleet 全是 deepseek/glm/qwen/mimo，经现有 LiteLLM proxy（litellm 与 model lake 双模式）；任何绑定单一厂商的框架（Claude Agent SDK、Google ADK）直接出局。

## 决策

### D1 首页 loop：OpenAI Agents SDK（Python）

**接法（关键）**：走 OpenAI 兼容路径而非 LiteLLM 集成：

```python
from openai import AsyncOpenAI
from agents import set_default_openai_client, set_tracing_disabled

# LiteLLM proxy 本身就是 OpenAI 兼容端点，模型字符串 = proxy 的模型 id
set_default_openai_client(
    AsyncOpenAI(base_url=LITELLM_PROXY_URL, api_key=LITELLM_PROXY_KEY)
)
set_tracing_disabled(True)   # 观测走自建日志，不用 OpenAI 云端 tracing
```

不采用 `pip install "openai-agents[litellm]"` + `LitellmModel`：官方标注 **beta**，小众 provider 有坑；且会引入第二个 LiteLLM 依赖面。

**subagent**：agent-as-tool 模式——主 agent 把子任务 Agent 用 `@function_tool` 包装成工具，由主 agent 按需扇出（并行 `asyncio.gather`）。不预定义角色流水线，扇出与否由主 agent 运行时判断。

**已知代价（接受）**：
- SDK 处于 0.x，API churn —— 锁版本，升级走变更管理；业务逻辑不侵入 SDK 层。
- 托管工具/tracing UI 不可用 —— 观测自建（已有 loguru + DB 体系）。

### D2 DIY 运行时：Pydantic AI（AgentSpec）

用户智能体定义 = Pydantic AI AgentSpec（YAML），`Agent.from_spec()` 加载执行。

选型依据（官方 agent-spec 文档）：

| 能力 | DIY 场景映射 |
|---|---|
| YAML/JSON 声明式定义 | 用户配置直接持久化；非开发者（提示词工程师）可写 |
| `AgentSpec.to_file(schema_path=...)` 生成伴随 JSON Schema | 前端表单编辑器校验/自动补全白拿 |
| 模板字符串 + `deps_schema` 构造期校验 | 用户配置错误保存时即报，非运行时炸 |
| `output_schema` 结构化输出 | 可选的结构化交付（注意：不做运行时校验，见风险） |
| 原生 token/请求预算硬上限 | 多租户防失控烧钱 |
| `capabilities` 可注册自定义能力 | 平台工具（沙箱/搜索/检索）注册为 capabilities |

模型接入：自定义 Model（或 OpenAI-compatible provider）指向同一 LiteLLM proxy；用户可选模型 = `/api/chat/models` 白名单子集。

**已知代价（接受）**：`output_schema` 是给模型的指令而非运行时校验（response 返回 plain dict 不验证 properties/required）——需要向用户承诺"结构化保证"的场景自行包 Pydantic 校验 + 重试。

### D3 双运行时的共存纪律

系统存在两个 loop 运行时是**受控的架构债**，以下纪律把漂移锁死：

1. **工具层只经 MCP**：两个运行时都不直接实现工具，都只做 MCP client。工具实现只有一份。
2. **agent 定义都收敛到声明式**：首页 agent 的配置（模型、工具白名单、技能列表）同样以 AgentSpec 同构的结构持久化，只是运行时不同。
3. **模型路由唯一**：都指向同一 LiteLLM proxy，模型清单同一份。
4. **技能层唯一**：SKILL.md 格式，两个运行时共用加载逻辑（各自适配层）。
5. 每季度评估一次是否合并为单运行时（触发条件：任一运行时的 SDK 出现重大问题，或维护成本超阈值）。

### 备选与否决理由

| 备选 | 适用处 | 否决/保留理由 |
|---|---|---|
| LangGraph 1.2 | — | 图编排不是当前需求；范式（预定义多角色图）已被 NMI 2026 研究证伪；作为未来按需组件保留在案 |
| Mastra | — | TypeScript only，后端是 Python |
| Vercel AI SDK v7 | 前端 | 记录在案：若前端需要 agent UI 原语（useChat 等）可评估；当前自研流式组件已够用 |
| Claude Agent SDK | — | 锁 Claude 模型；实测 ~35k input token/次（25× 于薄运行时，有效成本 4-5×）；8.5s 中位延迟 |
| CrewAI | — | 原型玩具（856MB 依赖、K8s client 进依赖树）；多 agent token 开销最高 |
| AutoGen / AG2 | — | 官方维护模式；社区分叉混乱 |
| 自研 loop | — | chat_service 已有一套且运行良好，但新平台选 SDK 是为了 subagent/tracing/guardrail 生态；两套并存（chat_service 服务知识库，新栈服务智能体平台）在服务边界上是清晰的 |

## 对既有代码的影响

- `agentic_knowledge_system` 不动：知识库问答继续走自己的 chat_service。
- `agent_apps` 现有 `src/agents/*`（空壳）与 deepresearch 设计文档归档；新代码结构按主方案 §2 落地。
- 前端 AI-site：首页解锁 + 智能体页解锁，复用知识库对话的流式组件。
