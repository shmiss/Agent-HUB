from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import Callable

from context_engine import text_from_content


DEFAULT_AGENT_TIMEOUT = int(os.environ.get("AGENT_HUB_AGENT_TIMEOUT", "900") or "900")


class UnsupportedAgentAdapter(RuntimeError):
    def __init__(self, adapter: str, detail: str):
        super().__init__(detail)
        self.adapter = adapter
        self.detail = detail


@dataclass
class AgentTurnResult:
    response: dict
    run: dict


@dataclass
class AgentRuntime:
    headers: Callable[[str], dict]
    ensure_hermes_api_server: Callable[[dict, str], dict]
    run_cli_json: Callable[..., dict]
    openclaw_message_from_messages: Callable[[list], str]
    openclaw_result_to_text: Callable[[dict], str]
    openclaw_gateway_agent_turn: Callable[[dict, str, str, int], dict]
    openclaw_gateway_pool_status: Callable[[dict], dict]
    openclaw_gateway_url: Callable[[dict], str]
    openclaw_agent_configured: Callable[[str], bool]
    openclaw_gateway_port_from_config: Callable[[], int]
    is_port_open: Callable[[str, int, float], bool]


def chat_response(response_id: str, model: str, content: str, ui: dict) -> dict:
    return {
        "id": response_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "_ui": ui,
    }


def run_payload(session_id: str, agent_id: str, adapter: str, input_messages, output_text: str, latency_ms: int) -> dict:
    return {
        "session_id": session_id,
        "agent_id": agent_id,
        "adapter": adapter,
        "status": "success",
        "input_messages": input_messages,
        "output_text": output_text,
        "latency_ms": latency_ms,
    }


def last_user_text(messages) -> str:
    if not isinstance(messages, list):
        return ""
    last_user = next((m for m in reversed(messages) if isinstance(m, dict) and m.get("role") == "user"), None)
    return text_from_content(last_user.get("content")).strip() if last_user else ""


def system_text(messages) -> str:
    if not isinstance(messages, list):
        return ""
    system = next((m for m in messages if isinstance(m, dict) and m.get("role") == "system"), None)
    return text_from_content(system.get("content")).strip() if system else ""


