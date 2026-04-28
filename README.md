# Agent Hub

Agent Hub 是一个本地多 Agent 工作台，提供类 ChatGPT 的网页交互界面，并通过本地服务代理连接 Hermes、OpenClaw 或其他 OpenAI-compatible Agent。

## 特性

- 多 Agent Profile 管理与切换
- 自动发现本机 Hermes / OpenClaw 配置
- OpenAI-compatible Chat Completions 代理，避免浏览器 CORS 问题
- OpenClaw Gateway RPC WebSocket 直连（优先）
- OpenClaw CLI Bridge 兼容模式
- 图片 / 文本 / 代码附件上传
- 运行状态可视化、耗时展示
- 多会话本地保存与 Markdown 导出
- 运行配置保存在 `data/agent-hub.json`，默认已被 `.gitignore` 忽略

## 安装依赖

Python 3.9+：

```bash
python3 -m pip install -r requirements.txt
```

如果只使用 Hermes/OpenAI-compatible HTTP，可以不安装额外依赖；如果使用 OpenClaw Gateway RPC，需要：

- `websocket-client`
- `cryptography`
- 本机 OpenClaw Gateway 已启动并完成设备授权

## 启动

```bash
cd agent-hub
HERMES_BASE_URL=http://127.0.0.1:8642/v1 HERMES_MODEL=hermes-agent ./run.sh
```

打开：

```text
http://127.0.0.1:8765
```

## OpenClaw 连接方式

优先使用：

```text
adapter = openclaw-gateway-rpc
baseUrl = ws://127.0.0.1:18789
agentId = main
```

兼容模式：

```text
adapter = openclaw-cli
baseUrl = openclaw 或 /path/to/openclaw
agentId = main
```

## 配置文件

首次启动时会自动生成：

```text
data/agent-hub.json
```

仓库只提交示例：

```text
data/agent-hub.example.json
```

请不要提交真实的 token、API Key、私有路径或本地会话数据。

## 安全说明

- `data/agent-hub.json` 被 `.gitignore` 忽略
- `/data/*` 静态访问被后端禁止
- `/api/config` 和 `/api/agents/discover` 不返回 API Key
- 浏览器 localStorage 不持久化图片 base64，避免缓存膨胀

## 路线图

- Gateway RPC 流式事件透传
- Agent 运行过程细粒度事件展示
- 后端数据库保存会话
- SSO / OAuth
- 权限、部门、审计日志
- 附件服务端解析与安全扫描
- 模型路由、配额、成本统计
