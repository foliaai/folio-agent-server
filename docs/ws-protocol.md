# folio 平台 WS 帧协议（§8.3 对照档）

> 双端实现对照：AKS（`src/service/chat/types.py`）与 agent-server（`src/runtime/ws_protocol.py`）。
> 约定：帧名与载荷字段保持一致；各自专属的帧在"专属"区；新增帧先改本档。

## C→S 帧

| 帧 | 载荷 | 说明 |
|---|---|---|
| `start` | `{message, model?, thinking_level?, session_id?}` | 开始一轮对话；同连接同时只允许一个进行中 turn。model 须在 `/api/v1/models` 清单内（服务端强制）；thinking_level 为 pi 标准档位（off/minimal/low/medium/high/xhigh/max），经 registry+adapter 翻译为厂商参数 |
| `stop` | `{}` | 取消进行中 turn |
| `ping` | `{}` | 心跳 |

## S→C 帧（平台公共）

| 帧 | 载荷 | 说明 |
|---|---|---|
| `session.ready` | `{session_id}` | turn 开始（AKS 含 user_message_id） |
| `thinking.delta` | `{text}` | 思考链增量 |
| `content.delta` | `{text}` | 正文增量 |
| `tool_call.started` | `{index?, id, name}` | 工具调用开始 |
| `tool_call.args_delta` | `{index?, id, text}` | 参数 JSON 增量 |
| `tool_call.completed` | `{id, name, args, result_brief, time_ms}` | 工具执行完成（brief 截断 200 字符） |
| `message.done` | `{message_id?, role, finish_reason, usage?, time_ms}` | 一条 assistant 消息完成 |
| `turn.done` | `{rounds, tool_calls_count, time_ms, ...}` | 一轮结束 |
| `usage` | `{input_tokens, output_tokens, total_tokens}` | token 用量（agent-server） |
| `error` | `{phase, error, cancelled?}` | 错误（cancelled=true 表示被 stop 取消） |
| `pong` | `{}` | 心跳应答 |

## 各端专属

| 帧 | 归属 | 说明 |
|---|---|---|
| `retrieval.started/progress/done`、`citation` | AKS | 知识库检索专属 |
| `tool.progress`、`tool_round.done` | AKS | AKS agent 模式工具执行细节 |
| `subagent.started/done`、`budget.warning/exceeded` | agent-server | M4 subagent 扇出与预算护栏透出 |

## 连接约定

- WS 端点：AKS `/api/chat/ws`、agent-server `/api/v1/chat/ws`（本地经 Caddy 网关 :4080，WS 透传零配置）
- 鉴权：握手 query `?token=<folio-auth RS256 JWT>`（浏览器 WS 无法带自定义 header）
- 模型清单：agent-server `GET /api/v1/models`（与 AKS `/api/chat/models` 同形；litellm / model_lake 双网关，环境变量同名——`MODEL_GATEWAY_TYPE` / `LITELLM_PROXY_URL` / `MODEL_LAKE_BASE` 等）
- 前端：`useKnowledgeChat` 泛化为 `useAgentChat(endpoint, handlers)`——事件分发表按后端注册（M2 前端侧待办）
