#!/usr/bin/env python3
from __future__ import annotations

import json
import mimetypes
import os
import base64
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from shutil import which
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CONFIG_FILE = DATA_DIR / "agent-hub.json"
PORT = int(os.environ.get("HERMES_UI_PORT", "8765"))
HOST = os.environ.get("HERMES_UI_HOST", "127.0.0.1")
DEFAULT_HERMES_BASE = os.environ.get("HERMES_BASE_URL", "http://127.0.0.1:8642/v1").rstrip("/")
DEFAULT_MODEL = os.environ.get("HERMES_MODEL", "hermes-agent")


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


def http_probe_json(url: str, timeout: int = 2):
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return True, json.loads(raw or "{}")
    except Exception as exc:
        return False, str(exc)


def run_cli_json(command: list[str], timeout: int = 15):
    completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if completed.returncode != 0:
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


def discover_openclaw() -> dict | None:
    home = Path.home()
    config_path = home / ".openclaw" / "openclaw.json"
    if not config_path.exists() and not which("openclaw"):
        return None

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
    agent_id = "main"
    agent_rows = (config.get("agents", {}) or {}).get("list") if isinstance(config, dict) else None
    if isinstance(agent_rows, list) and agent_rows:
        default_agent = (
            next((item for item in agent_rows if isinstance(item, dict) and item.get("id") == "hub-lite"), None)
            or next((item for item in agent_rows if isinstance(item, dict) and item.get("default")), None)
            or agent_rows[0]
        )
        if isinstance(default_agent, dict):
            agent_id = str(default_agent.get("id") or agent_id)
            if default_agent.get("model"):
                entry_model = default_agent.get("model")
                model = entry_model.get("primary") if isinstance(entry_model, dict) else str(entry_model or model)

    return {
        "id": "detected-openclaw-local",
        "label": "OpenClaw 本机",
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
        "notes": f"检测到 OpenClaw CLI / Gateway。推荐通过官方 CLI bridge 接入，默认 agent 为 {agent_id}。",
        "gatewayAuthMode": auth.get("mode", "none"),
        "gatewayBind": bind,
        "gatewayHost": host,
        "gatewayPort": port,
    }


def discover_agents() -> list[dict]:
    found = []
    for item in (discover_hermes(), discover_openclaw()):
        if item:
            found.append(item)
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
            for field in ("notes", "configSource", "binaryPath", "status"):
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
        "health",
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
            # Persist cleanup so repeated scans/imports don't grow the settings list.
            if len(profiles) != len(raw_profiles) or active != data.get("activeAgentId"):
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


def openclaw_message_from_messages(messages) -> str:
    if not isinstance(messages, list):
        return ""
    last_user = next((m for m in reversed(messages) if isinstance(m, dict) and m.get("role") == "user"), None)
    if not last_user:
        return ""
    content = last_user.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
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
        return "\n\n".join([part for part in text_parts if part]).strip()
    return str(content or "").strip()


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


