# Agent Hub 产品时间线与发展路线

日期：2026-05-06  
开发目录：`agent-hub`  
开源同步目录：`agent-hub`

## 当前定位

Agent Hub 是一个本地优先的多 Agent 任务工作台。

它不是单纯聊天 UI，也不是某个 Agent 的壳，而是要成为：

> 本地 Agent 的任务调度与交付中枢。

核心链路：

```text
理解任务
→ 选择 Agent
→ 传递上下文
→ 监督执行
→ 验收结果
→ 沉淀记忆
```

## 阶段时间线

### 2026-04-28：Hermes 桌面交互原型

起点问题：

- Hermes 主要在终端交互；
- 浏览器直连 Hermes API 有 CORS 问题；
- 用户看不到长任务执行过程；
- 普通用户不适合直接使用终端 Agent。

完成：

- 本地网页 UI；
- Hermes API Server 代理；
- ChatGPT 风格输入框；
- 基础消息发送和展示；
- 本地服务启动方式。

状态判断：

> 这是 Hermes 的桌面交互壳，能用，但还不是独立产品。

### 2026-04-28 至 2026-04-29：Agent Hub 雏形

产品转向：

- 产品名称从 Hermes 前端升级为 Agent Hub；
- 开始支持多 Agent；
- 目标从“让 Hermes 有界面”转向“管理本地 Agent”。

完成：

- Agent Registry；
- Hermes 自动发现；
- OpenClaw 自动发现；
- OpenClaw Gateway RPC / CLI 接入方向；
- Agent 快速切换；
- 设置页；
- 左侧任务列表；
- 顶部任务卡；
- 运行状态展示。

关键判断：

> Agent Hub 开始从单 Agent 前端，变成本地 Agent 管理器。

### 2026-04-29 至 2026-04-30：工程收敛

目标：

让项目从“能跑”变成“能持续迭代”。

完成：

- 前端拆出 `api.js`、`agents.js`、`state.js`、`context.js`、`render-chat.js`、`render-task-list.js`、`render-task-detail.js`；
- 后端拆出 `storage.py`、`context_engine.py`、`agent_adapters.py`；
- 增加 `docs/AGENT_ADAPTER_CONTRACT.md`；
- 增加 Python 单元测试；
- 增加前端 smoke 测试；
- 增加 `release.sh`；
- 增加敏感信息扫描；
- 同步脱敏开源目录 `agent-hub`。

关键判断：

> Agent Hub 具备了继续开发的工程骨架。

### 2026-05-01 至 2026-05-03：上下文中枢

目标：

让 Agent Hub 不只是转发消息，而是拥有任务上下文。

完成：

- SQLite 会话存储；
- 任务卡片列表；
- Agent Run 执行记录；
- Handoff Timeline；
- Context Package v1；
- Context Strategy v1；
- 附件固定到上下文；
- Word / Excel / PDF 附件解析；
- Project Workspace 绑定；
- 项目路径和 Git 状态检测；
- Claude Code 权限策略；
- Project Memory v1。

关键判断：

> Agent Hub 开始拥有自己的产品壁垒：上下文、交接、记忆和执行记录。

### 2026-05-04 至 2026-05-05：本地工作台成型

目标：

让个人和小团队可以真实使用。

完成：

- 顶部 Task Command Center；
- 顶部卡片支持折叠、展开、缩放、冻结；
- 左侧任务卡片紧凑化；
- 会话重命名；
- 会话删除；
- 主聊天角色头像区分；
- OpenClaw / Hermes / Claude Code 图标和 Agent 角色识别；
- Claude Code 工作区执行和权限策略；
- Handoff Timeline 正式化；
- Context Package 中关键文件路径提取；
- 工作区卡片进入顶部任务区。

关键判断：

> Agent Hub 已经从 Demo 进入内测可用状态，适合边用边开发。

### 2026-05-06：任务调度中枢启动

目标：

从“用户手动选择 Agent”升级为“系统理解任务并推荐 Agent”。

完成：

