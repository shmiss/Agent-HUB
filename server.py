#!/usr/bin/env python3
from __future__ import annotations

import json
import mimetypes
import os
import base64
import io
import queue
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from shutil import which
from urllib.parse import parse_qs, urlparse

import agent_adapters
import storage
import task_router
import task_workflow
from context_engine import text_from_content, truncate_text
from storage import accept_dispatch_recommendation, compact_context_messages, create_handoff, delete_project_memory, extract_project_memories, get_session, load_agent_runs, load_context_package, load_handoffs, load_project_memories, load_sessions_from_db, load_task_spec, load_task_workflow, record_agent_run, record_dispatch_recommendation, save_project_memory, save_sessions_to_db, save_task_spec, session_artifact_context, transition_task_stage

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("AGENT_HUB_DATA_DIR", str(ROOT / "data"))).expanduser()
CONFIG_FILE = DATA_DIR / "agent-hub.json"
DB_FILE = DATA_DIR / "agent-hub.db"
storage.configure(DATA_DIR, DB_FILE)
PORT = int(os.environ.get("HERMES_UI_PORT", "8765"))
HOST = os.environ.get("HERMES_UI_HOST", "127.0.0.1")
DEFAULT_HERMES_BASE = os.environ.get("HERMES_BASE_URL", "http://127.0.0.1:8642/v1").rstrip("/")
DEFAULT_MODEL = os.environ.get("HERMES_MODEL", "hermes-agent")
DEFAULT_AGENT_TIMEOUT = int(os.environ.get("AGENT_HUB_AGENT_TIMEOUT", "900") or "900")
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
RUN_CANCEL_FLAGS: set[str] = set()


def run_key(session_id: str, run_id: str = "") -> str:
    return f"{session_id or ''}:{run_id or ''}"


def is_cancelled(session_id: str, run_id: str = "") -> bool:
    return run_key(session_id, run_id) in RUN_CANCEL_FLAGS or run_key(session_id, "") in RUN_CANCEL_FLAGS


def clear_cancel(session_id: str, run_id: str = ""):
    RUN_CANCEL_FLAGS.discard(run_key(session_id, run_id))
    if run_id:
        RUN_CANCEL_FLAGS.discard(run_key(session_id, ""))


def run_debug_snapshot(
    *,
    active: dict,
    model: str,
    base: str,
    body: dict,
    input_messages: list,
    started: float,
    context_package_text: str = "",
    error_detail: str = "",
) -> dict:
    return {
        "runId": str(body.get("run_id") or ""),
        "model": model,
        "baseUrl": base,
        "adapter": active.get("adapter") or "",
        "agentProfileId": active.get("id") or "",
        "agentLabel": active.get("label") or "",
        "agentId": active.get("agentId") or "",
        "workspacePath": str(body.get("workspace_path") or ""),
        "approvalPolicy": str(body.get("approval_policy") or ""),
        "contextStrategy": str(body.get("context_strategy") or ""),
        "stream": bool(body.get("stream")),
        "bridge": bool(body.get("bridge")),
        "timeout": int(body.get("timeout") or 0),
        "startedAt": int(started * 1000),
        "messageCount": len(input_messages or []),
        "messages": [
            {
                "role": str(m.get("role") or ""),
                "text": truncate_text(text_from_content(m.get("content")), 1200),
            }
            for m in (input_messages or [])
            if isinstance(m, dict)
        ],
        "contextPackageText": truncate_text(context_package_text, 8000),
        "errorDetail": truncate_text(error_detail, 3000),
    }


def read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def is_port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def local_port_from_url(url: str) -> tuple[str, int] | None:
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return ("127.0.0.1" if host in {"localhost", "::1"} else host, int(port))
    except Exception:
        return None


