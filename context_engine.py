from __future__ import annotations

import json
import re


def text_from_content(content) -> str:
    if isinstance(content, list):
        parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif item.get("type") == "image_url":
                parts.append("[图片附件]")
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(parts)
    return str(content or "")


def truncate_text(text: str, limit: int = 900) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


FILE_PATH_RE = re.compile(r"(/Users/[^\n\r`'\"<>，。；;|]+?\.(?:docx|doc|xlsx|xls|pptx|ppt|pdf|md|txt|csv|json|py|js|ts|html|css))")


def extract_file_paths(text: str, source: str = "") -> list[dict]:
    refs = []
    seen = set()
    for match in FILE_PATH_RE.finditer(str(text or "")):
        path = match.group(1).strip().rstrip(")）]】,，.。")
        if path in seen:
            continue
        seen.add(path)
        start = max(0, match.start() - 40)
        end = min(len(text), match.end() + 40)
        near = text[start:end]
        role = "mentioned"
        if any(key in near for key in ("新文件位置", "已生成", "另存", "保存到", "输出", "生成文件")):
            role = "output"
        elif any(key in near for key in ("源文件", "file:", "原文件", "输入文件", "这个文件")):
            role = "source"
        refs.append({"path": path, "role": role, "source": source, "evidence": truncate_text(near, 180)})
    return refs


def merge_file_refs(*groups) -> list[dict]:
    merged = []
    seen = set()
    priority = {"output": 0, "source": 1, "mentioned": 2}
    for group in groups:
        for item in group or []:
            path = item.get("path")
            if not path or path in seen:
                continue
            seen.add(path)
            merged.append(item)
    return sorted(merged, key=lambda item: (priority.get(item.get("role"), 9), item.get("path", "")))[:12]


STRATEGY_PROFILES = {
    "light": {"recent_messages": 3, "artifacts": 3, "runs": 2, "handoffs": 1, "memories": 3, "include_runs": False, "include_handoffs": True, "include_memories": False, "max_chars": 2600},
    "standard": {"recent_messages": 6, "artifacts": 6, "runs": 5, "handoffs": 4, "memories": 6, "include_runs": True, "include_handoffs": True, "include_memories": True, "max_chars": 5200},
    "full": {"recent_messages": 10, "artifacts": 10, "runs": 10, "handoffs": 8, "memories": 12, "include_runs": True, "include_handoffs": True, "include_memories": True, "max_chars": 8500},
    "file": {"recent_messages": 6, "artifacts": 10, "runs": 6, "handoffs": 4, "memories": 8, "include_runs": True, "include_handoffs": True, "include_memories": True, "max_chars": 6200},
    "code": {"recent_messages": 6, "artifacts": 6, "runs": 8, "handoffs": 5, "memories": 12, "include_runs": True, "include_handoffs": True, "include_memories": True, "max_chars": 6800},
}


def strategy_profile(strategy: str) -> dict:
    return STRATEGY_PROFILES.get(str(strategy or "standard"), STRATEGY_PROFILES["standard"])


def generate_session_summary(chat: dict) -> str:
    title = str(chat.get("title") or "未命名任务")
    messages = [m for m in chat.get("messages", []) if isinstance(m, dict) and m.get("role") in {"user", "assistant"}]
    first_user = next((text_from_content(m.get("content")) for m in messages if m.get("role") == "user"), "")
    recent = messages[-8:]
    recent_lines = []
    for msg in recent:
        who = "用户" if msg.get("role") == "user" else "Agent"
        recent_lines.append(f"- {who}: {truncate_text(text_from_content(msg.get('content')), 180)}")
    return "\n".join([
        f"任务标题：{truncate_text(title, 120)}",
        f"原始目标：{truncate_text(first_user or title, 300)}",
        f"任务状态：{truncate_text(chat.get('taskStatus') or 'active', 40)}",
        "最近进展：",
        *(recent_lines or ["- 暂无可总结的对话进展。"]),
    ]).strip()


def derive_task_goal(chat: dict) -> str:
    explicit = str(chat.get("taskGoal") or "").strip()
    if explicit:
        return explicit
    for msg in chat.get("messages", []):
        if isinstance(msg, dict) and msg.get("role") == "user":
            text = text_from_content(msg.get("content"))
            if text.strip():
                return truncate_text(text.strip(), 300)
    return str(chat.get("title") or "未命名任务")


