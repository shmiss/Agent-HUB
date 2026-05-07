# Agent Hub Task Router v1 技术方案

日期：2026-05-06

## 目标

Task Router v1 让 Agent Hub 从“用户手动切换 Agent”升级为“系统推荐最适合的 Agent，并解释原因”。

第一版坚持半自动：

```text
用户创建/输入任务
→ Agent Hub 识别任务类型
→ 推荐 Agent 和上下文策略
→ 用户确认
→ 执行
→ 记录结果
```

不做全自动多 Agent 协作。先把推荐、确认、执行、反馈闭环跑通。

## 设计原则

1. 可解释优先：推荐必须展示原因。
2. 本地优先：不依赖云端路由服务。
3. 确定性优先：v1 以规则为主，模型判断为后续增强。
4. 上下文分层：不同任务类型使用不同 Context Strategy。
5. 可反馈：每次执行成功/失败都能反哺后续推荐。
6. 安全默认：涉及文件修改、命令执行、网络访问时保留确认机制。

## 核心对象

### Task Spec

任务规格是 Router 的输入。

建议表：`task_specs`

字段：

- `id TEXT PRIMARY KEY`
- `session_id TEXT NOT NULL`
- `goal TEXT DEFAULT ''`
- `task_type TEXT DEFAULT 'chat'`
- `stage TEXT DEFAULT 'draft'`
- `constraints_json TEXT DEFAULT '[]'`
- `acceptance_json TEXT DEFAULT '[]'`
- `files_json TEXT DEFAULT '[]'`
- `risk_notes TEXT DEFAULT ''`
- `final_deliverable TEXT DEFAULT ''`
- `created_at INTEGER NOT NULL`
- `updated_at INTEGER NOT NULL`

### Agent Capability

Agent 能力画像是 Router 的候选集。

v1 不一定新建表，可以先由现有 profile 动态生成；v1.1 再持久化。

结构：

```json
{
  "agentId": "detected-hermes-local",
  "label": "Hermes 本机",
  "adapter": "openai-chat",
  "capabilities": ["analysis", "planning", "summary", "research"],
  "bestFor": ["方案分析", "任务拆解", "总结归档"],
  "avoidFor": ["直接修改代码", "长时间本地命令执行"],
  "contextStrategies": ["standard", "full"],
  "riskLevel": "low"
}
```

### Dispatch Recommendation

一次推荐结果。

建议表：`dispatch_recommendations`

字段：

- `id TEXT PRIMARY KEY`
- `session_id TEXT NOT NULL`
- `task_type TEXT NOT NULL`
- `primary_agent_id TEXT NOT NULL`
- `assistant_agent_ids_json TEXT DEFAULT '[]'`
- `context_strategy TEXT DEFAULT 'standard'`
- `confidence REAL DEFAULT 0.6`
- `reasons_json TEXT DEFAULT '[]'`
- `warnings_json TEXT DEFAULT '[]'`
- `accepted INTEGER DEFAULT 0`
- `created_at INTEGER NOT NULL`

## 任务类型识别

v1 支持 6 类：

| 类型 | 含义 | 默认 Agent 倾向 | 默认 Context Strategy |
|---|---|---|---|
| `chat` | 普通问答、方案分析 | Hermes | standard |
| `code` | 代码修改、调试、测试 | Claude Code | code |
| `file` | 本地文件、Word、Excel、PDF | OpenClaw | file |
| `review` | 审查、复核、QA | Claude Code / Hermes | full |
| `research` | 资料整理、外部调研 | Hermes | standard |
| `workflow` | 多步骤复杂任务 | Hermes 规划 + 执行 Agent | standard |

## v1 规则

### 输入特征

从以下来源提取：

- 最新用户消息；
- 会话标题；
- Task Goal；
- 附件类型；
- 文件路径；
- Workspace 状态；
- 最近 handoff；
- 最近 agent_runs。

### 规则示例

```text
命中 /Users/... 或 .docx/.xlsx/.pdf → file
命中 bug/报错/测试/代码/函数/接口/前端/后端 → code
命中 review/检查/复核/验收/QA → review
命中 调研/学习/搜索/资料/竞品 → research
命中 多步骤/完成这个项目/自动分配/最终交付 → workflow
否则 → chat
```

### Agent 分配示例

```text
file + OpenClaw 可用 → OpenClaw
code + Claude Code 可用 + workspace 已绑定 → Claude Code
code + 无 workspace → Hermes 先规划，并提示绑定工作区
workflow → Hermes 规划，辅助 Agent 选择 Claude Code/OpenClaw
review → Claude Code 优先；若无 workspace，则 Hermes
chat/research → Hermes
```

### 推荐理由

推荐结果必须包含用户可读理由：

```json
[
  "检测到当前任务包含代码修改关键词",
  "当前任务已绑定 Git 工作区",
  "Claude Code 适合读取项目并修改文件",
  "建议使用 code 上下文策略，传递工作区、相关文件和验收标准"
]
```

### 风险提示

```json
[
  "当前未绑定工作区，代码类任务可能无法定位文件",
  "该 Agent 可能需要文件写入权限",
  "当前 Hermes 上游模型近期出现 503，建议失败时切换备用 Agent"
]
```