def claude_result_to_text(payload: dict) -> str:
    if not isinstance(payload, dict):
        return str(payload or "")
    for key in ("result", "content", "response", "text", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(payload, ensure_ascii=False)


def health_check(active: dict, base: str, model: str, runtime: AgentRuntime, started: float | None = None) -> tuple[dict, int]:
    started = started or time.time()
    result = {
        "ok": True,
        "ui": "healthy",
        "baseUrl": base,
        "model": active.get("model") or model,
        "activeAgent": active,
    }
    adapter = active.get("adapter") or "openai-chat"

    if adapter in {"openclaw-cli", "openclaw-gateway-rpc"}:
        binary = active.get("binaryPath") or active.get("baseUrl") or which("openclaw") or "openclaw"
        binary_exists = bool(which(binary) or Path(binary).exists() or adapter == "openclaw-gateway-rpc")
        agent_id = active.get("agentId") or "main"
        agent_configured = runtime.openclaw_agent_configured(agent_id)
        gateway_port = runtime.openclaw_gateway_port_from_config()
        gateway_reachable = runtime.is_port_open("127.0.0.1", gateway_port, 0.2)
        result["ok"] = bool(binary_exists and agent_configured and (adapter != "openclaw-gateway-rpc" or gateway_reachable))
        result["openclaw"] = {
            "mode": "gateway-rpc" if adapter == "openclaw-gateway-rpc" else "cli-bridge",
            "binary": binary,
            "binaryExists": binary_exists,
            "agentId": agent_id,
            "agentConfigured": agent_configured,
            "gatewayPort": gateway_port,
            "gatewayReachable": gateway_reachable,
            "toolsProfile": active.get("toolsProfile") or "",
            "toolDeny": active.get("toolDeny") or [],
            "thinking": active.get("thinking") or os.environ.get("OPENCLAW_THINKING") or "medium",
        }
        if adapter == "openclaw-gateway-rpc":
            result["openclaw"]["rpcPool"] = runtime.openclaw_gateway_pool_status(active)
        result["hint"] = "OpenClaw Gateway RPC 已配置，已启用 WebSocket 连接复用。" if adapter == "openclaw-gateway-rpc" else "OpenClaw CLI bridge 已配置。右上角状态使用轻量检测，不再执行耗时的 `openclaw agents list --json`。"
        if not binary_exists:
            result["openclawError"] = f"OpenClaw binary not found: {binary}"
        elif not agent_configured:
            result["openclawError"] = f"OpenClaw agent not configured: {agent_id}"
        elif adapter == "openclaw-gateway-rpc" and not gateway_reachable:
            result["openclawError"] = f"OpenClaw Gateway not reachable on port {gateway_port}"
        result["latencyMs"] = int((time.time() - started) * 1000)
        return result, 200 if result["ok"] else 502

    if adapter == "claude-code-cli":
        binary = active.get("binaryPath") or active.get("baseUrl") or which("claude") or "claude"
        binary_exists = bool(which(binary) or Path(binary).exists())
        result["ok"] = binary_exists
        result["claude"] = {
            "binary": binary,
            "binaryExists": binary_exists,
            "mode": "print-json",
            "sessionPersistence": False,
        }
        result["hint"] = "Claude Code CLI 已配置，Agent Hub 将使用非交互 print 模式调用。"
        if not binary_exists:
            result["claudeError"] = f"Claude Code binary not found: {binary}"
        result["latencyMs"] = int((time.time() - started) * 1000)
        return result, 200 if result["ok"] else 502

    if adapter != "openai-chat":
        result["ok"] = False
        result["hint"] = "当前选中的 Agent 使用的是 OpenClaw Gateway / 非 HTTP 适配器。配置已可管理，但执行桥接适配需要下一阶段接入。"
        result["latencyMs"] = int((time.time() - started) * 1000)
        return result, 501

    autostart = runtime.ensure_hermes_api_server(active, base)
    if autostart.get("attempted"):
        result["hermesAutostart"] = autostart
    try:
        req = urllib.request.Request(base.replace("/v1", "") + "/health", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            result["hermesHealth"] = json.loads(r.read().decode("utf-8", "replace") or "{}")
    except Exception as e:
        result["ok"] = False
        result["hermesHealthError"] = str(e)
    result["latencyMs"] = int((time.time() - started) * 1000)
    return result, 200 if result["ok"] else 502


def execute_chat_turn(active: dict, body: dict, base: str, api_key: str, model: str, runtime: AgentRuntime, started: float | None = None) -> AgentTurnResult:
    started = started or time.time()
    adapter = active.get("adapter") or "openai-chat"
    input_messages = body.get("messages") or []

    if adapter == "openclaw-gateway-rpc":
        session_id = str(body.get("session_id") or f"hub-{int(time.time())}")
        message = runtime.openclaw_message_from_messages(body.get("messages"))
        agent_id = active.get("agentId") or "main"
        timeout_sec = int(body.get("timeout", 300) or 300)
        rpc_before = runtime.openclaw_gateway_pool_status(active)
        raw = runtime.openclaw_gateway_agent_turn(active, message or "你好", session_id, timeout_sec)
        rpc_after = runtime.openclaw_gateway_pool_status(active)
        reply = runtime.openclaw_result_to_text(raw)
        latency_ms = int((time.time() - started) * 1000)
        return AgentTurnResult(
            response=chat_response(
                f"chatcmpl-openclaw-rpc-{int(time.time() * 1000)}",
                active.get("model") or agent_id,
                reply,
                {
                    "latencyMs": latency_ms,
                    "agentLabel": active.get("label"),
                    "adapter": adapter,
                    "sessionId": session_id,
                    "agentId": agent_id,
                    "gatewayUrl": runtime.openclaw_gateway_url(active),
                    "rpcReused": bool(rpc_before.get("authenticated")),
                    "rpcPool": rpc_after,
                },
            ),
            run=run_payload(session_id, agent_id, adapter, input_messages, reply, latency_ms),
        )

    if adapter == "openclaw-cli":
        binary = active.get("binaryPath") or active.get("baseUrl") or which("openclaw") or "openclaw"
        session_id = str(body.get("session_id") or f"hub-{int(time.time())}")
        message = runtime.openclaw_message_from_messages(body.get("messages"))
        agent_id = active.get("agentId") or "main"
        timeout_sec = int(body.get("timeout", 300) or 300)
        command = [
            binary, "agent",
            "--agent", agent_id,
            "--session-id", session_id,
            "--message", message or "你好",
            "--json",
            "--timeout", str(timeout_sec),
        ]
        raw = runtime.run_cli_json(command, timeout=min(timeout_sec + 30, 900))
        reply = runtime.openclaw_result_to_text(raw)
        latency_ms = int((time.time() - started) * 1000)
        safe_command = [
            "openclaw", "agent",
            "--agent", agent_id,
            "--session-id", session_id,
            "--message", "<user-message>",
            "--json",
            "--timeout", str(timeout_sec),
        ]
        return AgentTurnResult(
            response=chat_response(
                f"chatcmpl-openclaw-{int(time.time() * 1000)}",
                active.get("model") or agent_id,
                reply,
                {
                    "latencyMs": latency_ms,
                    "agentLabel": active.get("label"),
                    "adapter": adapter,
                    "sessionId": session_id,
                    "agentId": agent_id,
                    "command": " ".join(safe_command),
                },
            ),
            run=run_payload(session_id, agent_id, adapter, input_messages, reply, latency_ms),
        )

    if adapter == "claude-code-cli":
        binary = active.get("binaryPath") or active.get("baseUrl") or which("claude") or "claude"
        session_id = str(body.get("session_id") or f"hub-{int(time.time())}")
        user_prompt = last_user_text(body.get("messages")) or "你好"
        sys_prompt = system_text(body.get("messages"))
        # Claude Code print mode is most reliable when task context is present
        # in the user prompt itself. Keep --system-prompt too, but duplicate the
        # compact Agent Hub package here so file paths from prior agents are not
        # missed when the current user message is short ("方案1", "继续", etc.).
        prompt = user_prompt
        if sys_prompt and "Agent Hub Context Package v1" in sys_prompt:
            prompt = f"{sys_prompt[:6200]}\n\n[用户当前请求]\n{user_prompt}".strip()
        model_name = active.get("model") or model or "sonnet"
        timeout_sec = int(body.get("timeout", 300) or 300)
        workspace_path = str(body.get("workspace_path") or "").strip()
        approval_policy = str(body.get("approval_policy") or "auto").strip()
        command = [
            binary,
            "--bare",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--no-session-persistence",
            "--model",
            model_name,
        ]
        if workspace_path and Path(workspace_path).exists():
            command.extend(["--add-dir", workspace_path])
        if approval_policy == "readonly":
            command.extend(["--permission-mode", "dontAsk", "--tools", "Read,Glob,Grep,LS"])
        elif approval_policy == "workspace-auto":
            command.extend(["--permission-mode", "acceptEdits"])
        elif approval_policy == "bypass":
            command.extend(["--allow-dangerously-skip-permissions", "--dangerously-skip-permissions"])
        else:
            command.extend(["--permission-mode", "auto"])
        if sys_prompt:
            command.extend(["--system-prompt", sys_prompt])
        raw = runtime.run_cli_json(command, timeout=min(timeout_sec + 30, 900), cwd=workspace_path if workspace_path and Path(workspace_path).exists() else None)
        reply = claude_result_to_text(raw)
        latency_ms = int((time.time() - started) * 1000)
        return AgentTurnResult(
            response=chat_response(
                f"chatcmpl-claude-code-{int(time.time() * 1000)}",
                model_name,
                reply,
                {
                    "latencyMs": latency_ms,
                    "agentLabel": active.get("label"),
                    "adapter": adapter,
                    "sessionId": session_id,
                    "agentId": active.get("agentId") or "claude-code",
                    "command": "claude --bare -p <user-message> --output-format json --no-session-persistence",
                    "approvalPolicy": approval_policy,
                    "workspacePath": workspace_path,
                },
            ),
            run=run_payload(session_id, active.get("agentId") or "claude-code", adapter, input_messages, reply, latency_ms),
        )

    if adapter != "openai-chat":
        raise UnsupportedAgentAdapter(
            adapter,
            "已检测到 OpenClaw Gateway，但当前版本还未接上 WebSocket -> Chat UI 的执行桥接层。建议先切回 Hermes profile。",
        )

    hermes_autostart = runtime.ensure_hermes_api_server(active, base)
    if not body.get("model"):
        body["model"] = model
    if "stream" not in body:
        body["stream"] = False
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + "/chat/completions", data=raw, headers=runtime.headers(api_key), method="POST")
    timeout_sec = int(body.get("timeout", DEFAULT_AGENT_TIMEOUT) or DEFAULT_AGENT_TIMEOUT)
    with urllib.request.urlopen(req, timeout=timeout_sec) as r:
        resp = json.loads(r.read().decode("utf-8", "replace"))
    latency_ms = int((time.time() - started) * 1000)
    resp.setdefault("_ui", {})["latencyMs"] = latency_ms
    resp["_ui"]["agentLabel"] = active.get("label")
    if hermes_autostart.get("attempted"):
        resp["_ui"]["hermesAutostart"] = hermes_autostart
    reply = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
    return AgentTurnResult(
        response=resp,
        run=run_payload(str(body.get("session_id") or ""), str(active.get("id") or model), adapter, input_messages, text_from_content(reply), latency_ms),
    )
