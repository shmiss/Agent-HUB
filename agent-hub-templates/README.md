# Agent Hub 配置模板

这组模板给未来分发用：

- 用户如果本机已经装了 Hermes / OpenClaw，可以直接在 UI 里“发现并导入”
- 如果希望批量发给别人，也可以把下面的 profile 片段拷到 `data/agent-hub.json` 的 `profiles` 数组里

> 注意：当前可直接执行 `openai-chat`、`openclaw-gateway-rpc`，并保留 `openclaw-cli` 作为兼容模式。
> `openclaw-gateway-rpc` 通过本机 Gateway WebSocket 调用 OpenClaw。
