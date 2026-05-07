from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

import context_engine
import task_workflow
from context_engine import derive_task_goal, generate_session_summary, text_from_content, truncate_text


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_FILE = DATA_DIR / "agent-hub.db"


def configure(data_dir: Path | str | None = None, db_file: Path | str | None = None):
    global DATA_DIR, DB_FILE
    if data_dir is not None:
        DATA_DIR = Path(data_dir)
    if db_file is not None:
        DB_FILE = Path(db_file)


def db_connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with db_connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS kv (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            active_agent_id TEXT,
            summary TEXT DEFAULT '',
            task_goal TEXT DEFAULT '',
            task_status TEXT DEFAULT 'active',
            next_step TEXT DEFAULT '',
            workspace_path TEXT DEFAULT '',
            workspace_name TEXT DEFAULT '',
            workspace_git_repo INTEGER DEFAULT 0,
            workspace_git_branch TEXT DEFAULT '',
            workspace_git_dirty INTEGER DEFAULT 0,
            workspace_checked_at INTEGER DEFAULT 0,
            approval_policy TEXT DEFAULT 'auto',
            context_strategy TEXT DEFAULT 'standard',
            handoff_summary TEXT DEFAULT '',
            handoff_from_agent_id TEXT DEFAULT '',
            handoff_from_label TEXT DEFAULT '',
            handoff_to_agent_id TEXT DEFAULT '',
            handoff_to_label TEXT DEFAULT '',
            handoff_at INTEGER DEFAULT 0,
            handoff_dismissed INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content_json TEXT NOT NULL,
            meta TEXT DEFAULT '',
            agent_id TEXT DEFAULT '',
            message_order INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session_created ON messages(session_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_messages_session_order ON messages(session_id, message_order, created_at);
        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            message_id TEXT DEFAULT '',
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            mime_type TEXT DEFAULT '',
            content_text TEXT DEFAULT '',
            data_url TEXT DEFAULT '',
            include_in_context INTEGER DEFAULT 1,
            created_at INTEGER NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_artifacts_session_created ON artifacts(session_id, created_at);
        CREATE TABLE IF NOT EXISTS agent_runs (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            agent_id TEXT DEFAULT '',
            adapter TEXT DEFAULT '',
            status TEXT NOT NULL,
            input_summary TEXT DEFAULT '',
            output_summary TEXT DEFAULT '',
            latency_ms INTEGER DEFAULT 0,
            error TEXT DEFAULT '',
            debug_json TEXT DEFAULT '{}',
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_runs_session_created ON agent_runs(session_id, created_at);
        CREATE TABLE IF NOT EXISTS handoffs (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            from_agent_id TEXT DEFAULT '',
            to_agent_id TEXT DEFAULT '',
            handoff_summary TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_handoffs_session_created ON handoffs(session_id, created_at);
        CREATE TABLE IF NOT EXISTS project_memories (
            id TEXT PRIMARY KEY,
            workspace_path TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            source_session_id TEXT DEFAULT '',
            confidence REAL DEFAULT 0.7,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_project_memories_workspace_updated ON project_memories(workspace_path, updated_at);
        CREATE VIRTUAL TABLE IF NOT EXISTS project_memory_fts USING fts5(id UNINDEXED, workspace_path UNINDEXED, kind, title, content);
        CREATE TABLE IF NOT EXISTS task_specs (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL UNIQUE,
            goal TEXT DEFAULT '',
            task_type TEXT DEFAULT 'chat',
            stage TEXT DEFAULT 'draft',
            constraints_json TEXT DEFAULT '[]',
            acceptance_json TEXT DEFAULT '[]',
            files_json TEXT DEFAULT '[]',
            risk_notes TEXT DEFAULT '',
            final_deliverable TEXT DEFAULT '',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS dispatch_recommendations (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            task_type TEXT NOT NULL,
            primary_agent_id TEXT NOT NULL,
            assistant_agent_ids_json TEXT DEFAULT '[]',
            context_strategy TEXT DEFAULT 'standard',
            confidence REAL DEFAULT 0.6,
            reasons_json TEXT DEFAULT '[]',
            warnings_json TEXT DEFAULT '[]',
            accepted INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_dispatch_session_created ON dispatch_recommendations(session_id, created_at);
        CREATE TABLE IF NOT EXISTS task_workflow_events (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            from_stage TEXT DEFAULT '',
            to_stage TEXT NOT NULL,
            actor TEXT DEFAULT 'user',
            note TEXT DEFAULT '',
            created_at INTEGER NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_workflow_session_created ON task_workflow_events(session_id, created_at);
        """)
        session_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "task_goal" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN task_goal TEXT DEFAULT ''")
        if "task_status" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN task_status TEXT DEFAULT 'active'")
        if "next_step" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN next_step TEXT DEFAULT ''")
        if "workspace_path" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN workspace_path TEXT DEFAULT ''")
        if "workspace_name" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN workspace_name TEXT DEFAULT ''")
        if "workspace_git_repo" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN workspace_git_repo INTEGER DEFAULT 0")
        if "workspace_git_branch" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN workspace_git_branch TEXT DEFAULT ''")
        if "workspace_git_dirty" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN workspace_git_dirty INTEGER DEFAULT 0")
        if "workspace_checked_at" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN workspace_checked_at INTEGER DEFAULT 0")
        if "approval_policy" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN approval_policy TEXT DEFAULT 'auto'")
        if "context_strategy" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN context_strategy TEXT DEFAULT 'standard'")
        if "handoff_summary" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN handoff_summary TEXT DEFAULT ''")
        if "handoff_from_agent_id" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN handoff_from_agent_id TEXT DEFAULT ''")
        if "handoff_from_label" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN handoff_from_label TEXT DEFAULT ''")
        if "handoff_to_agent_id" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN handoff_to_agent_id TEXT DEFAULT ''")
        if "handoff_to_label" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN handoff_to_label TEXT DEFAULT ''")
        if "handoff_at" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN handoff_at INTEGER DEFAULT 0")
        if "handoff_dismissed" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN handoff_dismissed INTEGER DEFAULT 0")
        message_columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
        if "message_order" not in message_columns:
            conn.execute("ALTER TABLE messages ADD COLUMN message_order INTEGER DEFAULT 0")
            session_ids = [row["session_id"] for row in conn.execute("SELECT DISTINCT session_id FROM messages").fetchall()]
            for session_id in session_ids:
                rows = conn.execute(
                    "SELECT id FROM messages WHERE session_id=? ORDER BY created_at ASC, id ASC",
                    (session_id,),
                ).fetchall()
                for index, msg_row in enumerate(rows, start=1):
                    conn.execute("UPDATE messages SET message_order=? WHERE id=?", (index, msg_row["id"]))
        artifact_columns = {row["name"] for row in conn.execute("PRAGMA table_info(artifacts)").fetchall()}
        if "include_in_context" not in artifact_columns:
            conn.execute("ALTER TABLE artifacts ADD COLUMN include_in_context INTEGER DEFAULT 1")
        run_columns = {row["name"] for row in conn.execute("PRAGMA table_info(agent_runs)").fetchall()}
        if "debug_json" not in run_columns:
            conn.execute("ALTER TABLE agent_runs ADD COLUMN debug_json TEXT DEFAULT '{}'")


def _json_list(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def serialize_task_spec(row) -> dict:
    if not row:
        return {}
    return {
        "id": row["id"],
        "sessionId": row["session_id"],
        "goal": row["goal"] or "",
        "taskType": row["task_type"] or "chat",
        "stage": row["stage"] or "draft",
        "constraints": _json_list(row["constraints_json"]),
        "acceptance": _json_list(row["acceptance_json"]),
        "files": _json_list(row["files_json"]),
        "riskNotes": row["risk_notes"] or "",
        "finalDeliverable": row["final_deliverable"] or "",
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def serialize_dispatch_recommendation(row) -> dict:
    if not row:
        return {}
    return {
        "id": row["id"],
        "sessionId": row["session_id"],
        "taskType": row["task_type"],
        "primaryAgentId": row["primary_agent_id"],
        "assistantAgentIds": _json_list(row["assistant_agent_ids_json"]),
        "contextStrategy": row["context_strategy"] or "standard",
        "confidence": float(row["confidence"] or 0),
        "reasons": _json_list(row["reasons_json"]),
        "warnings": _json_list(row["warnings_json"]),
        "accepted": bool(row["accepted"] or 0),
        "createdAt": row["created_at"],
    }


def serialize_workflow_event(row) -> dict:
    if not row:
        return {}
    return {
        "id": row["id"],
        "sessionId": row["session_id"],
        "fromStage": row["from_stage"] or "",
        "toStage": row["to_stage"] or "draft",
        "actor": row["actor"] or "user",
        "note": row["note"] or "",
        "createdAt": row["created_at"],
    }


def normalize_workspace_path(path: str) -> str:
    text = str(path or "").strip()
    if text.startswith("~"):
        text = str(Path(text).expanduser())
    return str(Path(text).resolve()) if text else ""


def set_kv(conn, key: str, value: str):
    conn.execute(
        "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def get_kv(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row else default


def serialize_artifact(row: sqlite3.Row) -> dict:
    item = {
        "id": row["id"],
        "messageId": row["message_id"] or "",
        "kind": row["kind"],
        "name": row["name"],
        "mime": row["mime_type"] or "",
        "createdAt": row["created_at"],
    }
    if row["content_text"]:
        item["text"] = row["content_text"]
    if row["data_url"]:
        item["dataUrl"] = row["data_url"]
    item["includeInContext"] = row["include_in_context"] != 0
    return item


def serialize_message(row: sqlite3.Row) -> dict:
    try:
        content = json.loads(row["content_json"])
    except Exception:
        content = row["content_json"]
    item = {
        "id": row["id"],
        "role": row["role"],
        "content": content,
        "meta": row["meta"] or "",
        "createdAt": row["created_at"],
        "messageOrder": row["message_order"] if "message_order" in row.keys() else 0,
    }
    if row["agent_id"]:
        item["agentId"] = row["agent_id"]
    return item


def load_sessions_from_db() -> dict:
    init_db()
    with db_connect() as conn:
        rows = conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
        sessions = []
        for row in rows:
            messages = conn.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY message_order ASC, created_at ASC, id ASC",
                (row["id"],),
            ).fetchall()
            artifacts = conn.execute(
                "SELECT * FROM artifacts WHERE session_id=? ORDER BY created_at ASC",
                (row["id"],),
            ).fetchall()
            sessions.append({
                "id": row["id"],
                "title": row["title"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
                "activeAgentId": row["active_agent_id"] or "",
                "summary": row["summary"] or "",
                "taskGoal": row["task_goal"] or "",
                "taskStatus": row["task_status"] or "active",
                "nextStep": row["next_step"] or "",
                "workspacePath": row["workspace_path"] or "",
                "workspaceName": row["workspace_name"] or "",
                "workspaceGitRepo": bool(row["workspace_git_repo"] or 0),
                "workspaceGitBranch": row["workspace_git_branch"] or "",
                "workspaceGitDirty": bool(row["workspace_git_dirty"] or 0),
                "workspaceCheckedAt": row["workspace_checked_at"] or 0,
                "approvalPolicy": row["approval_policy"] or "auto",
                "contextStrategy": row["context_strategy"] or "standard",
                "handoffSummary": row["handoff_summary"] or "",
                "handoffFromAgentId": row["handoff_from_agent_id"] or "",
                "handoffFromLabel": row["handoff_from_label"] or "",
                "handoffToAgentId": row["handoff_to_agent_id"] or "",
                "handoffToLabel": row["handoff_to_label"] or "",
                "handoffAt": row["handoff_at"] or 0,
                "handoffDismissed": bool(row["handoff_dismissed"] or 0),
                "artifacts": [serialize_artifact(a) for a in artifacts],
                "messages": [serialize_message(m) for m in messages],
            })
        return {"sessions": sessions, "activeId": get_kv(conn, "active_session_id", "")}


def save_sessions_to_db(payload: dict) -> dict:
    init_db()
    sessions = [item for item in payload.get("sessions", []) if isinstance(item, dict)]
    now = int(time.time() * 1000)
    with db_connect() as conn:
        incoming_ids = []
        for chat in sessions:
            session_id = str(chat.get("id") or f"s-{uuid.uuid4()}")
            incoming_ids.append(session_id)
            title = str(chat.get("title") or "新的会话")
            created_at = int(chat.get("createdAt") or now)
            updated_at = int(chat.get("updatedAt") or now)
            active_agent_id = str(chat.get("activeAgentId") or payload.get("activeAgentId") or "")
            summary = str(chat.get("summary") or generate_session_summary(chat))
            task_goal = str(chat.get("taskGoal") or derive_task_goal(chat))
            task_status = str(chat.get("taskStatus") or "active")
            next_step = str(chat.get("nextStep") or "")
            workspace_path = str(chat.get("workspacePath") or "")
            workspace_name = str(chat.get("workspaceName") or (Path(workspace_path).name if workspace_path else ""))
            workspace_git_repo = 1 if chat.get("workspaceGitRepo") else 0
            workspace_git_branch = str(chat.get("workspaceGitBranch") or "")
            workspace_git_dirty = 1 if chat.get("workspaceGitDirty") else 0
            workspace_checked_at = int(chat.get("workspaceCheckedAt") or 0)
            approval_policy = str(chat.get("approvalPolicy") or "auto")
            context_strategy = str(chat.get("contextStrategy") or "standard")
            handoff_summary = str(chat.get("handoffSummary") or "")
            handoff_from_agent_id = str(chat.get("handoffFromAgentId") or "")
            handoff_from_label = str(chat.get("handoffFromLabel") or "")
            handoff_to_agent_id = str(chat.get("handoffToAgentId") or "")
            handoff_to_label = str(chat.get("handoffToLabel") or "")
            handoff_at = int(chat.get("handoffAt") or 0)
            handoff_dismissed = 1 if chat.get("handoffDismissed") else 0
            conn.execute("""
                INSERT INTO sessions(id,title,active_agent_id,summary,task_goal,task_status,next_step,workspace_path,workspace_name,workspace_git_repo,workspace_git_branch,workspace_git_dirty,workspace_checked_at,approval_policy,context_strategy,handoff_summary,handoff_from_agent_id,handoff_from_label,handoff_to_agent_id,handoff_to_label,handoff_at,handoff_dismissed,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  title=excluded.title,
                  active_agent_id=excluded.active_agent_id,
                  summary=excluded.summary,
                  task_goal=excluded.task_goal,
                  task_status=excluded.task_status,
                  next_step=excluded.next_step,
                  workspace_path=excluded.workspace_path,
                  workspace_name=excluded.workspace_name,
                  workspace_git_repo=excluded.workspace_git_repo,
                  workspace_git_branch=excluded.workspace_git_branch,
                  workspace_git_dirty=excluded.workspace_git_dirty,
                  workspace_checked_at=excluded.workspace_checked_at,
                  approval_policy=excluded.approval_policy,
                  context_strategy=excluded.context_strategy,
                  handoff_summary=excluded.handoff_summary,
                  handoff_from_agent_id=excluded.handoff_from_agent_id,
                  handoff_from_label=excluded.handoff_from_label,
                  handoff_to_agent_id=excluded.handoff_to_agent_id,
                  handoff_to_label=excluded.handoff_to_label,
                  handoff_at=excluded.handoff_at,
                  handoff_dismissed=excluded.handoff_dismissed,
                  updated_at=excluded.updated_at
            """, (session_id, title, active_agent_id, summary, task_goal, task_status, next_step, workspace_path, workspace_name, workspace_git_repo, workspace_git_branch, workspace_git_dirty, workspace_checked_at, approval_policy, context_strategy, handoff_summary, handoff_from_agent_id, handoff_from_label, handoff_to_agent_id, handoff_to_label, handoff_at, handoff_dismissed, created_at, updated_at))
            conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM artifacts WHERE session_id=?", (session_id,))
            message_index = 0
            for msg in chat.get("messages", []):
                if not isinstance(msg, dict) or msg.get("role") == "progress":
                    continue
                message_index += 1
                msg_id = str(msg.get("id") or f"{session_id}-m-{message_index}")
                conn.execute("""
                    INSERT OR REPLACE INTO messages(id,session_id,role,content_json,meta,agent_id,message_order,created_at)
                    VALUES(?,?,?,?,?,?,?,?)
                """, (
                    msg_id,
                    session_id,
                    str(msg.get("role") or "assistant"),
                    json.dumps(msg.get("content", ""), ensure_ascii=False),
                    str(msg.get("meta") or ""),
                    str(msg.get("agentId") or active_agent_id or ""),
                    message_index,
                    int(msg.get("createdAt") or (created_at + message_index)),
                ))
            artifact_rows = chat.get("artifacts") if isinstance(chat.get("artifacts"), list) else []
            for index, artifact in enumerate(artifact_rows, start=1):
                if not isinstance(artifact, dict):
                    continue
                artifact_id = str(artifact.get("id") or f"{session_id}-a-{index}")
                conn.execute("""
                    INSERT OR REPLACE INTO artifacts(id,session_id,message_id,kind,name,mime_type,content_text,data_url,include_in_context,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)
                """, (
                    artifact_id,
                    session_id,
                    str(artifact.get("messageId") or ""),
                    str(artifact.get("kind") or "file"),
                    str(artifact.get("name") or "attachment"),
                    str(artifact.get("mime") or ""),
                    str(artifact.get("text") or ""),
                    str(artifact.get("dataUrl") or ""),
                    0 if artifact.get("includeInContext") is False else 1,
                    int(artifact.get("createdAt") or (created_at + index)),
                ))
        if incoming_ids:
            placeholders = ",".join(["?"] * len(incoming_ids))
            conn.execute(f"DELETE FROM sessions WHERE id NOT IN ({placeholders})", incoming_ids)
        active_id = str(payload.get("activeId") or (incoming_ids[0] if incoming_ids else ""))
        set_kv(conn, "active_session_id", active_id)
    return load_sessions_from_db()


def get_session(session_id: str) -> dict | None:
    if not session_id:
        return None
    data = load_sessions_from_db()
    return next((item for item in data.get("sessions", []) if item.get("id") == session_id), None)


def save_task_spec(payload: dict) -> dict:
    init_db()
    session_id = str(payload.get("sessionId") or payload.get("session_id") or "")
    if not session_id:
        raise ValueError("缺少 sessionId")
    now = int(time.time() * 1000)
    spec_id = str(payload.get("id") or uuid.uuid4())
    constraints = payload.get("constraints") if isinstance(payload.get("constraints"), list) else []
    acceptance = payload.get("acceptance") if isinstance(payload.get("acceptance"), list) else []
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    with db_connect() as conn:
        existing = conn.execute("SELECT id,created_at FROM task_specs WHERE session_id=?", (session_id,)).fetchone()
        if existing:
            spec_id = existing["id"]
            created_at = existing["created_at"]
        else:
            created_at = now
        conn.execute(
            """
            INSERT INTO task_specs(id,session_id,goal,task_type,stage,constraints_json,acceptance_json,files_json,risk_notes,final_deliverable,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(session_id) DO UPDATE SET
              goal=excluded.goal,
              task_type=excluded.task_type,
              stage=excluded.stage,
              constraints_json=excluded.constraints_json,
              acceptance_json=excluded.acceptance_json,
              files_json=excluded.files_json,
              risk_notes=excluded.risk_notes,
              final_deliverable=excluded.final_deliverable,
              updated_at=excluded.updated_at
            """,
            (
                spec_id,
                session_id,
                str(payload.get("goal") or ""),
                str(payload.get("taskType") or payload.get("task_type") or "chat"),
                str(payload.get("stage") or "draft"),
                json.dumps(constraints, ensure_ascii=False),
                json.dumps(acceptance, ensure_ascii=False),
                json.dumps(files, ensure_ascii=False),
                str(payload.get("riskNotes") or payload.get("risk_notes") or ""),
                str(payload.get("finalDeliverable") or payload.get("final_deliverable") or ""),
                created_at,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM task_specs WHERE session_id=?", (session_id,)).fetchone()
    return serialize_task_spec(row)


def load_task_spec(session_id: str) -> dict:
    init_db()
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM task_specs WHERE session_id=?", (str(session_id or ""),)).fetchone()
    return serialize_task_spec(row)


def load_task_workflow(session_id: str, limit: int = 30) -> dict:
    init_db()
    session_id = str(session_id or "")
    spec = load_task_spec(session_id)
    current_stage = task_workflow.normalize_stage(spec.get("stage") or "draft")
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM task_workflow_events WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
            (session_id, max(1, min(int(limit or 30), 100))),
        ).fetchall()
    return {
        "sessionId": session_id,
        "currentStage": current_stage,
        "current": task_workflow.stage_meta(current_stage),
        "definition": task_workflow.workflow_definition(),
        "events": [serialize_workflow_event(row) for row in rows],
    }


def transition_task_stage(payload: dict) -> dict:
    init_db()
    session_id = str(payload.get("sessionId") or payload.get("session_id") or "")
    if not session_id:
        raise ValueError("缺少 sessionId")
    to_stage = task_workflow.normalize_stage(payload.get("stage") or payload.get("toStage") or payload.get("to_stage"))
    actor = str(payload.get("actor") or "user")[:80]
    note = truncate_text(str(payload.get("note") or ""), 500)
    now = int(time.time() * 1000)
    with db_connect() as conn:
        session = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not session:
            raise ValueError(f"未找到会话：{session_id}")
        spec_row = conn.execute("SELECT * FROM task_specs WHERE session_id=?", (session_id,)).fetchone()
        if spec_row:
            from_stage = task_workflow.normalize_stage(spec_row["stage"] or "draft")
            conn.execute(
                "UPDATE task_specs SET stage=?, updated_at=? WHERE session_id=?",
                (to_stage, now, session_id),
            )
        else:
            from_stage = "draft"
            conn.execute(
                """
                INSERT INTO task_specs(id,session_id,goal,task_type,stage,constraints_json,acceptance_json,files_json,risk_notes,final_deliverable,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(uuid.uuid4()),
                    session_id,
                    str(session["task_goal"] or ""),
                    "chat",
                    to_stage,
                    "[]",
                    "[]",
                    "[]",
                    "",
                    "",
                    now,
                    now,
                ),
            )
        status = task_workflow.status_for_stage(to_stage)
        next_step = note or task_workflow.next_step_for_stage(to_stage)
        conn.execute(
            "UPDATE sessions SET task_status=?, next_step=?, updated_at=? WHERE id=?",
            (status, next_step, now, session_id),
        )
        conn.execute(
            """
            INSERT INTO task_workflow_events(id,session_id,from_stage,to_stage,actor,note,created_at)
            VALUES(?,?,?,?,?,?,?)
            """,
            (str(uuid.uuid4()), session_id, from_stage, to_stage, actor, note, now),
        )
    return load_task_workflow(session_id)


def record_dispatch_recommendation(session_id: str, recommendation: dict) -> dict:
    init_db()
    if not session_id:
        raise ValueError("缺少 sessionId")
    rec_id = str(recommendation.get("id") or uuid.uuid4())
    now = int(time.time() * 1000)
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO dispatch_recommendations(id,session_id,task_type,primary_agent_id,assistant_agent_ids_json,context_strategy,confidence,reasons_json,warnings_json,accepted,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                rec_id,
                session_id,
                str(recommendation.get("taskType") or "chat"),
                str(recommendation.get("primaryAgentId") or ""),
                json.dumps(recommendation.get("assistantAgentIds") or [], ensure_ascii=False),
                str(recommendation.get("contextStrategy") or "standard"),
                float(recommendation.get("confidence") or 0.6),
                json.dumps(recommendation.get("reasons") or [], ensure_ascii=False),
                json.dumps(recommendation.get("warnings") or [], ensure_ascii=False),
                1 if recommendation.get("accepted") else 0,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM dispatch_recommendations WHERE id=?", (rec_id,)).fetchone()
    return serialize_dispatch_recommendation(row)


def load_dispatch_recommendation(recommendation_id: str) -> dict:
    init_db()
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM dispatch_recommendations WHERE id=?", (str(recommendation_id or ""),)).fetchone()
    return serialize_dispatch_recommendation(row)


def accept_dispatch_recommendation(session_id: str, recommendation_id: str = "") -> dict:
    init_db()
    with db_connect() as conn:
        if recommendation_id:
            row = conn.execute(
                "SELECT * FROM dispatch_recommendations WHERE id=? AND session_id=?",
                (str(recommendation_id), str(session_id)),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM dispatch_recommendations WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
                (str(session_id),),
            ).fetchone()
        if not row:
            raise ValueError("未找到推荐记录")
        now = int(time.time() * 1000)
        conn.execute("UPDATE dispatch_recommendations SET accepted=0 WHERE session_id=?", (str(session_id),))
        conn.execute("UPDATE dispatch_recommendations SET accepted=1 WHERE id=?", (row["id"],))
        conn.execute(
            "UPDATE sessions SET active_agent_id=?, context_strategy=?, task_status=?, updated_at=? WHERE id=?",
            (row["primary_agent_id"], row["context_strategy"] or "standard", "planned", now, str(session_id)),
        )
        refreshed = conn.execute("SELECT * FROM dispatch_recommendations WHERE id=?", (row["id"],)).fetchone()
    return serialize_dispatch_recommendation(refreshed)


def latest_session_context(session_id: str, to_agent_id: str = "") -> tuple[str, str]:
    if not session_id:
        return "", ""
    init_db()
    with db_connect() as conn:
        session = conn.execute("SELECT summary FROM sessions WHERE id=?", (session_id,)).fetchone()
        if to_agent_id:
            handoff = conn.execute(
                "SELECT handoff_summary FROM handoffs WHERE session_id=? AND to_agent_id=? ORDER BY created_at DESC LIMIT 1",
                (session_id, to_agent_id),
            ).fetchone()
        else:
            handoff = conn.execute(
                "SELECT handoff_summary FROM handoffs WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        return (session["summary"] if session else "", handoff["handoff_summary"] if handoff else "")


def session_artifact_context(session_id: str, limit: int = 6) -> str:
    if not session_id:
        return ""
    init_db()
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT kind,name,mime_type,content_text FROM artifacts WHERE session_id=? AND include_in_context != 0 ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return context_engine.format_artifact_context(rows)


def create_handoff(payload: dict) -> dict:
    init_db()
    session_id = str(payload.get("sessionId") or "")
    from_agent_id = str(payload.get("fromAgentId") or "")
    to_agent_id = str(payload.get("toAgentId") or "")
    chat = payload.get("chat") if isinstance(payload.get("chat"), dict) else {}
    # Prefer a fresh summary from the latest chat payload. Stored summaries can
    # be stale (for example an old "你好" task goal) and would otherwise poison
    # cross-agent handoff.
    generated_summary = generate_session_summary(chat)
    existing_summary = str(chat.get("summary") or "").strip()
    summary = generated_summary
    if existing_summary and existing_summary not in generated_summary:
        summary = f"{generated_summary}\n既有摘要：{existing_summary}"
    from_label = str(payload.get("fromAgentLabel") or from_agent_id or "上一 Agent")
    to_label = str(payload.get("toAgentLabel") or to_agent_id or "新 Agent")
    handoff = "\n".join([
        f"交接方向：{from_label} → {to_label}",
        "当前任务状态：",
        summary,
        "交接要求：请基于以上任务状态继续推进，不要要求用户重复已经提供的信息；如果上下文不足，先明确缺口再继续。",
    ]).strip()
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO handoffs(id,session_id,from_agent_id,to_agent_id,handoff_summary,created_at) VALUES(?,?,?,?,?,?)",
            (str(uuid.uuid4()), session_id, from_agent_id, to_agent_id, handoff, int(time.time() * 1000)),
        )
        if session_id:
            now = int(time.time() * 1000)
            conn.execute(
                "UPDATE sessions SET summary=?, active_agent_id=?, handoff_summary=?, handoff_from_agent_id=?, handoff_from_label=?, handoff_to_agent_id=?, handoff_to_label=?, handoff_at=?, handoff_dismissed=0, updated_at=? WHERE id=?",
                (summary, to_agent_id, handoff, from_agent_id, from_label, to_agent_id, to_label, now, now, session_id)
            )
    return {"summary": summary, "handoff": handoff}




def load_handoffs(session_id: str = "", limit: int = 50) -> list[dict]:
    init_db()
    limit = max(1, min(int(limit or 50), 200))
    with db_connect() as conn:
        if session_id:
            rows = conn.execute(
                "SELECT * FROM handoffs WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM handoffs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(row) for row in rows]


def load_context_package(session_id: str, target_agent_id: str = "", target_agent_label: str = "", strategy: str = "") -> dict:
    init_db()
    if not session_id:
        raise ValueError("缺少 session_id")
    data = load_sessions_from_db()
    chat = next((item for item in data.get("sessions", []) if item.get("id") == session_id), None)
    if not chat:
        raise ValueError(f"未找到会话：{session_id}")
    runs = load_agent_runs(session_id, 50)
    handoffs = load_handoffs(session_id, 50)
    memories = load_project_memories(chat.get("workspacePath") or "", limit=12) if chat.get("workspacePath") else []
    task_spec = load_task_spec(session_id)
    package = context_engine.context_package_from_chat(
        chat,
        artifacts=chat.get("artifacts", []),
        runs=runs,
        handoffs=handoffs,
        memories=memories,
        task_spec=task_spec,
        strategy=strategy or chat.get("contextStrategy") or "standard",
        target_agent_id=target_agent_id,
        target_agent_label=target_agent_label,
    )
    return {
        "package": package,
        "text": context_engine.context_package_to_text(package),
    }


def serialize_memory(row) -> dict:
    return {
        "id": row["id"],
        "workspacePath": row["workspace_path"],
        "kind": row["kind"],
        "title": row["title"],
        "content": row["content"],
        "sourceSessionId": row["source_session_id"] or "",
        "confidence": float(row["confidence"] or 0),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def save_project_memory(payload: dict) -> dict:
    init_db()
    workspace_path = normalize_workspace_path(payload.get("workspacePath") or payload.get("workspace_path") or "")
    if not workspace_path:
        raise ValueError("缺少 workspacePath")
    kind = str(payload.get("kind") or "note").strip()[:40] or "note"
    title = truncate_text(str(payload.get("title") or "项目记忆").strip(), 160)
    content = str(payload.get("content") or "").strip()
    if not content:
        raise ValueError("缺少记忆内容")
    source_session_id = str(payload.get("sourceSessionId") or payload.get("source_session_id") or "")
    confidence = float(payload.get("confidence") or 0.75)
    memory_id = str(payload.get("id") or uuid.uuid4())
    now = int(time.time() * 1000)
    with db_connect() as conn:
        existing = conn.execute(
            "SELECT id,created_at FROM project_memories WHERE workspace_path=? AND kind=? AND title=? AND content=? LIMIT 1",
            (workspace_path, kind, title, content),
        ).fetchone()
        if existing:
            memory_id = existing["id"]
            created_at = existing["created_at"]
        else:
            created_at = now
        conn.execute(
            """
            INSERT INTO project_memories(id,workspace_path,kind,title,content,source_session_id,confidence,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              workspace_path=excluded.workspace_path,
              kind=excluded.kind,
              title=excluded.title,
              content=excluded.content,
              source_session_id=excluded.source_session_id,
              confidence=excluded.confidence,
              updated_at=excluded.updated_at
            """,
            (memory_id, workspace_path, kind, title, content, source_session_id, confidence, created_at, now),
        )
        conn.execute("DELETE FROM project_memory_fts WHERE id=?", (memory_id,))
        conn.execute("INSERT INTO project_memory_fts(id,workspace_path,kind,title,content) VALUES(?,?,?,?,?)", (memory_id, workspace_path, kind, title, content))
        row = conn.execute("SELECT * FROM project_memories WHERE id=?", (memory_id,)).fetchone()
    return serialize_memory(row)


def load_project_memories(workspace_path: str, query: str = "", limit: int = 30) -> list[dict]:
    init_db()
    workspace_path = normalize_workspace_path(workspace_path)
    limit = max(1, min(int(limit or 30), 100))
    if not workspace_path:
        return []
    with db_connect() as conn:
        if query.strip():
            rows = conn.execute(
                """
                SELECT m.* FROM project_memory_fts f
                JOIN project_memories m ON m.id=f.id
                WHERE f.workspace_path=? AND project_memory_fts MATCH ?
                ORDER BY m.updated_at DESC LIMIT ?
                """,
                (workspace_path, query.strip(), limit),
            ).fetchall()
            if not rows:
                like = f"%{query.strip()}%"
                rows = conn.execute(
                    """
                    SELECT * FROM project_memories
                    WHERE workspace_path=? AND (title LIKE ? OR content LIKE ? OR kind LIKE ?)
                    ORDER BY updated_at DESC LIMIT ?
                    """,
                    (workspace_path, like, like, like, limit),
                ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM project_memories WHERE workspace_path=? ORDER BY updated_at DESC LIMIT ?",
                (workspace_path, limit),
            ).fetchall()
    return [serialize_memory(row) for row in rows]


def delete_project_memory(memory_id: str) -> bool:
    init_db()
    with db_connect() as conn:
        cur = conn.execute("DELETE FROM project_memories WHERE id=?", (str(memory_id),))
        conn.execute("DELETE FROM project_memory_fts WHERE id=?", (str(memory_id),))
        return cur.rowcount > 0


def extract_project_memories(session_id: str, workspace_path: str = "") -> list[dict]:
    init_db()
    data = load_sessions_from_db()
    chat = next((item for item in data.get("sessions", []) if item.get("id") == session_id), None)
    if not chat:
        raise ValueError(f"未找到会话：{session_id}")
    workspace_path = normalize_workspace_path(workspace_path or chat.get("workspacePath") or "")
    if not workspace_path:
        raise ValueError("当前任务未绑定项目工作区，不能沉淀项目记忆")
    candidates: list[dict] = []
    title = chat.get("title") or "未命名任务"
    if chat.get("taskGoal") or chat.get("summary"):
        candidates.append({
            "workspacePath": workspace_path,
            "kind": "task",
            "title": f"任务经验：{truncate_text(title, 60)}",
            "content": "\n".join([part for part in [
                f"任务目标：{chat.get('taskGoal') or ''}",
                f"当前状态：{chat.get('taskStatus') or ''}",
                f"下一步：{chat.get('nextStep') or ''}",
                f"摘要：{truncate_text(chat.get('summary') or '', 700)}",
            ] if part.split('：', 1)[-1].strip()]),
            "sourceSessionId": session_id,
            "confidence": 0.72,
        })
    runs = load_agent_runs(session_id, 8)
    for run in runs[:5]:
        output = str(run.get("output_summary") or run.get("error") or "")
        if output:
            candidates.append({
                "workspacePath": workspace_path,
                "kind": "run",
                "title": f"{run.get('agent_id') or run.get('adapter') or 'Agent'} 执行经验",
                "content": f"Agent：{run.get('agent_id') or ''}\nAdapter：{run.get('adapter') or ''}\n状态：{run.get('status') or ''}\n耗时：{run.get('latency_ms') or 0}ms\n结果摘要：{truncate_text(output, 700)}",
                "sourceSessionId": session_id,
                "confidence": 0.68,
            })
    all_text = "\n".join([text_from_content(m.get("content")) for m in chat.get("messages", []) if isinstance(m, dict)])
    for ref in context_engine.extract_file_paths(all_text, source="session"):
        candidates.append({
            "workspacePath": workspace_path,
            "kind": "file",
            "title": f"文件路径：{Path(ref['path']).name}",
            "content": f"路径：{ref['path']}\n类型：{ref.get('role') or 'mentioned'}\n证据：{ref.get('evidence') or ''}",
            "sourceSessionId": session_id,
            "confidence": 0.8,
        })
    commands = []
    for line in all_text.splitlines():
        stripped = line.strip().strip("`")
        if stripped.startswith(("./", "python", "python3", "node ", "npm ", "git ", "make ", "pytest", "ruff ", "eslint ", "uv ")):
            commands.append(stripped)
    for cmd in commands[:8]:
        candidates.append({
            "workspacePath": workspace_path,
            "kind": "command",
            "title": f"常用命令：{truncate_text(cmd, 80)}",
            "content": cmd,
            "sourceSessionId": session_id,
            "confidence": 0.78,
        })
    saved = []
    for item in candidates[:20]:
        if item.get("content"):
            saved.append(save_project_memory(item))
    return saved

def compact_context_messages(messages, session_id: str = "", agent_id: str = "") -> list[dict]:
    summary, handoff = latest_session_context(session_id, agent_id)
    artifact_context = session_artifact_context(session_id)
    context_package_text = ""
    if session_id:
        try:
            context_package_text = load_context_package(session_id, agent_id).get("text", "")
        except Exception:
            context_package_text = ""
    return context_engine.compact_context_messages(
        messages,
        summary=summary,
        handoff=handoff,
        artifact_context=artifact_context,
        context_package=context_package_text,
    )


def record_agent_run(session_id: str, agent_id: str, adapter: str, status: str, input_messages, output_text: str = "", error: str = "", latency_ms: int = 0, debug: dict | None = None):
    if not session_id:
        return
    init_db()
    input_summary = truncate_text(" | ".join([f"{m.get('role')}:{text_from_content(m.get('content'))}" for m in (input_messages or [])[-4:] if isinstance(m, dict)]), 900)
    output_summary = truncate_text(output_text or "", 900)
    debug_payload = debug if isinstance(debug, dict) else {}
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO agent_runs(id,session_id,agent_id,adapter,status,input_summary,output_summary,latency_ms,error,debug_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), session_id, agent_id, adapter, status, input_summary, output_summary, int(latency_ms or 0), truncate_text(error or "", 900), json.dumps(debug_payload, ensure_ascii=False), int(time.time() * 1000)),
        )


def load_agent_runs(session_id: str = "", limit: int = 50) -> list[dict]:
    init_db()
    limit = max(1, min(int(limit or 50), 200))
    with db_connect() as conn:
        if session_id:
            rows = conn.execute(
                "SELECT * FROM agent_runs WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM agent_runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["debug"] = json.loads(item.get("debug_json") or "{}")
            except Exception:
                item["debug"] = {}
            result.append(item)
        return result