def inspect_workspace_path(raw_path: str) -> dict:
    path_text = str(raw_path or "").strip()
    if path_text.startswith("~"):
        path_text = str(Path(path_text).expanduser())
    path = Path(path_text).resolve() if path_text else Path("")
    exists = bool(path_text and path.exists())
    is_dir = exists and path.is_dir()
    result = {
        "path": str(path) if path_text else "",
        "name": path.name if path_text else "",
        "exists": exists,
        "isDir": is_dir,
        "isGitRepo": False,
        "gitBranch": "",
        "gitDirty": False,
        "checkedAt": int(time.time() * 1000),
        "error": "" if exists and is_dir else ("路径不存在" if path_text and not exists else ("不是目录" if path_text else "缺少路径")),
    }
    if not is_dir:
        return result
    try:
        inside = subprocess.run(["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, timeout=3)
        if inside.returncode == 0 and "true" in (inside.stdout or "").lower():
            result["isGitRepo"] = True
            branch = subprocess.run(["git", "-C", str(path), "branch", "--show-current"], capture_output=True, text=True, timeout=3)
            result["gitBranch"] = (branch.stdout or "").strip() if branch.returncode == 0 else ""
            status = subprocess.run(["git", "-C", str(path), "status", "--porcelain"], capture_output=True, text=True, timeout=5)
            result["gitDirty"] = bool((status.stdout or "").strip()) if status.returncode == 0 else False
    except Exception as exc:
        result["error"] = f"Git 检查失败：{exc}"
    return result


def ensure_hermes_api_server(profile: dict, base_url: str) -> dict:
    """Best-effort local Hermes API autostart.

    Agent Hub uses Hermes through its OpenAI-compatible API. In daily use the
    Hermes TUI may be running while the API server is not, which surfaces as
    `[Errno 61] Connection refused`. For local Hermes profiles, try to start the
    gateway API server before failing the request.
    """
    if profile.get("type") != "hermes" or profile.get("adapter") != "openai-chat":
        return {"attempted": False}
    target = local_port_from_url(base_url)
    if not target:
        return {"attempted": False}
    host, port = target
    if is_port_open(host, port, timeout=0.15):
        return {"attempted": False, "running": True}
    binary = which("hermes")
    if not binary:
        return {"attempted": False, "error": "hermes binary not found"}
    log_path = f"/tmp/agent-hub-hermes-api-{port}.log"
    env = os.environ.copy()
    env["API_SERVER_ENABLED"] = "true"
    env["API_SERVER_PORT"] = str(port)
    try:
        with open(log_path, "ab") as log:
            subprocess.Popen(
                [binary, "gateway", "run"],
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
        deadline = time.time() + 6
        while time.time() < deadline:
            if is_port_open(host, port, timeout=0.2):
                return {"attempted": True, "started": True, "logPath": log_path}
            time.sleep(0.35)
        return {"attempted": True, "started": False, "logPath": log_path, "error": "Hermes API server did not open port in time"}
    except Exception as exc:
        return {"attempted": True, "started": False, "logPath": log_path, "error": str(exc)}


def http_probe_json(url: str, timeout: int = 2):
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return True, json.loads(raw or "{}")
    except Exception as exc:
        return False, str(exc)


def run_cli_json(command: list[str], timeout: int = 15, cwd: str | None = None):
    completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=cwd or None)
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if completed.returncode != 0:
        raise RuntimeError(stderr or stdout or f"Command failed: {' '.join(command)}")
    if not stdout:
        return {}
    return json.loads(stdout)


def run_cli_json_cancelable(command: list[str], timeout: int = 15, cwd: str | None = None, session_id: str = "", run_id: str = ""):
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd or None)
    deadline = time.time() + timeout
    while True:
        if is_cancelled(session_id, run_id):
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
            try:
                proc.communicate(timeout=1)
            except Exception:
                pass
            raise RuntimeError("任务已停止")
        if proc.poll() is not None:
            break
        if time.time() > deadline:
            proc.kill()
            raise subprocess.TimeoutExpired(command, timeout)
        time.sleep(0.2)
    stdout, stderr = proc.communicate()
    stdout = (stdout or "").strip()
    stderr = (stderr or "").strip()
    if proc.returncode != 0:
        raise RuntimeError(stderr or stdout or f"Command failed: {' '.join(command)}")
    if not stdout:
        return {}
    return json.loads(stdout)


def read_env_map(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def parse_simple_yaml_section_value(path: Path, section: str, key: str, default: str = "") -> str:
    if not path.exists():
        return default
    current_section = None
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line == raw_line.lstrip():
            current_section = raw_line.split(":", 1)[0].strip()
            continue
        if current_section != section:
            continue
        stripped = raw_line.strip()
        prefix = f"{key}:"
        if stripped.startswith(prefix):
            return stripped.split(":", 1)[1].strip().strip('"').strip("'")
    return default


def discover_hermes() -> dict | None:
    home = Path.home()
    config_path = home / ".hermes" / "config.yaml"
    env_path = home / ".hermes" / ".env"
    if not config_path.exists() and not env_path.exists() and not which("hermes"):
        return None

    env_map = read_env_map(env_path)
    upstream_model = parse_simple_yaml_section_value(config_path, "model", "default", DEFAULT_MODEL) or DEFAULT_MODEL
    api_enabled = env_map.get("API_SERVER_ENABLED", "false").lower() == "true"
    api_port = int(env_map.get("API_SERVER_PORT", "8642") or "8642")
    base_url = f"http://127.0.0.1:{api_port}/v1"
    reachable, health_data = http_probe_json(base_url.replace("/v1", "") + "/health", timeout=2)

    return {
        "id": "detected-hermes-local",
        "label": "Hermes 本机",
        "type": "hermes",
        "adapter": "openai-chat",
        "baseUrl": base_url,
        "model": DEFAULT_MODEL,
        "apiKey": "",
        "supportsStream": True,
        "supportsVision": True,
        "source": "detected",
        "configSource": str(config_path) if config_path.exists() else str(env_path),
        "binaryPath": which("hermes") or "",
        "installed": bool(config_path.exists() or which("hermes")),
        "apiServerEnabled": api_enabled,
        "reachable": reachable,
        "status": "online" if reachable else ("configured" if api_enabled else "installed"),
        "health": health_data,
        "notes": f"检测到 ~/.hermes 配置。当前 UI 走 Hermes API Server（模型标识 {DEFAULT_MODEL}），底层默认模型为 {upstream_model}。",
    }


def openclaw_profile_from_config(
    *,
    config_path: Path,
    gateway_url: str,
    model: str,
    reachable: bool,
    binary_path: str,
    auth: dict,
    bind: str,
    host: str,
    port: int,
    agent_row: dict | None = None,
) -> dict:
    agent_row = agent_row or {"id": "main", "name": "Main"}
    agent_id = str(agent_row.get("id") or "main")
    label_suffix = str(agent_row.get("name") or agent_id)
    entry_model = agent_row.get("model")
    if entry_model:
        model = entry_model.get("primary") if isinstance(entry_model, dict) else str(entry_model or model)
    tools = agent_row.get("tools") if isinstance(agent_row, dict) else {}
    tools = tools if isinstance(tools, dict) else {}
    tools_profile = str(tools.get("profile") or ("full" if agent_id == "main" else "custom"))
    tool_deny = tools.get("deny") if isinstance(tools.get("deny"), list) else []
    is_lite = tools_profile in {"minimal", "restricted"} or agent_id.endswith("lite")
    return {
        "id": f"detected-openclaw-local-{agent_id}",
        "label": "OpenClaw 本机" if agent_id == "main" else f"OpenClaw · {label_suffix}",
        "type": "openclaw",
        "adapter": "openclaw-gateway-rpc" if reachable else "openclaw-cli",
        "baseUrl": gateway_url if reachable else (binary_path or gateway_url),
        "model": model,
        "apiKey": "",
        "agentId": agent_id,
        "supportsStream": True,
        "supportsVision": True,
        "source": "detected",
        "configSource": str(config_path) if config_path.exists() else "",
        "binaryPath": binary_path,
        "installed": bool(config_path.exists() or which("openclaw")),
        "reachable": reachable,
        "status": "online" if reachable else "installed",
        "notes": (
            f"检测到 OpenClaw agent={agent_id}。"
            + ("该 agent 使用 minimal 工具集，适合安全轻量任务；如需浏览网页/执行复杂工具，建议切换 main。" if is_lite else "该 agent 接近 TUI 默认能力，适合需要完整工具能力的任务。")
        ),
        "gatewayAuthMode": auth.get("mode", "none"),
        "gatewayBind": bind,
        "gatewayHost": host,
        "gatewayPort": port,
        "toolsProfile": tools_profile,
        "toolDeny": [str(item) for item in tool_deny],
        "thinking": str(agent_row.get("thinking") or ""),
    }


def discover_openclaw_profiles() -> list[dict]:
    home = Path.home()
    config_path = home / ".openclaw" / "openclaw.json"
    if not config_path.exists() and not which("openclaw"):
        return []

    config = read_json(config_path, {})
    gateway = config.get("gateway", {}) if isinstance(config, dict) else {}
    defaults = (config.get("agents", {}) or {}).get("defaults", {}) if isinstance(config, dict) else {}
    auth = gateway.get("auth", {}) if isinstance(gateway, dict) else {}
    port = int(gateway.get("port", 18789) or 18789)
    bind = gateway.get("bind", "loopback") or "loopback"
    host = "127.0.0.1" if bind in {"loopback", "local", "auto"} else "0.0.0.0"
    gateway_url = f"ws://127.0.0.1:{port}"
    default_model = defaults.get("model") or {}
    model = default_model.get("primary") if isinstance(default_model, dict) else str(default_model or "")
    reachable = is_port_open("127.0.0.1", port)
    binary_path = which("openclaw") or ""
    agent_rows = (config.get("agents", {}) or {}).get("list") if isinstance(config, dict) else None
    rows = [item for item in (agent_rows if isinstance(agent_rows, list) else []) if isinstance(item, dict) and item.get("id")]
    if not any(str(item.get("id")) == "main" for item in rows):
        rows.insert(0, {"id": "main", "name": "Main"})
    if os.environ.get("AGENT_HUB_SHOW_RESTRICTED_OPENCLAW", "").lower() not in {"1", "true", "yes"}:
        def is_restricted_openclaw_agent(row: dict) -> bool:
            tools = row.get("tools") if isinstance(row, dict) else {}
            tools = tools if isinstance(tools, dict) else {}
            profile = str(tools.get("profile") or "").strip().lower()
            agent_id = str(row.get("id") or "").strip().lower()
            return profile in {"minimal", "restricted"} or agent_id.endswith("lite")
        rows = [row for row in rows if str(row.get("id") or "") == "main" or not is_restricted_openclaw_agent(row)]
    rows = sorted(rows, key=lambda item: (0 if str(item.get("id")) == "main" else 1, str(item.get("id"))))
    return [
        openclaw_profile_from_config(
            config_path=config_path,
            gateway_url=gateway_url,
            model=model,
            reachable=reachable,
            binary_path=binary_path,
            auth=auth,
            bind=bind,
            host=host,
            port=port,
            agent_row=row,
        )
        for row in rows
    ]


def discover_openclaw() -> dict | None:
    profiles = discover_openclaw_profiles()
    return profiles[0] if profiles else None


def discover_claude_code() -> dict | None:
    binary = which("claude")
    if not binary:
        return None
    version = ""
    try:
        completed = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5)
        if completed.returncode == 0:
            version = (completed.stdout or completed.stderr or "").strip()
    except Exception:
        version = ""
    return {
        "id": "detected-claude-code-local",
        "label": "Claude Code 本机",
        "type": "claude",
        "adapter": "claude-code-cli",
        "baseUrl": binary,
        "model": "sonnet",
        "apiKey": "",
        "agentId": "claude-code",
        "binaryPath": binary,
        "supportsStream": False,
        "supportsVision": False,
        "source": "detected",
        "installed": True,
        "reachable": True,
        "status": "installed",
        "version": version,
        "notes": f"检测到 Claude Code CLI{(' · ' + version) if version else ''}。Agent Hub 使用 claude -p 非交互模式调用，并由本地上下文中枢管理会话。",
    }


def discover_agents() -> list[dict]:
    found = []
    hermes = discover_hermes()
    if hermes:
        found.append(hermes)
    found.extend(discover_openclaw_profiles())
    claude = discover_claude_code()
    if claude:
        found.append(claude)
    return dedupe_profiles(found)[0]


def profile_key(item: dict) -> str:
    """Stable identity for de-duplicating scans/imports.

    Do not use label/source/id here: those change between "detected", "imported",
    and manually edited profiles. OpenClaw must include agentId so main/hub-lite
    can coexist.
    """
    adapter = str(item.get("adapter") or "").strip().lower()
    typ = str(item.get("type") or "").strip().lower()
    base = str(item.get("baseUrl") or item.get("binaryPath") or "").strip().rstrip("/")
    agent_id = str(item.get("agentId") or "").strip()
    model = str(item.get("model") or "").strip()
    return "|".join([typ, adapter, base, agent_id, model])


def dedupe_profiles(profiles: list[dict], active_id: str | None = None) -> tuple[list[dict], dict[str, str]]:
    """Return unique profiles and a map from removed duplicate id -> kept id."""
    unique: list[dict] = []
    index: dict[str, int] = {}
    id_map: dict[str, str] = {}
    for raw in profiles:
        if not isinstance(raw, dict):
            continue
        item = normalize_profile(raw)
        key = profile_key(item)
        old_id = item["id"]
        if key not in index:
            index[key] = len(unique)
            unique.append(item)
            id_map[old_id] = old_id
            continue

        kept = unique[index[key]]
        # If the duplicate is the active profile, keep its id/content so the
        # active selection does not point to a removed entry.
        if active_id and old_id == active_id:
            id_map[kept["id"]] = old_id
            id_map[old_id] = old_id
            unique[index[key]] = item
        else:
            id_map[old_id] = kept["id"]
            # Fill missing metadata on the kept profile without creating a new row.
            for field in ("notes", "configSource", "binaryPath", "status", "toolsProfile", "toolDeny", "thinking", "gatewayAuthMode", "gatewayBind", "gatewayHost", "gatewayPort"):
                if not kept.get(field) and item.get(field):
                    kept[field] = item[field]
    return unique, id_map


def public_profile(profile: dict) -> dict:
    """Return a browser-safe profile copy.

    The browser does not need stored API keys for normal calls; the server can
    resolve them from its local config. Avoid leaking tokens through /api/config
    or static JSON fetches.
    """
    item = dict(profile)
    if item.get("apiKey"):
        item["apiKeySet"] = True
    item["apiKey"] = ""
    return item


def public_hub(hub: dict) -> dict:
    return {
        "activeAgentId": hub.get("activeAgentId"),
        "profiles": [public_profile(item) for item in hub.get("profiles", [])],
    }


def normalize_profile(item: dict) -> dict:
    profile = {
        "id": str(item.get("id") or f"agent-{int(time.time() * 1000)}"),
        "label": str(item.get("label") or "未命名 Agent"),
        "type": str(item.get("type") or "custom"),
        "adapter": str(item.get("adapter") or "openai-chat"),
        "baseUrl": str(item.get("baseUrl") or "").rstrip("/"),
        "model": str(item.get("model") or ""),
        "apiKey": str(item.get("apiKey") or ""),
        "agentId": str(item.get("agentId") or ""),
        "binaryPath": str(item.get("binaryPath") or ""),
        "supportsStream": bool(item.get("supportsStream", True)),
        "supportsVision": bool(item.get("supportsVision", False)),
        "source": str(item.get("source") or "manual"),
        "notes": str(item.get("notes") or ""),
    }
    for key in (
        "reachable",
        "status",
        "configSource",
        "installed",
        "apiServerEnabled",
        "gatewayAuthMode",
        "gatewayBind",
        "gatewayHost",
        "gatewayPort",
        "toolsProfile",
        "toolDeny",
        "thinking",
        "health",
        "version",
    ):
        if key in item:
            profile[key] = item.get(key)
    if profile["adapter"] == "openclaw-gateway" and profile["baseUrl"].startswith("http"):
        profile["baseUrl"] = profile["baseUrl"].replace("http://", "ws://").replace("https://", "wss://")
    return profile


def default_profiles() -> tuple[list[dict], str]:
    discovered = discover_agents()
    hermes = next((item for item in discovered if item.get("type") == "hermes"), None)
    if hermes:
        profile = normalize_profile(hermes)
        profile["source"] = "imported"
        return [profile], profile["id"]
    profile = normalize_profile({
        "id": "hermes-default",
        "label": "Hermes 默认",
        "type": "hermes",
        "adapter": "openai-chat",
        "baseUrl": DEFAULT_HERMES_BASE,
        "model": DEFAULT_MODEL,
        "apiKey": "",
        "supportsStream": True,
        "supportsVision": True,
        "source": "generated",
        "notes": "根据本地 UI 启动参数生成。",
    })
    return [profile], profile["id"]


def load_agent_hub() -> dict:
    if CONFIG_FILE.exists():
        data = read_json(CONFIG_FILE, {})
        raw_profiles = [item for item in data.get("profiles", []) if isinstance(item, dict)]
        active = data.get("activeAgentId")
        profiles, id_map = dedupe_profiles(raw_profiles, active)
        if active in id_map:
            active = id_map[active]
        if profiles:
            if not active or not any(p["id"] == active for p in profiles):
                active = profiles[0]["id"]
            # Keep the full-capability OpenClaw main agent available even if an
            # older config only imported a restricted hub-lite profile. This
            # mirrors the OpenClaw TUI default more closely while preserving the
            # user's active selection.
            metadata_dirty = False
            try:
                discovered_openclaw = [normalize_profile(item) for item in discover_openclaw_profiles()]
                discovered_claude = [normalize_profile(item) for item in [discover_claude_code()] if item]
                existing_keys = {profile_key(item) for item in profiles}
                for item in discovered_openclaw:
                    key = profile_key(item)
                    existing = next((profile for profile in profiles if profile_key(profile) == key), None)
                    if existing:
                        for field in ("toolsProfile", "toolDeny", "thinking", "gatewayAuthMode", "gatewayBind", "gatewayHost", "gatewayPort", "configSource", "binaryPath", "reachable", "installed", "status"):
                            if not existing.get(field) and item.get(field):
                                existing[field] = item[field]
                                metadata_dirty = True
                    elif item.get("agentId") == "main" and key not in existing_keys:
                        item["source"] = "imported"
                        profiles.append(item)
                        existing_keys.add(key)
                        metadata_dirty = True
                for item in discovered_claude:
                    key = profile_key(item)
                    if key not in existing_keys:
                        item["source"] = "imported"
                        profiles.append(item)
                        existing_keys.add(key)
                        metadata_dirty = True
            except Exception:
                pass
            # Persist cleanup so repeated scans/imports don't grow the settings list.
            if metadata_dirty or len(profiles) != len(raw_profiles) or active != data.get("activeAgentId"):
                write_json(CONFIG_FILE, {"profiles": profiles, "activeAgentId": active})
            return {"profiles": profiles, "activeAgentId": active}

    profiles, active = default_profiles()
    data = {"profiles": profiles, "activeAgentId": active}
    write_json(CONFIG_FILE, data)
    return data


def save_agent_hub(payload: dict) -> dict:
    existing_by_id = {item.get("id"): item for item in load_agent_hub().get("profiles", []) if isinstance(item, dict)}
    raw_profiles = [item for item in payload.get("profiles", []) if isinstance(item, dict)]
    for item in raw_profiles:
        # Preserve a stored secret when the browser sends back a redacted/empty
        # field during ordinary edits.
        if not item.get("apiKey") and item.get("id") in existing_by_id:
            item["apiKey"] = existing_by_id[item.get("id")].get("apiKey", "")
    requested_active = str(payload.get("activeAgentId") or "")
    profiles, id_map = dedupe_profiles(raw_profiles, requested_active)
    if not profiles:
        profiles, active = default_profiles()
    else:
        active = id_map.get(requested_active, requested_active) or profiles[0]["id"]
        if not any(p["id"] == active for p in profiles):
            active = profiles[0]["id"]
    data = {"profiles": profiles, "activeAgentId": active}
    write_json(CONFIG_FILE, data)
    return data


def get_active_profile() -> dict:
    hub = load_agent_hub()
    return next((item for item in hub["profiles"] if item["id"] == hub["activeAgentId"]), hub["profiles"][0])


def resolve_helper_python() -> str:
    candidates = [
        os.environ.get("AGENT_HUB_PYTHON", "").strip(),
        str(Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "python" / "bin" / "python3"),
        which("python3") or "",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return sys.executable


def parse_office_attachment(payload: dict) -> dict:
    name = str(payload.get("name") or "attachment")
    mime = str(payload.get("mime") or "")
    data_b64 = str(payload.get("dataBase64") or "")
    if not data_b64:
        raise ValueError("缺少附件内容")
    raw = base64.b64decode(data_b64)
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise ValueError("附件过大，请控制在 15MB 以内")
    helper = resolve_helper_python()
    helper_script = r"""
import base64, io, json, sys

payload = json.loads(sys.stdin.read())
name = payload.get("name", "attachment")
mime = payload.get("mime", "")
raw = base64.b64decode(payload["dataBase64"])
lower = name.lower()
text = ""
meta = ""

if lower.endswith(".pdf") or mime == "application/pdf":
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(raw))
    pages = []
    for index, page in enumerate(reader.pages, start=1):
        extracted = (page.extract_text() or "").strip()
        if extracted:
            pages.append(f"[第 {index} 页]\\n{extracted}")
    text = "\\n\\n".join(pages).strip()
    meta = f"PDF · {len(reader.pages)} 页"
elif lower.endswith(".docx") or mime in {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}:
    import docx
    document = docx.Document(io.BytesIO(raw))
    parts = [p.text.strip() for p in document.paragraphs if p.text and p.text.strip()]
    for table in document.tables:
        rows = []
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            parts.append("\\n".join(rows))
    text = "\\n\\n".join(parts).strip()
    meta = f"Word · {len(document.paragraphs)} 段"
elif lower.endswith(".xlsx") or mime in {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
}:
    import openpyxl
    workbook = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    sheets = []
    for sheet in workbook.worksheets[:6]:
        rows = []
        for row_index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            values = [str(v).strip() for v in row if v not in (None, "")]
            if values:
                rows.append(" | ".join(values))
            if row_index >= 40:
                break
        if rows:
            sheets.append(f"[工作表] {sheet.title}\\n" + "\\n".join(rows))
    text = "\\n\\n".join(sheets).strip()
    meta = f"Excel · {len(workbook.sheetnames)} 个工作表"
else:
    raise SystemExit(json.dumps({"ok": False, "error": "暂不支持该 Office/PDF 格式，请优先使用 .docx / .xlsx / .pdf"}, ensure_ascii=False))

if not text:
    raise SystemExit(json.dumps({"ok": False, "error": "文件已读取，但未提取到可用文本内容"}, ensure_ascii=False))

if len(text) > 36000:
    text = text[:36000] + "\\n\\n[内容过长，已截断]"

print(json.dumps({
    "ok": True,
    "kind": "text",
    "name": name,
    "mime": mime,
    "text": text,
    "meta": meta,
}, ensure_ascii=False))
"""
    completed = subprocess.run(
        [helper, "-c", helper_script],
        input=json.dumps({"name": name, "mime": mime, "dataBase64": data_b64}, ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=45,
    )
    output = (completed.stdout or completed.stderr or "").strip()
    if not output:
        raise RuntimeError("附件解析无返回结果")
    parsed = json.loads(output)
    if completed.returncode != 0 or parsed.get("ok") is False:
        raise RuntimeError(parsed.get("error") or output or "附件解析失败")
    return parsed


def openclaw_message_from_messages(messages) -> str:
    if not isinstance(messages, list):
        return ""
    system = next((m for m in messages if isinstance(m, dict) and m.get("role") == "system"), None)
    last_user = next((m for m in reversed(messages) if isinstance(m, dict) and m.get("role") == "user"), None)
    if not last_user:
        return ""
    content = last_user.get("content")
    if isinstance(content, str):
        user_text = content.strip()
    elif isinstance(content, list):
        text_parts = []
        image_count = 0
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and part.get("text"):
                text_parts.append(str(part.get("text")))
            elif part.get("type") == "image_url":
                image_count += 1
        if image_count:
            text_parts.append(f"[附带图片 {image_count} 张；当前 OpenClaw CLI 适配器暂按文本模式转发，如需图片理解建议切换 Hermes。]")
        user_text = "\n\n".join([part for part in text_parts if part]).strip()
    else:
        user_text = str(content or "").strip()

    # OpenClaw Gateway agent RPC accepts a single message string. Borrowing the
    # CLI GUI pattern, we fold the compacted Agent Hub system context into the
    # prompt so OpenClaw receives session summary / handoff information too.
    system_text = text_from_content(system.get("content")) if isinstance(system, dict) else ""
    context_markers = ("Agent Hub 会话摘要", "Agent Hub Agent 交接摘要", "Agent Hub Context Package v1")
    if system_text and any(marker in system_text for marker in context_markers):
        return f"{truncate_text(system_text, 2200)}\n\n[用户当前请求]\n{user_text}".strip()
    return user_text


def openclaw_result_to_text(payload: dict) -> str:
    result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(result, dict):
        parts = result.get("payloads")
        if isinstance(parts, list):
            lines = []
            for item in parts:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if text:
                    lines.append(str(text))
                media_urls = item.get("mediaUrls")
                if isinstance(media_urls, list) and media_urls:
                    lines.extend([f"MEDIA: {url}" for url in media_urls])
            if lines:
                return "\n\n".join(lines).strip()
        summary = result.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
    summary = payload.get("summary") if isinstance(payload, dict) else None
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    return json.dumps(payload, ensure_ascii=False, indent=2)


def openclaw_agent_configured(agent_id: str) -> bool:
    """Fast config-only check for OpenClaw agents.

    `openclaw agents list --json` can take 10s+ on some installs because it
    loads plugins and workspaces. The UI badge should not run that heavyweight
    command on every health check.
    """
    agent_id = (agent_id or "main").strip() or "main"
    config_path = Path.home() / ".openclaw" / "openclaw.json"
    config = read_json(config_path, {})
    agents = (config.get("agents", {}) or {}) if isinstance(config, dict) else {}
    rows = agents.get("list")
    if agent_id == "main":
        return True
    if isinstance(rows, list):
        return any(isinstance(row, dict) and str(row.get("id") or "") == agent_id for row in rows)
    return False


def openclaw_gateway_port_from_config() -> int:
    config_path = Path.home() / ".openclaw" / "openclaw.json"
    config = read_json(config_path, {})
    gateway = (config.get("gateway", {}) or {}) if isinstance(config, dict) else {}
    try:
        return int(gateway.get("port", 18789) or 18789)
    except Exception:
        return 18789


def b64url_no_padding(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def openclaw_gateway_url(profile: dict) -> str:
    raw = str(profile.get("baseUrl") or "").strip()
    if raw.startswith("ws://") or raw.startswith("wss://"):
        return raw
    return f"ws://127.0.0.1:{openclaw_gateway_port_from_config()}"


def build_openclaw_device_connect(nonce: str) -> dict:
    """Build a signed OpenClaw Gateway connect frame using local device auth.

    This follows the same Gateway auth path as the official CLI, but avoids
    spawning a CLI process for every chat turn.
    """
    identity = read_json(Path.home() / ".openclaw" / "identity" / "device.json", {})
    auth_store = read_json(Path.home() / ".openclaw" / "identity" / "device-auth.json", {})
    token_entry = ((auth_store.get("tokens") or {}).get("operator") or {}) if isinstance(auth_store, dict) else {}
    token = str(token_entry.get("token") or "")
    scopes = token_entry.get("scopes") if isinstance(token_entry, dict) else None
    if not isinstance(scopes, list) or not scopes:
        scopes = ["operator.admin", "operator.read", "operator.write"]
    scopes = [str(item) for item in scopes if str(item).strip()]
    if not token:
        raise RuntimeError("OpenClaw device auth token not found. Run `openclaw gateway status` or `openclaw agent --message 你好 --json` once to initialize pairing.")
    if not identity.get("deviceId") or not identity.get("privateKeyPem") or not identity.get("publicKeyPem"):
        raise RuntimeError("OpenClaw device identity not found under ~/.openclaw/identity.")

    try:
        from cryptography.hazmat.primitives import serialization
    except Exception as exc:
        raise RuntimeError("Missing Python dependency `cryptography`; install requirements.txt or use OpenClaw CLI adapter.") from exc

    private_key = serialization.load_pem_private_key(str(identity["privateKeyPem"]).encode("utf-8"), password=None)
    public_key = serialization.load_pem_public_key(str(identity["publicKeyPem"]).encode("utf-8"))
    public_raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    client_id = "cli"
    client_mode = "cli"
    role = "operator"
    signed_at = int(time.time() * 1000)
    platform = "darwin" if sys.platform == "darwin" else sys.platform
    device_family = ""
    signature_payload = "|".join([
        "v3",
        str(identity["deviceId"]),
        client_id,
        client_mode,
        role,
        ",".join(scopes),
        str(signed_at),
        token,
        nonce,
        platform,
        device_family,
    ])
    signature = b64url_no_padding(private_key.sign(signature_payload.encode("utf-8")))
    return {
        "token": token,
        "role": role,
        "scopes": scopes,
        "client": {
            "id": client_id,
            "displayName": "agent-hub",
            "version": "agent-hub",
            "platform": platform,
            "mode": client_mode,
            "instanceId": str(uuid.uuid4()),
        },
        "device": {
            "id": str(identity["deviceId"]),
            "publicKey": b64url_no_padding(public_raw),
            "signature": signature,
            "signedAt": signed_at,
            "nonce": nonce,
        },
    }


class OpenClawGatewayRpcError(RuntimeError):
    """OpenClaw Gateway returned an RPC error frame."""


class OpenClawGatewayClient:
    """Reusable, authenticated OpenClaw Gateway WebSocket client.

    websocket-client is not safe for concurrent recv/send pairs, so each client
    serializes RPC calls with one re-entrant lock. This avoids paying the
    challenge/sign/connect handshake cost for every chat turn while keeping the
    bridge predictable under ThreadingHTTPServer.
    """

    def __init__(self, url: str):
        self.url = url
        self._ws = None
        self._lock = threading.RLock()
        self.connected = False
        self.authenticated = False
        self.created_at = time.time()
        self.last_connected_at = 0.0
        self.last_used_at = 0.0
        self.request_count = 0
        self.connect_count = 0
        self.last_error = ""

    def _is_socket_open(self) -> bool:
        return bool(self._ws is not None and getattr(self._ws, "connected", True))

    def _recv_json(self):
        raw = self._ws.recv()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        return json.loads(raw)

    def _reset_locked(self, reason: str = ""):
        ws = self._ws
        self._ws = None
        self.connected = False
        self.authenticated = False
        if reason:
            self.last_error = reason
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def close(self):
        with self._lock:
            self._reset_locked("closed")

    def connect_locked(self):
        if self.authenticated and self._is_socket_open():
            return
        self._reset_locked()
        try:
            import websocket
        except Exception as exc:
            raise RuntimeError("Missing Python dependency `websocket-client`; install requirements.txt or use OpenClaw CLI adapter.") from exc

        try:
            ws = websocket.create_connection(self.url, timeout=10, enable_multithread=True)
            self._ws = ws
            self.connected = True
            ws.settimeout(10)
            challenge = self._recv_json()
            if challenge.get("event") != "connect.challenge":
                raise RuntimeError("OpenClaw Gateway did not send connect.challenge")
            nonce = str((challenge.get("payload") or {}).get("nonce") or "")
            auth = build_openclaw_device_connect(nonce)
            self._request_locked("connect", {
                "minProtocol": 3,
                "maxProtocol": 3,
                "client": auth["client"],
                "caps": [],
                "auth": {"token": auth["token"]},
                "role": auth["role"],
                "scopes": auth["scopes"],
                "device": auth["device"],
            }, timeout=10)
            self.authenticated = True
            self.connect_count += 1
            self.last_connected_at = time.time()
            self.last_error = ""
        except Exception as exc:
            self._reset_locked(str(exc))
            raise

    def _request_locked(self, method: str, params: dict, timeout: int = 120, expect_final: bool = False):
        if self._ws is None:
            raise RuntimeError("OpenClaw Gateway socket is not connected")
        request_id = str(uuid.uuid4())
        sent = False
        try:
            self._ws.settimeout(max(1, min(timeout, 30)))
            self._ws.send(json.dumps({"type": "req", "id": request_id, "method": method, "params": params}, ensure_ascii=False))
            sent = True
            deadline = time.time() + timeout
            while time.time() < deadline:
                self._ws.settimeout(max(0.1, deadline - time.time()))
                frame = self._recv_json()
                # The Gateway can emit async event frames (tick/log/etc.). Keep
                # draining until the matching response id arrives.
                if frame.get("type") != "res" or frame.get("id") != request_id:
                    continue
                if not frame.get("ok"):
                    error = frame.get("error") or {}
                    raise OpenClawGatewayRpcError(error.get("message") or json.dumps(error, ensure_ascii=False))
                payload = frame.get("payload")
                if expect_final and isinstance(payload, dict) and payload.get("status") == "accepted":
                    continue
                self.request_count += 1
                self.last_used_at = time.time()
                self.last_error = ""
                return payload
            raise TimeoutError(f"OpenClaw Gateway request timed out: {method}")
        except OpenClawGatewayRpcError:
            # RPC-level errors do not necessarily mean the socket is bad.
            raise
        except Exception as exc:
            self._reset_locked(str(exc))
            raise

    def request(self, method: str, params: dict, timeout: int = 120, expect_final: bool = False):
        with self._lock:
            self.connect_locked()
            started = time.time()
            try:
                return self._request_locked(method, params, timeout=timeout, expect_final=expect_final)
            except OpenClawGatewayRpcError as exc:
                self.last_error = str(exc)
                raise
            except Exception as exc:
                # If an idle reused socket was closed by the Gateway, the first
                # send/recv fails almost immediately. Reconnect once and resend
                # the same params/idempotency key. Do not retry after a long
                # model wait/timeout, because that would hide real slow runs.
                elapsed = time.time() - started
                self._reset_locked(str(exc))
                if elapsed < 3:
                    self.connect_locked()
                    return self._request_locked(method, params, timeout=timeout, expect_final=expect_final)
                raise

    def agent_turn(self, profile: dict, message: str, session_id: str, timeout_sec: int = 300) -> dict:
        thinking = str(profile.get("thinking") or os.environ.get("OPENCLAW_THINKING") or "medium").strip() or "medium"
        return self.request("agent", {
            "agentId": profile.get("agentId") or "main",
            "message": message or "你好",
            "thinking": thinking,
            "deliver": False,
            "timeout": timeout_sec,
            "sessionId": session_id,
            "idempotencyKey": str(uuid.uuid4()),
        }, timeout=timeout_sec + 30, expect_final=True)

    def status(self) -> dict:
        # Non-blocking snapshot: health checks must not wait behind a long
        # running agent turn that is holding the RPC lock. A slightly stale
        # status is fine for UI badges.
        return {
            "url": self.url,
            "connected": bool(self.connected and self._is_socket_open()),
            "authenticated": bool(self.authenticated and self._is_socket_open()),
            "requestCount": self.request_count,
            "connectCount": self.connect_count,
            "createdAt": self.created_at,
            "lastConnectedAt": self.last_connected_at,
            "lastUsedAt": self.last_used_at,
            "lastError": self.last_error,
        }


OPENCLAW_RPC_CLIENTS: dict[str, OpenClawGatewayClient] = {}
OPENCLAW_RPC_CLIENTS_LOCK = threading.RLock()


def openclaw_gateway_client_for(profile: dict) -> OpenClawGatewayClient:
    url = openclaw_gateway_url(profile)
    with OPENCLAW_RPC_CLIENTS_LOCK:
        client = OPENCLAW_RPC_CLIENTS.get(url)
        if client is None:
            client = OpenClawGatewayClient(url)
            OPENCLAW_RPC_CLIENTS[url] = client
        return client


def openclaw_gateway_pool_status(profile: dict | None = None) -> dict:
    with OPENCLAW_RPC_CLIENTS_LOCK:
        if profile is not None:
            url = openclaw_gateway_url(profile)
            client = OPENCLAW_RPC_CLIENTS.get(url)
            return client.status() if client else {
                "url": url,
                "connected": False,
                "authenticated": False,
                "requestCount": 0,
                "connectCount": 0,
                "lastConnectedAt": 0,
                "lastUsedAt": 0,
                "lastError": "",
            }
        return {url: client.status() for url, client in OPENCLAW_RPC_CLIENTS.items()}


def openclaw_gateway_request(ws, method: str, params: dict, timeout: int = 120, expect_final: bool = False):
    """Compatibility helper for one-off callers/tests."""
    request_id = str(uuid.uuid4())
    ws.send(json.dumps({"type": "req", "id": request_id, "method": method, "params": params}, ensure_ascii=False))
    deadline = time.time() + timeout
    while time.time() < deadline:
        ws.settimeout(max(0.1, deadline - time.time()))
        frame = json.loads(ws.recv())
        if frame.get("type") != "res" or frame.get("id") != request_id:
            continue
        if not frame.get("ok"):
            error = frame.get("error") or {}
            raise RuntimeError(error.get("message") or json.dumps(error, ensure_ascii=False))
        payload = frame.get("payload")
        if expect_final and isinstance(payload, dict) and payload.get("status") == "accepted":
            continue
        return payload
    raise TimeoutError(f"OpenClaw Gateway request timed out: {method}")


def openclaw_gateway_agent_turn(profile: dict, message: str, session_id: str, timeout_sec: int = 300) -> dict:
    client = openclaw_gateway_client_for(profile)
    return client.agent_turn(profile, message, session_id, timeout_sec=timeout_sec)


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentHub/0.4"

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args), flush=True)

    def _send(self, code=200, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _read_json(self):
        n = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/config":
            hub = load_agent_hub()
            discovered = discover_agents()
            active = next((p for p in hub["profiles"] if p["id"] == hub["activeAgentId"]), hub["profiles"][0])
            safe_hub = public_hub(hub)
            self._json({
                "defaultBaseUrl": active.get("baseUrl") or DEFAULT_HERMES_BASE,
                "defaultModel": active.get("model") or DEFAULT_MODEL,
                "uiPort": PORT,
                "agentHub": {
                    "activeAgentId": safe_hub["activeAgentId"],
                    "profiles": safe_hub["profiles"],
                    "discovered": [public_profile(item) for item in discovered],
                    "configPath": str(CONFIG_FILE),
                    "activeProfile": public_profile(active),
                },
            })
            return
        if path == "/api/agents/discover":
            started = time.time()
            warning = ""
            try:
                discovered = discover_agents()
            except Exception as exc:
                discovered = []
                warning = str(exc)
            self._json({
                "ok": True,
                "data": [public_profile(item) for item in discovered],
                "configPath": str(CONFIG_FILE),
                "latencyMs": int((time.time() - started) * 1000),
                "warning": warning,
            })
            return
        if path == "/api/sessions":
            data = load_sessions_from_db()
            self._json({"ok": True, "data": data, "dbPath": str(DB_FILE)})
            return
        if path == "/api/runs":
            query = parse_qs(urlparse(self.path).query)
            self._json({
                "ok": True,
                "data": load_agent_runs(
                    str((query.get("session_id") or [""])[0]),
                    int((query.get("limit") or ["50"])[0] or "50"),
                ),
                "dbPath": str(DB_FILE),
            })
            return
        if path == "/api/handoffs":
            query = parse_qs(urlparse(self.path).query)
            self._json({
                "ok": True,
                "data": load_handoffs(
                    str((query.get("session_id") or [""])[0]),
                    int((query.get("limit") or ["50"])[0] or "50"),
                ),
                "dbPath": str(DB_FILE),
            })
            return
        if path == "/api/context-package":
            query = parse_qs(urlparse(self.path).query)
            active = get_active_profile()
            session_id = str((query.get("session_id") or [""])[0])
            target_agent_id = str((query.get("agent_id") or [active.get("id") or active.get("agentId") or ""])[0])
            strategy = str((query.get("strategy") or [""])[0])
            result = load_context_package(session_id, target_agent_id, str(active.get("label") or ""), strategy)
            self._json({"ok": True, **result, "dbPath": str(DB_FILE)})
            return
        if path == "/api/task-spec":
            query = parse_qs(urlparse(self.path).query)
            session_id = str((query.get("session_id") or query.get("sessionId") or [""])[0])
            self._json({"ok": True, "data": load_task_spec(session_id), "dbPath": str(DB_FILE)})
            return
        if path == "/api/task-workflow":
            query = parse_qs(urlparse(self.path).query)
            session_id = str((query.get("session_id") or query.get("sessionId") or [""])[0])
            self._json({"ok": True, "data": load_task_workflow(session_id), "dbPath": str(DB_FILE)})
            return
        if path == "/api/memories":
            query = parse_qs(urlparse(self.path).query)
            self._json({
                "ok": True,
                "data": load_project_memories(
                    str((query.get("workspace_path") or [""])[0]),
                    str((query.get("q") or [""])[0]),
                    int((query.get("limit") or ["30"])[0] or "30"),
                ),
                "dbPath": str(DB_FILE),
            })
            return
        if path == "/api/health":
            self._health()
            return
        if path == "/api/models":
            base = self.headers.get("X-Hermes-Base") or get_active_profile().get("baseUrl") or DEFAULT_HERMES_BASE
            key = self.headers.get("X-Hermes-Key", "") or get_active_profile().get("apiKey", "")
            self._proxy_get(base.rstrip("/") + "/models", key)
            return
        if path == "/":
            file = ROOT / "index.html"
        else:
            file = (ROOT / path.lstrip("/")).resolve()
            if file != ROOT and ROOT not in file.parents:
                self._send(403, b"Forbidden")
                return
            if file == DATA_DIR or DATA_DIR in file.parents:
                self._send(403, b"Forbidden")
                return
        if not file.exists() or file.is_dir():
            self._send(404, b"Not found")
            return
        ctype = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
        if file.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        if file.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        if file.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        self._send(200, file.read_bytes(), ctype)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/chat":
            self._chat()
            return
        if path == "/api/chat-stream":
            self._chat_stream()
            return
        if path == "/api/responses":
            self._responses()
            return
        if path == "/api/agents/save":
            hub = save_agent_hub(self._read_json())
            self._json({"ok": True, "agentHub": public_hub(hub), "configPath": str(CONFIG_FILE)})
            return
        if path == "/api/sessions/sync":
            data = save_sessions_to_db(self._read_json())
            self._json({"ok": True, "data": data, "dbPath": str(DB_FILE)})
            return
        if path == "/api/sessions/handoff":
            data = create_handoff(self._read_json())
            self._json({"ok": True, **data, "dbPath": str(DB_FILE)})
            return
        if path == "/api/runs/cancel":
            body = self._read_json()
            session_id = str(body.get("sessionId") or body.get("session_id") or "")
            run_id = str(body.get("runId") or body.get("run_id") or "")
            if not session_id:
                self._json({"ok": False, "error": "缺少 sessionId"}, 400)
                return
            RUN_CANCEL_FLAGS.add(run_key(session_id, run_id))
            record_agent_run(session_id, str(body.get("agentId") or ""), str(body.get("adapter") or ""), "cancelled", [], error="用户请求停止当前任务", latency_ms=0)
            self._json({"ok": True, "sessionId": session_id, "runId": run_id, "cancelled": True})
            return
        if path == "/api/task-router/recommend":
            try:
                body = self._read_json()
                hub = load_agent_hub()
                session_id = str(body.get("sessionId") or body.get("session_id") or "")
                chat = body.get("chat") if isinstance(body.get("chat"), dict) else (get_session(session_id) or {})
                message = str(body.get("message") or "")
                recommendation = task_router.recommend_agent(
                    chat,
                    hub.get("profiles", []),
                    message=message,
                    active_agent_id=str(hub.get("activeAgentId") or ""),
                    workflow_stage=str(body.get("workflowStage") or body.get("workflow_stage") or ""),
                    task_spec=load_task_spec(session_id) if session_id else {},
                )
                generated_spec = task_router.task_spec_from_session(chat, message=message, recommendation=recommendation)
                if session_id:
                    generated_spec["sessionId"] = session_id
                    generated_spec = save_task_spec(generated_spec)
                recorded = record_dispatch_recommendation(session_id, recommendation) if session_id else {}
                self._json({"ok": True, "recommendation": {**recommendation, **recorded}, "taskSpec": generated_spec, "dbPath": str(DB_FILE)})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            return
        if path == "/api/task-spec/generate":
            try:
                body = self._read_json()
                session_id = str(body.get("sessionId") or body.get("session_id") or "")
                chat = body.get("chat") if isinstance(body.get("chat"), dict) else (get_session(session_id) or {})
                message = str(body.get("message") or "")
                spec = task_router.task_spec_from_session(chat, message=message)
                if session_id:
                    spec["sessionId"] = session_id
                    spec = save_task_spec(spec)
                self._json({"ok": True, "data": spec, "dbPath": str(DB_FILE)})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            return
        if path == "/api/task-router/accept":
            try:
                body = self._read_json()
                session_id = str(body.get("sessionId") or body.get("session_id") or "")
                recommendation_id = str(body.get("recommendationId") or body.get("recommendation_id") or "")
                accepted = accept_dispatch_recommendation(session_id, recommendation_id)
                hub = load_agent_hub()
                if accepted.get("primaryAgentId") and any(p.get("id") == accepted["primaryAgentId"] for p in hub.get("profiles", [])):
                    hub["activeAgentId"] = accepted["primaryAgentId"]
                    write_json(CONFIG_FILE, hub)
                self._json({"ok": True, "recommendation": accepted, "agentHub": public_hub(load_agent_hub()), "dbPath": str(DB_FILE)})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            return
        if path == "/api/task-spec/save":
            try:
                self._json({"ok": True, "data": save_task_spec(self._read_json()), "dbPath": str(DB_FILE)})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            return
        if path == "/api/task-workflow/transition":
            try:
                body = self._read_json()
                workflow = transition_task_stage(body)
                session_id = str(body.get("sessionId") or body.get("session_id") or "")
                hub = load_agent_hub()
                chat = get_session(session_id) or {}
                spec = load_task_spec(session_id)
                recommendation = task_router.recommend_agent(
                    chat,
                    hub.get("profiles", []),
                    active_agent_id=str(hub.get("activeAgentId") or ""),
                    workflow_stage=str(workflow.get("currentStage") or spec.get("stage") or ""),
                    task_spec=spec,
                )
                recorded = record_dispatch_recommendation(session_id, recommendation) if session_id else {}
                self._json({"ok": True, "data": workflow, "recommendation": {**recommendation, **recorded}, "dbPath": str(DB_FILE)})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            return
        if path == "/api/task-workflow/stage-action":
            try:
                body = self._read_json()
                session_id = str(body.get("sessionId") or body.get("session_id") or "")
                stage = str(body.get("stage") or "")
                chat = get_session(session_id) or {}
                spec = load_task_spec(session_id)
                hub = load_agent_hub()
                recommendation = task_router.recommend_agent(
                    chat,
                    hub.get("profiles", []),
                    active_agent_id=str(hub.get("activeAgentId") or ""),
                    workflow_stage=stage or spec.get("stage") or "",
                    task_spec=spec,
                )
                recorded = record_dispatch_recommendation(session_id, recommendation) if session_id else {}
                recommendation = {**recommendation, **recorded}
                action = task_workflow.stage_action(chat, spec, stage=stage, recommendation=recommendation)
                self._json({"ok": True, "data": action, "recommendation": recommendation, "dbPath": str(DB_FILE)})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            return
        if path == "/api/workspace/inspect":
            info = inspect_workspace_path(str(self._read_json().get("path") or ""))
            self._json({"ok": bool(info.get("exists") and info.get("isDir")), "workspace": info, "error": info.get("error", "")})
            return
        if path == "/api/memories/save":
            try:
                item = save_project_memory(self._read_json())
                self._json({"ok": True, "data": item, "dbPath": str(DB_FILE)})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            return
        if path == "/api/memories/extract":
            try:
                body = self._read_json()
                items = extract_project_memories(str(body.get("session_id") or body.get("sessionId") or ""), str(body.get("workspace_path") or body.get("workspacePath") or ""))
                self._json({"ok": True, "data": items, "dbPath": str(DB_FILE)})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            return
        if path == "/api/memories/delete":
            ok = delete_project_memory(str(self._read_json().get("id") or ""))
            self._json({"ok": ok})
            return
        if path == "/api/attachments/parse":
            try:
                data = parse_office_attachment(self._read_json())
                self._json({"ok": True, "data": data})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            return
        self._send(404, b"Not found")

    def _headers(self, key=""):
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if key:
            h["Authorization"] = "Bearer " + key
        return h

    def _agent_runtime(self) -> agent_adapters.AgentRuntime:
        try:
            active_session_id = getattr(self, "_active_session_id", "")
            active_run_id = getattr(self, "_active_run_id", "")
        except Exception:
            active_session_id = ""
            active_run_id = ""
        return agent_adapters.AgentRuntime(
            headers=self._headers,
            ensure_hermes_api_server=ensure_hermes_api_server,
            run_cli_json=lambda command, timeout=15, cwd=None: run_cli_json_cancelable(command, timeout=timeout, cwd=cwd, session_id=active_session_id, run_id=active_run_id),
            openclaw_message_from_messages=openclaw_message_from_messages,
            openclaw_result_to_text=openclaw_result_to_text,
            openclaw_gateway_agent_turn=openclaw_gateway_agent_turn,
            openclaw_gateway_pool_status=openclaw_gateway_pool_status,
            openclaw_gateway_url=openclaw_gateway_url,
            openclaw_agent_configured=openclaw_agent_configured,
            openclaw_gateway_port_from_config=openclaw_gateway_port_from_config,
            is_port_open=is_port_open,
        )

    def _proxy_get(self, url, key=""):
        started = time.time()
        try:
            req = urllib.request.Request(url, headers=self._headers(key))
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read()
            self._send(200, raw, "application/json; charset=utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            latency_ms = int((time.time()-started)*1000)
            record_agent_run(str(locals().get("session_id_for_context", "")), str(locals().get("agent_id_for_context", "")), str(locals().get("active", {}).get("adapter", "")) if isinstance(locals().get("active"), dict) else "", "error", locals().get("input_messages", []), error=f"HTTP {e.code}: {detail}", latency_ms=latency_ms)
            self._json({"ok": False, "error": f"HTTP {e.code}", "detail": detail, "latencyMs": latency_ms}, e.code)
        except Exception as e:
            latency_ms = int((time.time()-started)*1000)
            record_agent_run(str(locals().get("session_id_for_context", "")), str(locals().get("agent_id_for_context", "")), str(locals().get("active", {}).get("adapter", "")) if isinstance(locals().get("active"), dict) else "", "error", locals().get("input_messages", []), error=str(e), latency_ms=latency_ms)
            self._json({"ok": False, "error": str(e), "latencyMs": latency_ms}, 502)

    def _resolve_target(self, body: dict | None = None) -> tuple[dict, str, str]:
        active = get_active_profile()
        body = body or {}
        base = (body.pop("base_url", None) or active.get("baseUrl") or DEFAULT_HERMES_BASE).rstrip("/")
        api_key = body.pop("api_key", None)
        if not api_key:
            api_key = active.get("apiKey", "")
        model = body.get("model") or active.get("model") or DEFAULT_MODEL
        return active, base, api_key, model

    def _health(self):
        started = time.time()
        active = get_active_profile()
        base = (self.headers.get("X-Hermes-Base") or active.get("baseUrl") or DEFAULT_HERMES_BASE).rstrip("/")
        result, status = agent_adapters.health_check(active, base, DEFAULT_MODEL, self._agent_runtime(), started=started)
        result["activeAgent"] = public_profile(active)
        self._json(result, status)

    def _chat(self):
        started = time.time()
        try:
            body = self._read_json()
            active, base, api_key, model = self._resolve_target(body)
            session_id_for_context = str(body.get("session_id") or "")
            run_id_for_context = str(body.get("run_id") or "")
            self._active_session_id = session_id_for_context
            self._active_run_id = run_id_for_context
            clear_cancel(session_id_for_context, run_id_for_context)
            agent_id_for_context = str(active.get("agentId") or active.get("id") or model or "")
            if isinstance(body.get("messages"), list):
                body["messages"] = compact_context_messages(body.get("messages"), session_id_for_context, agent_id_for_context)
            input_messages = body.get("messages") or []
            try:
                context_package_text = load_context_package(session_id_for_context, agent_id_for_context).get("text", "")
            except Exception:
                context_package_text = ""
            turn = agent_adapters.execute_chat_turn(active, body, base, api_key, model, self._agent_runtime(), started=started)
            run = turn.run
            debug = run_debug_snapshot(active=active, model=model, base=base, body=body, input_messages=input_messages, started=started, context_package_text=context_package_text)
            record_agent_run(
                str(run.get("session_id") or ""),
                str(run.get("agent_id") or ""),
                str(run.get("adapter") or active.get("adapter") or ""),
                str(run.get("status") or "success"),
                run.get("input_messages") or input_messages,
                str(run.get("output_text") or ""),
                latency_ms=int(run.get("latency_ms") or 0),
                debug=debug,
            )
            self._json(turn.response)
        except agent_adapters.UnsupportedAgentAdapter as e:
            self._json({
                "ok": False,
                "error": "当前 Agent 还不是 HTTP 可调用类型",
                "detail": e.detail,
                "latencyMs": int((time.time()-started)*1000),
            }, 501)
        except urllib.error.HTTPError as e:
            self._json({"ok": False, "error": f"HTTP {e.code}", "detail": e.read().decode("utf-8", "replace"), "latencyMs": int((time.time()-started)*1000)}, e.code)
        except Exception as e:
            self._json({"ok": False, "error": str(e), "latencyMs": int((time.time()-started)*1000)}, 502)

    def _chat_turn_sse_bridge(self, active: dict, body: dict, base: str, api_key: str, model: str, started: float, label: str):
        """Run any non-native-streaming Agent behind an SSE heartbeat bridge.

        Some local agents (OpenClaw Gateway RPC, Claude Code CLI, future Codex
        adapters, or OpenAI-compatible endpoints with streaming disabled) may
        spend minutes executing before producing a final answer. A plain browser
        fetch with no bytes returned can surface as `Failed to fetch`. This
        bridge sends progress events while the actual agent turn runs in a
        worker thread, then emits a final Chat Completions-compatible payload.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        input_messages = body.get("messages") or []
        body["stream"] = False
        session_id = str(body.get("session_id") or "")
        run_id = str(body.get("run_id") or "")
        self._active_session_id = session_id
        self._active_run_id = run_id
        clear_cancel(session_id, run_id)
        try:
            context_package_text = load_context_package(session_id, str(active.get("agentId") or active.get("id") or model or "")).get("text", "")
        except Exception:
            context_package_text = ""
        debug_base = run_debug_snapshot(active=active, model=model, base=base, body=body, input_messages=input_messages, started=started, context_package_text=context_package_text)
        events: "queue.Queue[tuple[str, dict]]" = queue.Queue(maxsize=4)

        def worker():
            try:
                if is_cancelled(session_id, run_id):
                    events.put(("cancelled", {"ok": False, "cancelled": True, "error": "任务已停止"}))
                    return
                turn = agent_adapters.execute_chat_turn(active, body, base, api_key, model, self._agent_runtime(), started=started)
                if is_cancelled(session_id, run_id):
                    record_agent_run(session_id, str(active.get("agentId") or active.get("id") or ""), str(active.get("adapter") or ""), "cancelled", input_messages, error="任务已停止，已忽略后续结果", latency_ms=int((time.time() - started) * 1000))
                    events.put(("cancelled", {"ok": False, "cancelled": True, "error": "任务已停止，已忽略后续结果"}))
                    return
                run = turn.run
                record_agent_run(
                    str(run.get("session_id") or ""),
                    str(run.get("agent_id") or ""),
                    str(run.get("adapter") or active.get("adapter") or ""),
                    str(run.get("status") or "success"),
                    run.get("input_messages") or input_messages,
                    str(run.get("output_text") or ""),
                    latency_ms=int(run.get("latency_ms") or 0),
                    debug=debug_base,
                )
                events.put(("final", turn.response))
            except Exception as exc:
                if is_cancelled(session_id, run_id):
                    events.put(("cancelled", {"ok": False, "cancelled": True, "error": "任务已停止"}))
                    return
                events.put(("error", {
                    "ok": False,
                    "error": str(exc),
                    "latencyMs": int((time.time() - started) * 1000),
                }))

        threading.Thread(target=worker, daemon=True).start()

        def write_sse(event_name: str, payload: dict):
            raw = json.dumps(payload, ensure_ascii=False)
            self.wfile.write((f"event: {event_name}\ndata: {raw}\n\n").encode("utf-8"))
            self.wfile.flush()

        write_sse("progress", {
            "statusText": f"{label} 已接收任务，正在执行；Agent Hub 会保持连接并持续等待结果。",
            "latencyMs": int((time.time() - started) * 1000),
        })
        while True:
            if is_cancelled(session_id, run_id):
                write_sse("cancelled", {
                    "ok": False,
                    "cancelled": True,
                    "error": "任务已停止",
                    "latencyMs": int((time.time() - started) * 1000),
                })
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                return
            try:
                event_name, payload = events.get(timeout=8)
            except queue.Empty:
                if is_cancelled(session_id, run_id):
                    continue
                write_sse("progress", {
                    "statusText": f"{label} 仍在执行中，连接保持中…",
                    "latencyMs": int((time.time() - started) * 1000),
                })
                continue
            write_sse(event_name, payload)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

    def _chat_stream(self):
        started = time.time()
        try:
            body = self._read_json()
            active, base, api_key, model = self._resolve_target(body)
            session_id_for_context = str(body.get("session_id") or "")
            agent_id_for_context = str(active.get("agentId") or active.get("id") or model or "")
            if isinstance(body.get("messages"), list):
                body["messages"] = compact_context_messages(body.get("messages"), session_id_for_context, agent_id_for_context)
            if active.get("adapter") != "openai-chat" or body.get("bridge") or body.get("stream") is False:
                label = str(active.get("label") or active.get("adapter") or "Agent")
                self._chat_turn_sse_bridge(active, body, base, api_key, model, started, label)
                return
            ensure_hermes_api_server(active, base)
            if not body.get("model"):
                body["model"] = model
            body["stream"] = True
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(base + "/chat/completions", data=raw, headers=self._headers(api_key), method="POST")
            upstream_timeout = int(body.get("timeout") or DEFAULT_AGENT_TIMEOUT)
            with urllib.request.urlopen(req, timeout=upstream_timeout) as r:
                content_type = r.headers.get("Content-Type", "")
                if "text/event-stream" not in content_type:
                    payload = r.read().decode("utf-8", "replace")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("X-Accel-Buffering", "no")
                    self.end_headers()
                    self.wfile.write(("event: final\ndata: " + payload + "\n\n").encode("utf-8"))
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return

                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                while True:
                    if is_cancelled(session_id_for_context, run_id_for_context):
                        self.wfile.write(("event: cancelled\ndata: " + json.dumps({"ok": False, "cancelled": True, "error": "任务已停止", "latencyMs": int((time.time()-started)*1000)}, ensure_ascii=False) + "\n\n").encode("utf-8"))
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                        return
                    chunk = r.readline()
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            self.send_response(e.code)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            payload = json.dumps({"ok": False, "error": f"HTTP {e.code}", "detail": detail, "latencyMs": int((time.time()-started)*1000)}, ensure_ascii=False)
            self.wfile.write(("event: error\ndata: " + payload + "\n\n").encode("utf-8"))
            self.wfile.flush()
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            payload = json.dumps({"ok": False, "error": str(e), "latencyMs": int((time.time()-started)*1000)}, ensure_ascii=False)
            self.wfile.write(("event: error\ndata: " + payload + "\n\n").encode("utf-8"))
            self.wfile.flush()

    def _responses(self):
        started = time.time()
        try:
            body = self._read_json()
            active, base, api_key, _model = self._resolve_target(body)
            if active.get("adapter") != "openai-chat":
                self._json({
                    "ok": False,
                    "error": "当前 Agent 还不是 HTTP 可调用类型",
                    "detail": "Responses API 目前仅支持 OpenAI-compatible profile。",
                    "latencyMs": int((time.time()-started)*1000),
                }, 501)
                return
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(base + "/responses", data=raw, headers=self._headers(api_key), method="POST")
            upstream_timeout = int(body.get("timeout") or DEFAULT_AGENT_TIMEOUT)
            with urllib.request.urlopen(req, timeout=upstream_timeout) as r:
                resp = json.loads(r.read().decode("utf-8", "replace"))
            resp.setdefault("_ui", {})["latencyMs"] = int((time.time()-started)*1000)
            self._json(resp)
        except urllib.error.HTTPError as e:
            self._json({"ok": False, "error": f"HTTP {e.code}", "detail": e.read().decode("utf-8", "replace"), "latencyMs": int((time.time()-started)*1000)}, e.code)
        except Exception as e:
            self._json({"ok": False, "error": str(e), "latencyMs": int((time.time()-started)*1000)}, 502)


if __name__ == "__main__":
    os.chdir(ROOT)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    load_agent_hub()
    print(f"Agent Hub running at http://{HOST}:{PORT}", flush=True)
    print(f"Agent Hub config: {CONFIG_FILE}", flush=True)
    print(f"Default Hermes target: {DEFAULT_HERMES_BASE} model={DEFAULT_MODEL}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