def format_artifact_context(rows) -> str:
    if not rows:
        return ""
    lines = []
    for index, row in enumerate(reversed(rows), start=1):
        preview = ""
        content_text = row["content_text"] if isinstance(row, dict) else row["content_text"]
        mime_type = row["mime_type"] if isinstance(row, dict) else row["mime_type"]
        kind = row["kind"] if isinstance(row, dict) else row["kind"]
        name = row["name"] if isinstance(row, dict) else row["name"]
        if content_text:
            preview = truncate_text(content_text, 220)
        lines.append(
            f"{index}. {name} [{kind}{', ' + mime_type if mime_type else ''}]"
            + (f"\n   摘要: {preview}" if preview else "")
        )
    return "[Agent Hub 会话附件资产]\n" + "\n".join(lines)


def compact_message_content(content, limit: int = 3000):
    if isinstance(content, list):
        compacted = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text" and len(str(part.get("text") or "")) > limit:
                compacted.append({**part, "text": str(part.get("text") or "")[:limit] + "\n\n[内容过长，Agent Hub 已截断]"})
            else:
                compacted.append(part)
        return compacted
    text = str(content or "")
    return text if len(text) <= limit else text[:limit] + "\n\n[内容过长，Agent Hub 已截断]"


def compact_context_messages(
    messages,
    summary: str = "",
    handoff: str = "",
    artifact_context: str = "",
    context_package: str = "",
    max_messages: int = 8,
    max_chars: int = 6500,
) -> list[dict]:
    allowed = [m for m in (messages or []) if isinstance(m, dict) and m.get("role") in {"system", "user", "assistant"}]
    system = next((m for m in allowed if m.get("role") == "system"), {"role": "system", "content": "你是 Agent Hub 中的企业智能体，请用中文结构化回答。"})
    conversational = [m for m in allowed if m.get("role") != "system"]
    system_text = text_from_content(system.get("content"))
    context_blocks = []
    if summary and "Agent Hub 会话摘要" not in system_text:
        context_blocks.append("[Agent Hub 会话摘要]\n" + truncate_text(summary, 1400))
    if handoff and "Agent Hub Agent 交接摘要" not in system_text:
        context_blocks.append("[Agent Hub Agent 交接摘要]\n" + truncate_text(handoff, 1800))
    if context_package and "Agent Hub Context Package v1" not in system_text:
        context_blocks.append(context_package)
    if artifact_context and "Agent Hub 会话附件资产" not in system_text and "Agent Hub Context Package v1" not in system_text:
        context_blocks.append(artifact_context)
    if context_blocks:
        system_text = system_text + "\n\n" + "\n\n".join(context_blocks)

    selected = []
    total = len(system_text)
    for msg in reversed(conversational):
        text_len = len(text_from_content(msg.get("content")))
        if len(selected) >= max_messages:
            break
        if selected and total + text_len > max_chars:
            break
        selected.insert(0, {"role": msg["role"], "content": compact_message_content(msg.get("content"))})
        total += text_len
    return [{"role": "system", "content": compact_message_content(system_text, 4200)}] + selected