def openclaw_gateway_request(ws, method: str, params: dict, timeout: int = 120, expect_final: bool = False):
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
    try:
        import websocket
    except Exception as exc:
        raise RuntimeError("Missing Python dependency `websocket-client`; install requirements.txt or use OpenClaw CLI adapter.") from exc

    url = openclaw_gateway_url(profile)
    ws = websocket.create_connection(url, timeout=10, enable_multithread=True)
    try:
        challenge = json.loads(ws.recv())
        if challenge.get("event") != "connect.challenge":
            raise RuntimeError("OpenClaw Gateway did not send connect.challenge")
        nonce = str((challenge.get("payload") or {}).get("nonce") or "")
        auth = build_openclaw_device_connect(nonce)
        openclaw_gateway_request(ws, "connect", {
            "minProtocol": 3,
            "maxProtocol": 3,
            "client": auth["client"],
            "caps": [],
            "auth": {"token": auth["token"]},
            "role": auth["role"],
            "scopes": auth["scopes"],
            "device": auth["device"],
        }, timeout=10)
        return openclaw_gateway_request(ws, "agent", {
            "agentId": profile.get("agentId") or "main",
            "message": message or "你好",
            "thinking": "off",
            "deliver": False,
            "timeout": timeout_sec,
            "sessionId": session_id,
            "idempotencyKey": str(uuid.uuid4()),
        }, timeout=timeout_sec + 30, expect_final=True)
    finally:
        try:
            ws.close()
        except Exception:
            pass


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
            self._json({
                "ok": True,
                "data": [public_profile(item) for item in discover_agents()],
                "configPath": str(CONFIG_FILE),
                "latencyMs": int((time.time() - started) * 1000),
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
        self._send(404, b"Not found")

    def _headers(self, key=""):
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if key:
            h["Authorization"] = "Bearer " + key
        return h

    def _proxy_get(self, url, key=""):
        started = time.time()
        try:
            req = urllib.request.Request(url, headers=self._headers(key))
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read()
            self._send(200, raw, "application/json; charset=utf-8")
        except urllib.error.HTTPError as e:
            self._json({"ok": False, "error": f"HTTP {e.code}", "detail": e.read().decode("utf-8", "replace"), "latencyMs": int((time.time()-started)*1000)}, e.code)
        except Exception as e:
            self._json({"ok": False, "error": str(e), "latencyMs": int((time.time()-started)*1000)}, 502)

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
        result = {
            "ok": True,
            "ui": "healthy",
            "baseUrl": base,
            "model": active.get("model") or DEFAULT_MODEL,
            "activeAgent": public_profile(active),
        }
        if active.get("adapter") in {"openclaw-cli", "openclaw-gateway-rpc"}:
            binary = active.get("binaryPath") or active.get("baseUrl") or which("openclaw") or "openclaw"
            binary_exists = bool(which(binary) or Path(binary).exists() or active.get("adapter") == "openclaw-gateway-rpc")
            agent_id = active.get("agentId") or "main"
            agent_configured = openclaw_agent_configured(agent_id)
            gateway_port = openclaw_gateway_port_from_config()
            gateway_reachable = is_port_open("127.0.0.1", gateway_port, timeout=0.2)
            result["ok"] = bool(binary_exists and agent_configured and (active.get("adapter") != "openclaw-gateway-rpc" or gateway_reachable))
            result["openclaw"] = {
                "mode": "gateway-rpc" if active.get("adapter") == "openclaw-gateway-rpc" else "cli-bridge",
                "binary": binary,
                "binaryExists": binary_exists,
                "agentId": agent_id,
                "agentConfigured": agent_configured,
                "gatewayPort": gateway_port,
                "gatewayReachable": gateway_reachable,
            }
            result["hint"] = "OpenClaw Gateway RPC 已配置。" if active.get("adapter") == "openclaw-gateway-rpc" else "OpenClaw CLI bridge 已配置。右上角状态使用轻量检测，不再执行耗时的 `openclaw agents list --json`。"
            if not binary_exists:
                result["openclawError"] = f"OpenClaw binary not found: {binary}"
            elif not agent_configured:
                result["openclawError"] = f"OpenClaw agent not configured: {agent_id}"
            elif active.get("adapter") == "openclaw-gateway-rpc" and not gateway_reachable:
                result["openclawError"] = f"OpenClaw Gateway not reachable on port {gateway_port}"
            result["latencyMs"] = int((time.time()-started)*1000)
            self._json(result, 200 if result["ok"] else 502)
            return
        if active.get("adapter") != "openai-chat":
            result["ok"] = False
            result["hint"] = "当前选中的 Agent 使用的是 OpenClaw Gateway / 非 HTTP 适配器。配置已可管理，但执行桥接适配需要下一阶段接入。"
            result["latencyMs"] = int((time.time()-started)*1000)
            self._json(result, 501)
            return
        try:
            req = urllib.request.Request(base.replace("/v1", "") + "/health", headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                result["hermesHealth"] = json.loads(r.read().decode("utf-8", "replace") or "{}")
        except Exception as e:
            result["ok"] = False
            result["hermesHealthError"] = str(e)
        result["latencyMs"] = int((time.time()-started)*1000)
        self._json(result, 200 if result["ok"] else 502)

    def _chat(self):
        started = time.time()
        try:
            body = self._read_json()
            active, base, api_key, model = self._resolve_target(body)
            if active.get("adapter") == "openclaw-gateway-rpc":
                session_id = str(body.get("session_id") or f"hub-{int(time.time())}")
                message = openclaw_message_from_messages(body.get("messages"))
                agent_id = active.get("agentId") or "main"
                timeout_sec = int(body.get("timeout", 300) or 300)
                raw = openclaw_gateway_agent_turn(active, message or "你好", session_id, timeout_sec=timeout_sec)
                reply = openclaw_result_to_text(raw)
                self._json({
                    "id": f"chatcmpl-openclaw-rpc-{int(time.time()*1000)}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": active.get("model") or agent_id,
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": reply},
                        "finish_reason": "stop",
                    }],
                    "_ui": {
                        "latencyMs": int((time.time()-started)*1000),
                        "agentLabel": active.get("label"),
                        "adapter": "openclaw-gateway-rpc",
                        "sessionId": session_id,
                        "agentId": agent_id,
                        "gatewayUrl": openclaw_gateway_url(active),
                    }
                })
                return
            if active.get("adapter") == "openclaw-cli":
                binary = active.get("binaryPath") or active.get("baseUrl") or which("openclaw") or "openclaw"
                session_id = str(body.get("session_id") or f"hub-{int(time.time())}")
                message = openclaw_message_from_messages(body.get("messages"))
                agent_id = active.get("agentId") or "main"
                command = [
                    binary, "agent",
                    "--agent", agent_id,
                    "--session-id", session_id,
                    "--message", message or "你好",
                    "--json",
                    "--timeout", str(int(body.get("timeout", 300) or 300)),
                ]
                raw = run_cli_json(command, timeout=min(int(body.get("timeout", 300) or 300) + 30, 900))
                reply = openclaw_result_to_text(raw)
                safe_command = [
                    "openclaw", "agent",
                    "--agent", agent_id,
                    "--session-id", session_id,
                    "--message", "<user-message>",
                    "--json",
                    "--timeout", str(int(body.get("timeout", 300) or 300)),
                ]
                self._json({
                    "id": f"chatcmpl-openclaw-{int(time.time()*1000)}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": active.get("model") or agent_id,
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": reply},
                        "finish_reason": "stop",
                    }],
                    "_ui": {
                        "latencyMs": int((time.time()-started)*1000),
                        "agentLabel": active.get("label"),
                        "adapter": "openclaw-cli",
                        "sessionId": session_id,
                        "agentId": agent_id,
                        "command": " ".join(safe_command),
                    }
                })
                return
            if active.get("adapter") != "openai-chat":
                self._json({
                    "ok": False,
                    "error": "当前 Agent 还不是 HTTP 可调用类型",
                    "detail": "已检测到 OpenClaw Gateway，但当前版本还未接上 WebSocket -> Chat UI 的执行桥接层。建议先切回 Hermes profile。",
                    "latencyMs": int((time.time()-started)*1000),
                }, 501)
                return
            if not body.get("model"):
                body["model"] = model
            if "stream" not in body:
                body["stream"] = False
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(base + "/chat/completions", data=raw, headers=self._headers(api_key), method="POST")
            with urllib.request.urlopen(req, timeout=300) as r:
                resp = json.loads(r.read().decode("utf-8", "replace"))
            resp.setdefault("_ui", {})["latencyMs"] = int((time.time()-started)*1000)
            resp["_ui"]["agentLabel"] = active.get("label")
            self._json(resp)
        except urllib.error.HTTPError as e:
            self._json({"ok": False, "error": f"HTTP {e.code}", "detail": e.read().decode("utf-8", "replace"), "latencyMs": int((time.time()-started)*1000)}, e.code)
        except Exception as e:
            self._json({"ok": False, "error": str(e), "latencyMs": int((time.time()-started)*1000)}, 502)

    def _chat_stream(self):
        started = time.time()
        try:
            body = self._read_json()
            active, base, api_key, model = self._resolve_target(body)
            if active.get("adapter") in {"openclaw-cli", "openclaw-gateway-rpc"}:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                payload = json.dumps({
                    "ok": False,
                    "error": "OpenClaw 当前不提供浏览器流式分片",
                    "detail": "前端将自动切换为普通响应模式；Gateway RPC / CLI 都可以直接调用 OpenClaw。",
                    "latencyMs": int((time.time()-started)*1000),
                }, ensure_ascii=False)
                self.wfile.write(("event: error\ndata: " + payload + "\n\n").encode("utf-8"))
                self.wfile.flush()
                return
            if active.get("adapter") != "openai-chat":
                self.send_response(501)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                payload = json.dumps({
                    "ok": False,
                    "error": "当前 Agent 还不是 HTTP 可调用类型",
                    "detail": "已检测到 OpenClaw Gateway，但当前版本还未接上 WebSocket -> Chat UI 的执行桥接层。",
                    "latencyMs": int((time.time()-started)*1000),
                }, ensure_ascii=False)
                self.wfile.write(("event: error\ndata: " + payload + "\n\n").encode("utf-8"))
                self.wfile.flush()
                return
            if not body.get("model"):
                body["model"] = model
            body["stream"] = True
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(base + "/chat/completions", data=raw, headers=self._headers(api_key), method="POST")
            with urllib.request.urlopen(req, timeout=300) as r:
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
            with urllib.request.urlopen(req, timeout=300) as r:
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
