# Agent Adapter Contract

This document defines the internal contract for Agent Hub adapters.

The goal is to keep `server.py` independent from agent-specific protocols. Each adapter can use HTTP, WebSocket, CLI, or another local bridge, but it must return the same internal shapes to Agent Hub.

## Supported Adapter IDs

- `openai-chat`: OpenAI-compatible Chat Completions HTTP.
- `openclaw-gateway-rpc`: OpenClaw Gateway RPC over WebSocket.
- `openclaw-cli`: OpenClaw CLI bridge.
- `claude-code-cli`: Claude Code CLI print mode.

Future adapters should use stable IDs such as:

- `codex-cli`
- `custom-http`

## Cancellation

Agent Hub exposes `POST /api/runs/cancel` as the user-facing stop signal.

Current behavior:

- Browser streaming requests are aborted immediately.
- Agent Hub records a `cancelled` run event for the session.
- SSE bridge checks the cancel flag and emits `event: cancelled`.
- CLI-backed adapters are executed through the server runtime's cancelable CLI runner; when possible, the child process is terminated.
- HTTP streaming adapters stop the browser wait immediately; the upstream HTTP request may continue if the upstream API does not support hard cancellation.

Adapter expectation:

- Future adapters should support a real `cancel(session_id, run_id)` capability when their underlying protocol exposes cancellation.
- Adapters must treat cancelled results as non-deliverable: Agent Hub should not append late output to the chat after the user stops a task.

## Profile Input

Every adapter receives an `active` profile dictionary.

Required fields:

- `id`: stable local profile id.
- `label`: user-facing name.
- `adapter`: adapter id.
- `type`: broad agent family, such as `hermes`, `openclaw`, `codex`, or `custom`.

Common optional fields:

- `baseUrl`: HTTP base URL, WebSocket URL, or local binary alias.
- `model`: model or agent model label.
- `apiKey`: optional secret used only at runtime.
- `agentId`: agent/session target for local agent systems.
- `binaryPath`: local CLI path.
- `supportsStream`: whether the adapter can stream.
- `supportsVision`: whether the adapter can receive image inputs.

## Chat Turn Input

`execute_chat_turn(active, body, base, api_key, model, runtime, started=None)`

The `body` should follow OpenAI Chat Completions style where possible:

- `model`
- `messages`
- `stream`
- `session_id`
- `timeout`

Agent Hub prepares context before calling the adapter. Adapters should not decide which session history to include.

## Chat Turn Output

Adapters return `AgentTurnResult`.

`response` must be OpenAI-compatible enough for the frontend:

```json
{
  "id": "chatcmpl-example",
  "object": "chat.completion",
  "created": 0,
  "model": "agent-model",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "reply text"
      },
      "finish_reason": "stop"
    }
  ],
  "_ui": {
    "latencyMs": 0,
    "agentLabel": "Agent name",
    "adapter": "adapter-id"
  }
}
```

`run` must be suitable for `agent_runs` persistence:

```json
{
  "session_id": "session-id",
  "agent_id": "agent-id",
  "adapter": "adapter-id",
  "status": "success",
  "input_messages": [],
  "output_text": "reply text",
  "latency_ms": 0
}
```

## Health Input

`health_check(active, base, model, runtime, started=None)`

Health checks should be lightweight. They should not execute a real task, modify remote state, or create a new long-running session.

## Health Output

Health checks return `(result, http_status)`.

Required result fields:

- `ok`: boolean.
- `ui`: usually `healthy`.
- `baseUrl`: target URL or bridge address.
- `model`: model or agent model label.
- `activeAgent`: the active profile.
- `latencyMs`: integer.

Recommended adapter-specific blocks:

- `openclaw`: OpenClaw bridge state.
- `claude`: Claude Code CLI state.
- `hermesHealth`: Hermes API health response.
- `hint`: short user-facing diagnostic hint.
- `openclawError`, `claudeError`, or `hermesHealthError`: error detail when `ok` is false.

HTTP status rules:

- `200`: adapter is usable.
- `501`: adapter profile exists but execution is not implemented.
- `502`: configured adapter is implemented but currently unhealthy.

## Error Rules

Unsupported adapter execution should raise `UnsupportedAgentAdapter`.

Runtime failures from HTTP, WebSocket, or CLI may bubble up to `server.py`, where they are converted to API errors and recorded as failed runs when a session id is available.

Adapters must avoid returning secrets in `_ui`, health results, or run records.

## Runtime Dependency Injection

`AgentRuntime` provides side-effectful functions to adapters:

- HTTP headers
- Hermes autostart
- CLI execution
- OpenClaw message conversion
- OpenClaw result conversion
- OpenClaw Gateway RPC call
- OpenClaw RPC pool status
- OpenClaw gateway URL
- OpenClaw agent configured check
- OpenClaw gateway port lookup
- local port probe

Claude Code currently uses `claude --bare -p <prompt> --output-format json --no-session-persistence`. Agent Hub owns session context, so Claude's own CLI session persistence is disabled by default. `--bare` prevents Claude Code from automatically loading large local project context; explicit Agent Hub context is passed through the prompt contract instead.

Adapters should use `AgentRuntime` instead of importing server functions directly. This keeps adapter tests isolated and makes future desktop packaging safer.

## Adding A New Adapter

1. Add a stable adapter id.
2. Add chat execution branch in `execute_chat_turn`.
3. Add lightweight health branch in `health_check`.
4. Return the common response and run shapes.
5. Add tests for success, health, and unsupported or failure behavior.
6. Update this contract document.
