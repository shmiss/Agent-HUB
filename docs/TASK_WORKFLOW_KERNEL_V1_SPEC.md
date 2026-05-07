# Task Workflow Kernel v1

更新时间：2026-05-06

## 目标

让 Agent Hub 不只管理聊天上下文，而是开始管理一个任务从设立到交付的生命周期。

## 阶段定义

| Stage | 中文 | 会话状态 | 说明 |
| --- | --- | --- | --- |
| `draft` | 草稿 | `todo` | 任务目标还不完整 |
| `planning` | 规划 | `active` | 确认任务规格、Agent 分工和上下文策略 |
| `executing` | 执行 | `active` | Agent 正在处理实际任务 |
| `review` | 复核 | `handoff` | 检查输出质量、风险和遗漏 |
| `testing` | 测试 | `active` | 运行测试、人工检查或文件验收 |
| `done` | 完成 | `done` | 任务已满足验收标准 |
| `archived` | 归档 | `done` | 任务只作为历史上下文和项目记忆使用 |

## API

### GET `/api/task-workflow?session_id=...`

返回：

- 当前阶段；
- 当前阶段文案；
- 可流转下一阶段；
- workflow 定义；
- 最近阶段切换事件。

### POST `/api/task-workflow/transition`

请求：

```json
{
  "sessionId": "session-id",
  "stage": "executing",
  "actor": "user",
  "note": "进入执行"
}
```

行为：

- 写入 `task_workflow_events`；
- 更新 `task_specs.stage`；
- 更新 `sessions.task_status`；
- 更新 `sessions.next_step`。

## 数据表

### `task_workflow_events`

| 字段 | 说明 |
| --- | --- |
| `id` | 事件 ID |
| `session_id` | 会话/任务 ID |
| `from_stage` | 原阶段 |
| `to_stage` | 新阶段 |
| `actor` | 操作者，默认 `user` |
| `note` | 切换说明 |
| `created_at` | 创建时间 |

## UI

顶部任务卡展示 Workflow 阶段条：

```text
草稿 → 规划 → 执行 → 复核 → 测试 → 完成 → 归档
```

用户可以点击允许流转的阶段，Agent Hub 会同步更新任务状态和下一步提示。

## 后续演进

- 阶段切换自动触发 Router 推荐；（已完成 v1）
- 阶段动作按钮；（已完成 v1）
- `executing` 阶段绑定 Agent Run；
- `review/testing` 阶段生成验收清单；
- `done` 阶段生成最终交付摘要；
- `archived` 阶段自动沉淀 Project Memory。

## Workflow × Task Router 联动 v1

阶段切换后，`/api/task-workflow/transition` 会同步返回新的 `recommendation`。

推荐策略：

| Stage | 推荐倾向 | 上下文策略 |
| --- | --- | --- |
| `draft` / `planning` | 规划、分析、总结型 Agent，例如 Hermes | `standard` |
| `executing` | 根据 Task Spec 类型推荐执行 Agent，例如 Claude Code / OpenClaw | `code` / `file` / `standard` |
| `review` / `testing` | Review / QA 型 Agent，例如 Claude Code 或 Hermes | `full` |
| `done` / `archived` | 总结沉淀型 Agent | `standard` |

前端行为：

- 用户点击阶段条；
- 后端更新 Workflow；
- 后端立刻计算阶段推荐；
- 顶部“智能任务调度”推荐条自动刷新；
- `下一步` 文案更新为推荐 Agent。

## 阶段动作按钮 v1

API：

```text
POST /api/task-workflow/stage-action
```

请求：

```json
{
  "sessionId": "session-id",
  "stage": "executing"
}
```

返回：

- 阶段动作标题；
- 推荐 Agent；
- 上下文策略；
- 可插入当前会话的 Markdown 内容。

阶段动作：

| Stage | 按钮 | 生成内容 |
| --- | --- | --- |
| `draft` | 澄清需求 | 需求澄清清单 |
| `planning` | 生成执行计划 | 执行计划草案 |
| `executing` | 生成执行指令 | Agent 执行指令包 |
| `review` | 生成 Review 清单 | Review Checklist |
| `testing` | 生成验收清单 | 测试 / 验收清单 |
| `done` | 生成交付摘要 | 最终交付摘要 |
| `archived` | 生成归档记忆 | 归档与项目记忆草案 |

前端行为：

- 点击 Workflow 右侧阶段动作按钮；
- Agent Hub 本地生成阶段动作；
- 作为一条 `Agent Hub 阶段动作` 消息插入当前任务；
- 刷新推荐条；
- 更新下一步提示。

## 发送给推荐 Agent v1

阶段动作生成后，Workflow 条会出现：

```text
发送给推荐 Agent
```

行为：

1. 自动采用当前推荐；
2. 如果推荐 Agent 与当前 Agent 不同，复用 handoff 流程生成交接摘要；
3. 将阶段动作内容放入输入框；
4. 触发当前发送流程；
5. 执行结果继续进入 Agent Run Timeline / Debug Panel。

边界：

- 当前版本发送的是完整阶段动作 Markdown；
- 仍遵守当前 Agent 的执行权限策略和工作区绑定；
- 如果正在运行任务，按钮不会重复发送。