- 开源项目拆解报告：`research/agent-dispatcher-open-source-study-2026-05-06.md`；
- `docs/TASK_ROUTER_V1_SPEC.md`；
- `task_router.py`；
- `task_specs`；
- `dispatch_recommendations`；
- `/api/task-router/recommend`；
- `/api/task-router/accept`；
- `/api/task-spec`；
- `/api/task-spec/save`；
- 顶部智能任务调度条；
- 推荐 Agent、任务类型、上下文策略、理由和风险；
- 一键采用推荐；
- 推荐结果进入测试覆盖。

支持任务类型：

- `chat`：普通问答 / 方案分析；
- `code`：代码修改 / 调试 / 测试；
- `file`：本地文件 / Office / PDF；
- `review`：审查 / 复核 / QA；
- `research`：资料整理 / 调研；
- `workflow`：多步骤复杂任务。

关键判断：

> Agent Hub 正式开始构建“本地任务调度中枢”的核心能力。

### 2026-05-06：模型管理补齐

起点问题：

- 不同 Agent 使用不同模型；
- 原模型切换下拉把所有模型混在一起；
- 这会误导用户，也不符合未来模型治理方向。

完成：

- 输入框模型下拉改为按 Agent 隔离；
- 每个 Agent 拥有独立模型候选；
- Claude Code 默认候选：`sonnet`、`opus`、`default`；
- Hermes 默认候选：`hermes-agent`；
- OpenClaw 默认候选：当前 profile model；
- OpenAI-compatible Agent 支持读取 `/v1/models`；
- 设置页新增 `Model Registry`；
- 每个 Agent 一张模型卡；
- 支持在总控台为单个 Agent 切换、读取、添加模型；
- `docs/MODEL_REGISTRY_V1_SPEC.md`。

关键判断：

> 模型管理从“小下拉”升级为 Agent Hub 的模型总控雏形。

### 2026-05-06：任务停止能力

起点问题：

- 本地 Agent 可能执行很久；
- 用户在执行中不能停止任务；
- 这会让产品缺少控制感，也影响企业内部使用的安全边界。

完成：

- 运行状态条增加“停止”按钮；
- 前端可 abort 当前流式请求；
- 新增 `/api/runs/cancel`；
- 服务端维护运行取消标记；
- SSE bridge 支持 `cancelled` 事件；
- CLI 类 Agent 通过 cancelable runner 终止子进程；
- Agent Run 写入 `cancelled` 记录；
- Adapter 合约补充 Cancellation 约定。

当前边界：

- CLI 子进程可尽量硬停止；
- HTTP 上游如果没有取消协议，Agent Hub 会停止等待并忽略后续结果，但上游请求可能仍在服务端完成。

关键判断：

> 停止能力是任务调度中枢的基础控制面，后续多 Agent 协作必须以此为前提。

### 2026-05-06：Run Debug Panel v1

起点问题：

- Agent 执行失败时只能看到“调用失败”；
- 无法快速判断是 Agent、模型、权限、上下文、文件路径还是上游服务问题；
- 多 Agent 调度如果没有执行黑匣子，后续难以可靠自动协作。

完成：

- `agent_runs` 增加 `debug_json`；
- 记录 run id、模型、baseUrl、adapter、agent profile、工作区、权限、context strategy、messages 快照；
- 记录 Context Package snapshot；
- Agent Run Timeline 增加“详情 / 重试 / 复制上下文”；
- 新增 Run Debug Panel 弹窗；
- 支持复制完整调试信息；
- 支持用当前最新上下文重试，并恢复上次 Agent / 模型 / context strategy。

关键判断：

> Run Debug Panel 是 Agent Hub 从“能调度”走向“可靠调度”的黑匣子能力。

### 2026-05-06：Task Spec v1 自动生成与注入

起点问题：

- 任务调度只有“推荐哪个 Agent”，但缺少稳定的任务契约；
- Agent 接力时容易只传聊天摘要，缺少目标、约束、验收标准和相关文件；
- 后续 Workflow Kernel 需要一个可被执行、验收、复盘的任务规格。

完成：