def context_package_from_chat(
    chat: dict,
    artifacts=None,
    runs=None,
    handoffs=None,
    memories=None,
    task_spec=None,
    strategy: str = "standard",
    target_agent_id: str = "",
    target_agent_label: str = "",
    max_recent_messages: int = 6,
) -> dict:
    """Build a task-level context package for cross-agent handoff/injection.

    The package is intentionally structured and compact: it is the single object
    Agent Hub can pass to any local Agent adapter before a turn, instead of
    letting every adapter guess from raw chat history.
    """
    strategy = str(strategy or chat.get("contextStrategy") or "standard")
    profile = strategy_profile(strategy)
    artifacts = [a for a in (artifacts if artifacts is not None else chat.get("artifacts", [])) if isinstance(a, dict)]
    runs = [r for r in (runs or []) if isinstance(r, dict)]
    handoffs = [h for h in (handoffs or []) if isinstance(h, dict)]
    memories = [m for m in (memories or []) if isinstance(m, dict)]
    task_spec = task_spec if isinstance(task_spec, dict) else {}
    messages = [m for m in chat.get("messages", []) if isinstance(m, dict) and m.get("role") in {"user", "assistant"}]
    pinned = [a for a in artifacts if a.get("includeInContext", True) is not False]
    recent_messages = []
    message_file_refs = []
    max_recent_messages = int(profile.get("recent_messages") or max_recent_messages)
    for msg in messages[-max_recent_messages:]:
        msg_text = text_from_content(msg.get("content"))
        recent_messages.append({
            "role": msg.get("role", ""),
            "agentId": msg.get("agentId", ""),
            "text": truncate_text(msg_text, 360),
            "createdAt": msg.get("createdAt", 0),
        })
        message_file_refs.extend(extract_file_paths(msg_text, source=f"message:{msg.get('role', '')}"))
    run_file_refs = []
    for r in runs[: int(profile.get("runs") or 0)]:
        run_file_refs.extend(extract_file_paths(str(r.get("output_summary") or r.get("error") or r.get("input_summary") or ""), source=f"run:{r.get('agent_id') or r.get('adapter') or ''}"))
    artifact_file_refs = [{"path": str(a.get("name") or ""), "role": "artifact", "source": "artifact", "evidence": str(a.get("name") or "")} for a in pinned if str(a.get("name") or "").startswith("/Users/")]
    file_refs = merge_file_refs(run_file_refs, message_file_refs, artifact_file_refs)
    package = {
        "schema": "agent-hub.context-package.v1",
        "sessionId": str(chat.get("id") or ""),
        "title": str(chat.get("title") or "未命名任务"),
        "targetAgentId": target_agent_id or str(chat.get("activeAgentId") or ""),
        "targetAgentLabel": target_agent_label,
        "strategy": strategy,
        "task": {
            "goal": derive_task_goal(chat),
            "status": str(chat.get("taskStatus") or "active"),
            "nextStep": str(chat.get("nextStep") or ""),
            "summary": str(chat.get("summary") or generate_session_summary(chat)),
        },
        "taskSpec": {
            "goal": str(task_spec.get("goal") or ""),
            "taskType": str(task_spec.get("taskType") or task_spec.get("task_type") or ""),
            "stage": str(task_spec.get("stage") or ""),
            "constraints": [str(item) for item in (task_spec.get("constraints") or []) if str(item).strip()][:8],
            "acceptance": [str(item) for item in (task_spec.get("acceptance") or []) if str(item).strip()][:8],
            "files": [item for item in (task_spec.get("files") or []) if isinstance(item, dict)][:12],
            "riskNotes": str(task_spec.get("riskNotes") or task_spec.get("risk_notes") or ""),
            "finalDeliverable": str(task_spec.get("finalDeliverable") or task_spec.get("final_deliverable") or ""),
        },
        "workspace": {
            "path": str(chat.get("workspacePath") or ""),
            "name": str(chat.get("workspaceName") or ""),
            "isGitRepo": bool(chat.get("workspaceGitRepo") or False),
            "branch": str(chat.get("workspaceGitBranch") or ""),
            "dirty": bool(chat.get("workspaceGitDirty") or False),
            "checkedAt": int(chat.get("workspaceCheckedAt") or 0),
        },
        "approval": {
            "policy": str(chat.get("approvalPolicy") or "auto"),
            "policyLabel": {"readonly": "只读", "workspace-auto": "工作区自动", "bypass": "跳过确认", "auto": "自动判断"}.get(str(chat.get("approvalPolicy") or "auto"), "自动判断"),
        },
        "context": {
            "latestHandoff": str(chat.get("handoffSummary") or (handoffs[0].get("handoff_summary") if handoffs else "")),
            "fileRefs": file_refs,
            "projectMemories": ([] if not profile.get("include_memories") else [
                {
                    "kind": str(m.get("kind") or "note"),
                    "title": str(m.get("title") or ""),
                    "content": truncate_text(str(m.get("content") or ""), 420),
                    "confidence": float(m.get("confidence") or 0),
                }
                for m in memories[: int(profile.get("memories") or 0)]
            ]),
            "recentMessages": recent_messages,
            "pinnedArtifacts": [
                {
                    "id": str(a.get("id") or ""),
                    "name": str(a.get("name") or "attachment"),
                    "kind": str(a.get("kind") or ""),
                    "mime": str(a.get("mime") or a.get("mime_type") or ""),
                    "preview": truncate_text(str(a.get("text") or a.get("content_text") or ""), 260) if (a.get("text") or a.get("content_text")) else "",
                }
                for a in pinned[-int(profile.get("artifacts") or 0):]
            ],
            "recentRuns": ([] if not profile.get("include_runs") else [
                {
                    "agentId": str(r.get("agent_id") or r.get("agentId") or ""),
                    "adapter": str(r.get("adapter") or ""),
                    "status": str(r.get("status") or ""),
                    "latencyMs": int(r.get("latency_ms") or r.get("latencyMs") or 0),
                    "summary": truncate_text(str(r.get("output_summary") or r.get("error") or r.get("input_summary") or ""), 260),
                    "createdAt": int(r.get("created_at") or r.get("createdAt") or 0),
                }
                for r in runs[: int(profile.get("runs") or 0)]
            ]),
            "handoffTimeline": ([] if not profile.get("include_handoffs") else [
                {
                    "fromAgentId": str(h.get("from_agent_id") or h.get("fromAgentId") or ""),
                    "toAgentId": str(h.get("to_agent_id") or h.get("toAgentId") or ""),
                    "summary": truncate_text(str(h.get("handoff_summary") or h.get("summary") or ""), 420),
                    "createdAt": int(h.get("created_at") or h.get("createdAt") or 0),
                }
                for h in handoffs[: int(profile.get("handoffs") or 0)]
            ]),
        },
        "limits": {
            "recentMessages": max_recent_messages,
            "pinnedArtifacts": int(profile.get("artifacts") or 0),
            "recentRuns": int(profile.get("runs") or 0),
            "handoffs": int(profile.get("handoffs") or 0),
            "memories": int(profile.get("memories") or 0),
        },
    }
    package["stats"] = {
        "messages": len(messages),
        "recentMessages": len(recent_messages),
        "artifacts": len(artifacts),
        "pinnedArtifacts": len(pinned),
        "runs": len(runs),
        "handoffs": len(handoffs),
        "memories": len(memories),
        "chars": len(context_package_to_text(package, int(profile.get("max_chars") or 5200))),
    }
    return package