## API 设计

### 1. 获取推荐

`POST /api/task-router/recommend`

请求：

```json
{
  "sessionId": "session-id",
  "message": "帮我修复这个 bug",
  "forceRefresh": false
}
```

响应：

```json
{
  "ok": true,
  "recommendation": {
    "id": "rec-id",
    "taskType": "code",
    "primaryAgentId": "detected-claude-code",
    "assistantAgentIds": ["detected-hermes-local"],
    "contextStrategy": "code",
    "confidence": 0.78,
    "reasons": [],
    "warnings": []
  }
}
```

### 2. 接受推荐

`POST /api/task-router/accept`

请求：

```json
{
  "sessionId": "session-id",
  "recommendationId": "rec-id"
}
```

效果：

- 更新 session active_agent_id；
- 更新 context_strategy；
- 标记 recommendation accepted；
- 写入 handoff 或 timeline 事件。

### 3. 保存 Task Spec

`POST /api/task-spec/save`

### 4. 获取 Task Spec

`GET /api/task-spec?sessionId=...`

## 前端设计

### 入口位置

放在顶部任务卡片内，做成紧凑推荐条：

```text
推荐：Claude Code · 代码任务 · 置信度 78%
[查看理由] [采用] [换一个]
```

展开后显示：

- 任务类型；
- 推荐 Agent；
- 辅助 Agent；
- 上下文策略；
- 推荐理由；
- 风险提示；
- 采用按钮。

### 状态

- `idle`: 暂无推荐；
- `analyzing`: 正在分析；
- `ready`: 有推荐；
- `accepted`: 已采用；
- `stale`: 推荐可能过期；
- `failed`: 推荐失败。

## 后端模块

新增：

```text
task_router.py
```

职责：

- `classify_task(...)`
- `build_agent_capabilities(...)`
- `recommend_agent(...)`
- `serialize_recommendation(...)`

扩展：

```text
storage.py
server.py
context_engine.py
```

## 测试计划

### 单元测试

新增 `tests/test_task_router.py`：

- 文件任务识别；
- 代码任务识别；
- review 任务识别；
- workflow 任务识别；
- Claude Code 不可用时 fallback；
- OpenClaw 不可用时 fallback；
- context strategy 匹配；
- 推荐理由不为空。

### API 测试

- `POST /api/task-router/recommend`
- `POST /api/task-router/accept`
- `GET /api/task-spec`
- `POST /api/task-spec/save`

### 前端 smoke

- 推荐条能渲染；
- 点击采用后当前 Agent 切换；
- context strategy 跟随变化；
- 风险提示可展开。

## 实施顺序

### 第一刀：纯后端 Router

- 新增 `task_router.py`
- 新增测试；
- 不改 UI。

### 第二刀：API 接入

- 新增 `/api/task-router/recommend`
- 新增 `/api/task-router/accept`
- 写入 SQLite。

### 第三刀：顶部任务卡推荐条

- 渲染推荐；
- 采用推荐；
- 展示理由和风险。

### 第四刀：Task Spec v1

- 存储 Task Spec；
- Context Package 注入 Task Spec；
- 顶部卡片展示目标和验收标准。

当前已完成：

- `/api/task-router/recommend` 自动生成并保存 Task Spec；
- `/api/task-spec/generate` 支持手动刷新；
- 顶部任务卡展示 Task Spec 摘要与展开详情；
- Context Package 注入 Task Spec，作为跨 Agent 接力的统一任务契约。

### 第五刀：Workflow × Router 联动

当前已完成：

- `/api/task-router/recommend` 支持传入 `workflowStage`；
- Task Router 会根据阶段调整推荐：
  - 规划阶段优先规划/分析型 Agent；
  - 执行阶段按 Task Spec 类型推荐执行 Agent；
  - 复核/测试阶段强制切到 Review 类型和 `full` 上下文；
  - 完成/归档阶段优先总结沉淀；
- `/api/task-workflow/transition` 切换阶段后自动返回新的推荐结果；
- 前端阶段切换后自动刷新推荐条。

## 验收标准

V0.7 完成时，应满足：

1. 用户输入“帮我修复这个项目的 bug”，系统推荐 Claude Code。
2. 用户输入“帮我重新排版这个 Word”，系统推荐 OpenClaw。
3. 用户输入“你先分析这个方案”，系统推荐 Hermes。
4. 用户输入“检查刚才改动有没有问题”，系统推荐 Review 类型。
5. 推荐结果有明确理由和风险提示。
6. 点击采用后当前 Agent 与 Context Strategy 自动切换。
7. 推荐、采用、执行结果都能在数据库中追踪。

## 后续演进

### V0.7.1

- 根据 agent_runs 成功率调整推荐分；
- 根据任务历史记录推荐；
- 增加用户手动反馈。

### V0.8

- Task Workflow Stage；
- Review / QA 阶段；
- 最终交付物归档。

### V0.9

- 多 Agent 半自动接力；
- Hermes 规划；
- Claude/OpenClaw 执行；
- Review Agent 验收；
- Agent Hub 总结交付。