- 新增本地启发式 Task Spec 生成能力；
- `/api/task-router/recommend` 在推荐 Agent 时自动生成并保存 Task Spec；
- 新增 `/api/task-spec/generate`，支持用户手动刷新任务书；
- 顶部任务卡展示 Task Spec，包含目标、类型/阶段、最终交付、验收标准、约束、相关文件和风险；
- Context Package v1 注入 Task Spec，让 Hermes / OpenClaw / Claude Code 接力时能拿到统一任务契约；
- 增加 Task Spec 和 Context Package 测试。

关键判断：

> Task Spec 是 Agent Hub 从“聊天上下文管理”走向“任务交付管理”的第一层协议。

### 2026-05-06：Task Workflow Kernel v1

起点问题：

- 有 Task Spec 以后，还缺少任务从“想法”到“交付”的阶段控制；
- 多 Agent 接力不能只靠聊天推进，需要可追踪的阶段流转；
- 后续自动调度需要知道当前任务到底是在规划、执行、复核、测试还是归档。

完成：

- 新增 `task_workflow.py`，定义统一阶段、阶段文案、会话状态映射和允许流转；
- 新增 `task_workflow_events` 表，记录每次阶段切换；
- 新增 `/api/task-workflow` 查询当前任务流程；
- 新增 `/api/task-workflow/transition` 切换任务阶段；
- 阶段切换会同步更新 `task_specs.stage`、`sessions.task_status` 和 `sessions.next_step`；
- 顶部任务卡新增 Workflow 阶段条：草稿、规划、执行、复核、测试、完成、归档；
- 增加 workflow 存储测试和前端 smoke 校验。

关键判断：

> Workflow Kernel 是后续“自动分配 Agent、监督执行、验收交付”的控制骨架。

### 2026-05-06：Workflow × Task Router 联动 v1

起点问题：

- Task Router 能按任务内容推荐 Agent，但还不知道任务当前处于哪个阶段；
- Workflow 能流转阶段，但阶段切换后还需要用户自己判断下一步该用哪个 Agent；
- 真正的任务调度中枢，需要把“当前阶段”纳入推荐逻辑。

完成：

- Task Router 增加 `workflow_stage` / `task_spec` 输入；
- 不同阶段自动调整推荐策略：
  - `draft/planning`：优先 Hermes 等规划/分析型 Agent；
  - `executing`：按 Task Spec 类型推荐 Claude Code / OpenClaw 等执行型 Agent；
  - `review/testing`：强制进入 Review 任务类型，使用 `full` 上下文；
  - `done/archived`：优先总结沉淀型 Agent；
- `/api/task-router/recommend` 支持传入 `workflowStage`；
- `/api/task-workflow/transition` 阶段切换后自动返回新的 Router 推荐；
- 前端阶段切换后自动刷新顶部“智能任务调度”推荐条；
- 下一步提示会变成“已进入某阶段，推荐由某 Agent 继续处理”；
- 增加阶段推荐单测和前端 smoke 校验。

关键判断：

> 这一步开始让 Agent Hub 从“用户手动切 Agent”走向“系统按任务阶段推荐 Agent”。

### 2026-05-06：阶段动作按钮 v1

起点问题：

- Workflow 已能切阶段，Router 已能按阶段推荐 Agent；
- 但用户进入某个阶段后，还需要自己组织下一步操作；
- 任务调度中枢应该把“当前阶段该做什么”直接变成可执行动作。

完成：

- 新增 `/api/task-workflow/stage-action`；
- 新增本地阶段动作生成器，不依赖上游模型；
- 顶部 Workflow 条增加阶段动作按钮；
- 不同阶段生成不同内容：
  - 草稿：需求澄清清单；
  - 规划：执行计划草案；
  - 执行：Agent 执行指令包；
  - 复核：Review Checklist；
  - 测试：测试 / 验收清单；
  - 完成：最终交付摘要；
  - 归档：归档与项目记忆草案；
- 点击后会把阶段动作作为 Agent Hub 消息插入当前任务；
- 同时刷新推荐信息，并更新下一步提示。

