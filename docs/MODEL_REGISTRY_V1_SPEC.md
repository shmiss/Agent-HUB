# Agent Hub Model Registry v1

日期：2026-05-06

## 背景

Agent Hub 当前接入 Hermes、OpenClaw、Claude Code，未来还会接 Codex、本地 OpenAI-compatible 网关和企业模型服务。每个 Agent 的模型体系不同：

- Hermes：前端模型通常是 `hermes-agent`，真实上游模型由 Hermes 配置管理。
- OpenClaw：模型跟 OpenClaw agent 配置绑定。
- Claude Code：模型通常是 `sonnet`、`opus`、`default` 等 CLI 参数。
- OpenAI-compatible：可通过 `/v1/models` 读取模型列表。

因此模型切换不能使用全局候选池。否则会把 Hermes、OpenClaw、Claude Code 的模型混在一起，误导用户。

## v1 已实现原则

### 1. 按 Agent 隔离模型候选

每个 Agent 使用独立 registry key：

```text
profile.id | adapter | endpoint
```

同一个 Agent 下读取到的模型，只属于这个 Agent。

### 2. 默认候选按 Agent 类型生成

- Hermes：当前 profile.model 或 `hermes-agent`
- Claude Code：`sonnet`、`opus`、`default`
- OpenClaw：当前 profile.model 或 `openclaw-agent`
- OpenAI-compatible：当前 profile.model，并支持读取 `/v1/models`

### 3. 快捷切换只修改当前 Agent

用户在输入框下方切模型时，只更新当前 active profile 的 `model` 字段，不影响其他 Agent。

### 4. 读取 `/models` 只对 HTTP Agent 生效

非 OpenAI-compatible HTTP 的 Agent 不读取 `/v1/models`，只允许用户自定义填写。

## v1 存储

前端 localStorage 中保存轻量 `modelRegistry`：

```json
{
  "profile-id|openai-chat|http://127.0.0.1:8642/v1": {
    "providerKey": "...",
    "agentId": "profile-id",
    "adapter": "openai-chat",
    "label": "Hermes 本机",
    "endpoint": "http://127.0.0.1:8642/v1",
    "models": ["hermes-agent"],
    "fetchedAt": 1778030000000,
    "source": "/v1/models"
  }
}
```

这里暂时不存 API Key，不写入服务端数据库。

## 下一步：Model Registry v2

v2 应该进入设置页，成为正式模型总控台。

建议新增：

```text
model_providers
models
agent_model_bindings
```

### model_providers

- id
- label
- provider_type
- base_url
- api_key_ref
- auth_mode
- created_at
- updated_at

### models

- id
- provider_id
- model_id
- display_name
- context_length
- supports_vision
- supports_tools
- cost_level
- latency_level
- best_for_json
- created_at
- updated_at

### agent_model_bindings

- agent_profile_id
- model_id
- default_for_task_type
- fallback_model_id

## 与 Task Router 的关系

后续 Task Router 不只推荐 Agent，还应推荐模型：

```text
任务类型 → Agent → Model → Context Strategy → Permission Policy
```

示例：

- 代码修改：Claude Code + sonnet + code strategy
- 高风险 review：Claude Code + opus + full strategy
- 普通方案：Hermes + 默认上游 + standard strategy
- 文件处理：OpenClaw + 当前 agent 模型 + file strategy

## 当前边界

v1 只是把模型候选隔离清楚，并提供当前 Agent 级别的模型快捷切换。

真正的模型治理，包括供应商、成本、能力、上下文长度、任务适配和 fallback，应放到 v2。