def context_package_to_text(package: dict, max_chars: int | None = None) -> str:
    if max_chars is None:
        max_chars = int(strategy_profile(package.get("strategy") if isinstance(package, dict) else "standard").get("max_chars") or 5200)
    task = package.get("task", {}) if isinstance(package, dict) else {}
    task_spec = package.get("taskSpec", {}) if isinstance(package, dict) else {}
    workspace = package.get("workspace", {}) if isinstance(package, dict) else {}
    approval = package.get("approval", {}) if isinstance(package, dict) else {}
    ctx = package.get("context", {}) if isinstance(package, dict) else {}
    lines = [
        "[Agent Hub Context Package v1]",
        f"任务：{package.get('title') or '未命名任务'}",
        f"Session：{package.get('sessionId') or ''}",
        f"目标 Agent：{package.get('targetAgentLabel') or package.get('targetAgentId') or '当前 Agent'}",
        "",
        "## 任务状态",
        f"- 目标：{task.get('goal') or ''}",
        f"- 状态：{task.get('status') or ''}",
        f"- 下一步：{task.get('nextStep') or ''}",
        f"- 摘要：{truncate_text(task.get('summary') or '', 900)}",
    ]
    if any(task_spec.get(key) for key in ("goal", "taskType", "stage", "constraints", "acceptance", "files", "riskNotes", "finalDeliverable")):
        lines += ["", "## 任务规格 Task Spec"]
        if task_spec.get("goal"):
            lines.append(f"- 目标：{task_spec.get('goal')}")
        if task_spec.get("taskType") or task_spec.get("stage"):
            lines.append(f"- 类型/阶段：{task_spec.get('taskType') or '-'} / {task_spec.get('stage') or '-'}")
        if task_spec.get("finalDeliverable"):
            lines.append(f"- 最终交付：{task_spec.get('finalDeliverable')}")
        constraints = task_spec.get("constraints") or []
        if constraints:
            lines.append("- 约束：" + "；".join(str(item) for item in constraints[:5]))
        acceptance = task_spec.get("acceptance") or []
        if acceptance:
            lines.append("- 验收：" + "；".join(str(item) for item in acceptance[:5]))
        files = task_spec.get("files") or []
        if files:
            lines.append("- 相关文件：" + "；".join(str(item.get("path") or "") for item in files[:6] if isinstance(item, dict)))
        if task_spec.get("riskNotes"):
            lines.append(f"- 风险：{truncate_text(task_spec.get('riskNotes') or '', 520)}")
    if workspace.get("path"):
        lines += [
            "",
            "## 项目工作区",
            f"- 路径：{workspace.get('path')}",
            f"- 名称：{workspace.get('name') or ''}",
            f"- Git：{'是' if workspace.get('isGitRepo') else '否'}"
            + (f" · branch={workspace.get('branch') or '-'} · {'dirty' if workspace.get('dirty') else 'clean'}" if workspace.get("isGitRepo") else ""),
            "- 执行要求：如需进行本地文件分析、代码修改或命令执行，默认在该项目目录内完成；访问项目外路径前应先说明原因并请求确认。",
        ]
    if approval.get("policy"):
        lines += [
            "",
            "## 执行权限策略",
            f"- 当前策略：{approval.get('policyLabel') or approval.get('policy')}",
            "- 默认规则：读取上下文和工作区文件可继续；写入、覆盖、删除、安装依赖、访问项目外路径等高风险操作应明确说明并遵守当前策略。",
        ]
    file_refs = ctx.get("fileRefs") or []
    if file_refs:
        lines += ["", "## 关键文件路径"]
        for idx, ref in enumerate(file_refs, 1):
            role = {"output": "输出文件", "source": "源文件", "artifact": "附件", "mentioned": "相关文件"}.get(ref.get("role"), "相关文件")
            lines.append(f"{idx}. [{role}] {ref.get('path')}")
        lines.append("- 使用要求：如果用户说“这个文件/上一个文件/重新排版/另存一个版本”，优先以上述文件路径为准，不要再询问文件在哪里。")
    memories = ctx.get("projectMemories") or []
    if memories:
        lines += ["", "## 项目记忆"]
        for idx, mem in enumerate(memories, 1):
            lines.append(f"{idx}. [{mem.get('kind') or 'note'}] {mem.get('title') or ''}：{truncate_text(mem.get('content') or '', 520)}")
    if ctx.get("latestHandoff"):
        lines += ["", "## 最新 Agent 交接", truncate_text(ctx.get("latestHandoff") or "", 900)]
    artifacts = ctx.get("pinnedArtifacts") or []
    if artifacts:
        lines += ["", "## 固定附件资产"]
        for idx, a in enumerate(artifacts, 1):
            lines.append(f"{idx}. {a.get('name')} [{a.get('kind') or '-'} {a.get('mime') or ''}] {a.get('preview') or ''}".strip())
    messages = ctx.get("recentMessages") or []
    if messages:
        lines += ["", "## 最近对话"]
        for m in messages:
            role = "用户" if m.get("role") == "user" else "Agent"
            lines.append(f"- {role}: {m.get('text') or ''}")
    runs = ctx.get("recentRuns") or []
    if runs:
        lines += ["", "## 最近执行记录"]
        for r in runs:
            lines.append(f"- {r.get('agentId') or r.get('adapter')}: {r.get('status')} · {r.get('latencyMs')}ms · {r.get('summary')}")
    handoffs = ctx.get("handoffTimeline") or []
    if handoffs:
        lines += ["", "## 交接时间线"]
        for h in handoffs:
            lines.append(f"- {h.get('fromAgentId')} → {h.get('toAgentId')}: {h.get('summary')}")
    text = "\n".join(lines).strip()
    return text if len(text) <= max_chars else text[:max_chars] + "\n\n[Context Package 已截断]"