关键判断：

> 阶段动作按钮让 Workflow 不再只是状态展示，而开始变成可推动任务前进的操作面板。

### 2026-05-06：发送给推荐 Agent v1

起点问题：

- 阶段动作已经能生成执行指令、Review 清单和验收清单；
- 但用户还需要手动复制内容、切换 Agent、粘贴发送；
- 要形成真正的调度闭环，阶段动作应能直接交给推荐 Agent。

完成：

- 阶段动作生成后，Workflow 条显示“发送给推荐 Agent”按钮；
- 点击后自动采用当前推荐；
- 如推荐 Agent 与当前 Agent 不同，会走原有 handoff 流程；
- 自动把阶段动作内容填入输入框并发送；
- 发送后复用现有 Run Timeline、Context Package、停止任务和 Debug Panel 能力；
- `/api/task-workflow/stage-action` 会把本次阶段推荐写入推荐记录，保证后续可采用。

关键判断：

> 这一步打通了“阶段 → 动作 → 推荐 Agent → 执行”的最小闭环。

### 2026-05-07：长任务超时治理 v1

起点问题：

- 文件重设计、Excel 排序、网上资料收集这类复合任务容易超过 300 秒；
- 原生流式请求超时后会再 fallback 到普通响应，导致同一轮任务又等一次，体验上像重复失败；
- 用户只看到 `timed out`，不知道是上游慢、任务过大还是前端问题。

完成：

- 将 Agent Hub 默认 Agent 等待时间从 300 秒提升到 900 秒；
- 前端发送 payload 显式携带 `timeout=900`；
- 服务端 OpenAI-compatible stream / non-stream / responses 都读取该 timeout；
- 原生 stream 如果已经超时，不再自动 fallback 到 non-stream 重跑一次；
- 超时错误文案改成可执行建议：拆分 PDF 设计、Excel 排序、网上收集，或切换 OpenClaw / Claude Code 长任务 Agent。

关键判断：

> 长任务不是简单“调用失败”，需要产品层把任务拆分、Agent 路由和超时策略结合起来。

### 2026-05-07：Agent Run × Workflow 自动联动 v1

起点问题：

- Agent Run Timeline 已经能记录成功、失败、取消；
- Workflow 已经有阶段，但还需要用户手动判断下一阶段；
- 调度中枢应该根据执行结果自动建议进入 Review / Testing / Done，失败时回到执行修复。

完成：

- 新增前端 `autoLinkWorkflowAfterRun`；
- Agent Run 成功后按阶段自动推进：
  - `draft → planning`
  - `planning → executing`
  - `executing → review`
  - `review → testing`
  - `testing → done`
- Agent Run 失败或取消后，自动保持/回退到执行修复阶段，并标记任务为阻塞；
- 阶段联动会调用 `/api/task-workflow/transition`，因此会同步刷新 Router 推荐；
- 流式成功、普通成功、失败、取消都接入该联动；
- 前端 smoke 增加自动联动校验。

关键判断：

> 这一步让 Agent Hub 开始具备“监督执行结果并推动下一阶段”的调度能力。

## 当前版本状态

当前可定义为：

> V0.7 内测版：本地任务工作台 + 初版任务调度 + 初版模型总控。

当前核心能力：

- 本地多 Agent 接入；
- Hermes / OpenClaw / Claude Code；
- Agent Registry；
- Model Registry v1；
- SQLite 会话；
- 任务卡片；
- 工作区绑定；
- Context Package；
- Context Strategy；
- Project Memory；
- Handoff Timeline；
- Agent Run Timeline；
- Task Router v1；
- Task Spec v1；
- Task Workflow Kernel v1；
- Workflow × Task Router 联动 v1；
- 阶段动作按钮 v1；
- 发送给推荐 Agent v1；
- 执行中停止任务；
- Run Debug Panel；
- 附件解析；
- 运行状态可视化；
- 敏感信息扫描与开源同步。

## 产品价值判断

### 对个人

- 不需要在多个终端和工具间切换；
- 任务上下文不容易丢；
- 可以让不同 Agent 接力；
- 能看见执行过程和失败原因；
- 本地文件、本地代码、本地记忆更可控。

### 对小团队

- 把个人 Agent 使用经验沉淀为团队任务流程；
- 统一 Agent 接入方式；
- 降低新成员接手任务成本；
- 每个任务有历史、上下文、执行记录、交接记录。

### 对企业内部个人和小团队

- Local-first；
- 可审计；
- 可控权限；
- 适合内部代码、客户资料、项目文件；
- 比重型企业平台更容易试点。

## 技术资产

### 前端

- `app.js`：主交互调度；
- `api.js`：统一 API 调用；
- `agents.js`：Agent Profile 与能力标签；
- `state.js`：本地 UI 偏好和轻量缓存；
- `context.js`：前端上下文处理；
- `render-chat.js`：消息渲染；
- `render-task-list.js`：任务列表；
- `render-task-detail.js`：任务详情、Task Command Center、智能推荐条。

### 后端

- `server.py`：HTTP 入口；
- `storage.py`：SQLite；
- `context_engine.py`：上下文包、摘要、文件路径提取；
- `agent_adapters.py`：Hermes / OpenClaw / Claude Code 执行适配；
- `task_router.py`：任务识别与 Agent 推荐。

### 文档

- `AGENT_ADAPTER_CONTRACT.md`
- `V0.5_DEV_PLAN.md`
- `TASK_ROUTER_V1_SPEC.md`
- `MODEL_REGISTRY_V1_SPEC.md`
- `PRODUCT_TIMELINE_AND_ROADMAP.md`

## 后续路线

### V0.7.1：Task Router 完善

目标：

让任务推荐更稳定、更可解释。

计划：

- 推荐结果持久化展示；
- 根据 Agent Run 成功/失败调整推荐权重；
- 用户可对推荐结果做反馈；
- Task Spec 自动生成；
- 推荐模型和权限策略；
- 推荐后自动生成下一步计划。

### V0.8：Task Workflow Kernel

目标：

让每个任务从聊天记录升级为流程对象。

计划：

- 需求澄清；
- 方案设计；
- 任务拆解；
- 执行；
- Review；
- QA；
- 归档；
- 最终交付摘要。

### V0.9：半自动多 Agent 接力

目标：

用户设立任务后，Agent Hub 自动拆解并推荐执行链路。

计划：

- Hermes 规划；
- Claude Code / OpenClaw 执行；
- Hermes / Claude Review；
- Agent Hub 汇总；
- 用户关键节点确认；
- 失败后切换备选 Agent；
- 交接上下文按 Agent 定制。

### V1.0：本地 Agent Delivery Hub

目标：

个人和小团队可稳定使用、可开源分发。

计划：

- 安装向导；
- 首次扫描引导；
- Agent 模板导入；
- Model Provider 管理；
- 错误诊断页；
- 日志打包；
- 配置导入/导出；
- 桌面化封装；
- 开源文档完善。

## 开发原则

1. 每一版都要增强 Agent Hub 自己的中枢能力。
2. 不做单纯 UI 套壳。
3. 不盲目堆 Agent 数量。
4. 不做黑盒自动派工，推荐必须可解释。
5. 本地文件、权限、上下文、执行记录必须可控。
6. 任何开源同步必须经过敏感信息扫描。
7. 每次重要开发后必须更新文档。

## 当前验证标准

每次开发后执行：

```bash
cd agent-hub
node --check *.js
python3 -m py_compile *.py
python3 -m unittest discover -s tests
node tests/smoke_frontend.js

cd agent-hub
./release.sh
```

服务重启：

```bash
PID=$(lsof -tiTCP:8765 -sTCP:LISTEN || true)
if [ -n "$PID" ]; then kill $PID; sleep 1; fi
cd agent-hub
nohup env HERMES_BASE_URL=http://127.0.0.1:8642/v1 HERMES_MODEL=hermes-agent ./run.sh >/tmp/agent-hub-ui.log 2>&1 &
```
