from __future__ import annotations

import re
from dataclasses import dataclass

from context_engine import extract_file_paths, text_from_content, truncate_text


TASK_TYPES = {"chat", "code", "file", "review", "research", "workflow"}

CONTEXT_BY_TYPE = {
    "chat": "standard",
    "code": "code",
    "file": "file",
    "review": "full",
    "research": "standard",
    "workflow": "standard",
}

TYPE_LABELS = {
    "chat": "方案/问答",
    "code": "代码任务",
    "file": "文件任务",
    "review": "审查/验收",
    "research": "资料调研",
    "workflow": "复杂流程",
}

WORKFLOW_STAGE_LABELS = {
    "draft": "草稿",
    "planning": "规划",
    "executing": "执行",
    "review": "复核",
    "testing": "测试",
    "done": "完成",
    "archived": "归档",
}

KEYWORDS = {
    "code": [
        "bug", "报错", "错误", "异常", "代码", "函数", "接口", "前端", "后端", "api",
        "测试", "单元测试", "unittest", "pytest", "修复", "重构", "数据库", "sqlite",
        "css", "html", "javascript", "python", "组件", "路由", "提交", "git",
    ],
    "file": [
        "word", "excel", "pdf", "docx", "xlsx", "ppt", "pptx", "排版", "文档",
        "表格", "附件", "文件", "导出", "生成", "整理这个文件",
    ],
    "review": [
        "review", "检查", "复核", "审查", "验收", "qa", "测试一下", "有没有问题",
        "风险", "回归", "确认是否", "评估改动",
    ],
    "research": [
        "调研", "学习", "研究", "搜索", "资料", "竞品", "开源项目", "国内外",
        "趋势", "参考", "借鉴", "看看网上",
    ],
    "workflow": [
        "多步骤", "自动分配", "最终交付", "完整完成", "一条龙", "协作", "多agent",
        "多 agent", "接力", "任务调度", "从头到尾", "整个项目",
    ],
}

CODE_EXTENSIONS = re.compile(r"\.(py|js|ts|tsx|jsx|html|css|json|yaml|yml|sql|md)\b", re.I)
FILE_EXTENSIONS = re.compile(r"\.(docx|doc|xlsx|xls|pdf|pptx|ppt|csv|txt)\b", re.I)


@dataclass
class AgentCapability:
    agent_id: str
    label: str
    adapter: str
    agent_type: str
    capabilities: list[str]
    best_for: list[str]
    avoid_for: list[str]
    reachable: bool

    def to_dict(self) -> dict:
        return {
            "agentId": self.agent_id,
            "label": self.label,
            "adapter": self.adapter,
            "type": self.agent_type,
            "capabilities": self.capabilities,
            "bestFor": self.best_for,
            "avoidFor": self.avoid_for,
            "reachable": self.reachable,
        }


def _contains_any(text: str, keywords: list[str]) -> list[str]:
    lower = text.lower()
    return [kw for kw in keywords if kw.lower() in lower]


def message_text(messages) -> str:
    if not isinstance(messages, list):
        return ""
    parts = []
    for msg in messages[-8:]:
        if isinstance(msg, dict) and msg.get("role") in {"user", "assistant"}:
            parts.append(text_from_content(msg.get("content")))
    return "\n".join(parts)


def build_task_signal(session: dict | None = None, message: str = "") -> dict:
    session = session or {}
    artifacts = [a for a in session.get("artifacts", []) if isinstance(a, dict)]
    text = "\n".join([
        str(session.get("title") or ""),
        str(session.get("taskGoal") or ""),
        str(session.get("summary") or ""),
        message or "",
        message_text(session.get("messages")),
        "\n".join(str(a.get("name") or "") for a in artifacts),
    ]).strip()
    file_refs = extract_file_paths(text)
    return {
        "text": text,
        "hasWorkspace": bool(session.get("workspacePath")),
        "workspacePath": str(session.get("workspacePath") or ""),
        "artifactCount": len(artifacts),
        "fileRefs": file_refs,
        "hasOfficeArtifact": any(FILE_EXTENSIONS.search(str(a.get("name") or "")) for a in artifacts),
        "hasCodeLikePath": bool(CODE_EXTENSIONS.search(text)),
        "hasFileLikePath": bool(FILE_EXTENSIONS.search(text)) or bool(file_refs),
    }


def classify_task(session: dict | None = None, message: str = "") -> dict:
    signal = build_task_signal(session, message)
    text = signal["text"]
    scores = {task_type: 0 for task_type in TASK_TYPES}
    evidence: dict[str, list[str]] = {task_type: [] for task_type in TASK_TYPES}

    for task_type, keywords in KEYWORDS.items():
        hits = _contains_any(text, keywords)
        if hits:
            scores[task_type] += min(len(hits), 5) * 2
            evidence[task_type].extend([f"命中关键词：{hit}" for hit in hits[:4]])

    if signal["hasWorkspace"]:
        scores["code"] += 2
        scores["workflow"] += 1
        evidence["code"].append("当前任务已绑定工作区")
    if signal["artifactCount"]:
        scores["file"] += 1
        evidence["file"].append("当前任务包含附件")
    if signal["hasOfficeArtifact"]:
        scores["file"] += 4
        evidence["file"].append("附件包含 Office/PDF 文件")
    if signal["hasCodeLikePath"]:
        scores["code"] += 4
        evidence["code"].append("检测到代码文件路径或扩展名")
    if signal["hasFileLikePath"]:
        scores["file"] += 3
        evidence["file"].append("检测到本地文件路径或文档扩展名")

    # Review intent should override generic code/file hints when explicit.
    if scores["review"] >= 4:
        task_type = "review"
    # Workflow is a meta-intent: only choose it when explicit and meaningful.
    elif scores["workflow"] >= 4:
        task_type = "workflow"
    else:
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        task_type = ranked[0][0] if ranked and ranked[0][1] > 0 else "chat"

    confidence = min(0.95, 0.45 + (scores[task_type] * 0.06))
    if task_type == "chat" and scores[task_type] == 0:
        confidence = 0.55
        evidence["chat"].append("未检测到强执行意图，按普通问答/方案分析处理")

    return {
        "taskType": task_type,
        "taskTypeLabel": TYPE_LABELS.get(task_type, task_type),
        "confidence": round(confidence, 2),
        "contextStrategy": CONTEXT_BY_TYPE.get(task_type, "standard"),
        "scores": scores,
        "evidence": evidence.get(task_type, []),
        "signal": signal,
    }


def capability_from_profile(profile: dict) -> AgentCapability:
    adapter = str(profile.get("adapter") or "")
    typ = str(profile.get("type") or "")
    label = str(profile.get("label") or profile.get("id") or "Agent")
    reachable = profile.get("reachable") is not False and str(profile.get("status") or "").lower() not in {"offline", "error"}

    capabilities = ["chat"]
    best_for = ["普通问答"]
    avoid_for = []

    if typ == "hermes" or adapter == "openai-chat":
        capabilities = ["analysis", "planning", "summary", "research", "chat", "review"]
        best_for = ["方案分析", "任务拆解", "上下文总结", "资料整理"]
        avoid_for = ["直接修改本地代码", "长时间本地命令执行"]
    if "openclaw" in typ or adapter in {"openclaw-cli", "openclaw-gateway-rpc"}:
        capabilities = ["local_files", "commands", "file", "workflow", "chat"]
        best_for = ["本地文件处理", "系统操作", "长任务执行", "本地 Agent 工具调用"]
        avoid_for = ["纯方案长文分析"]
    if typ == "claude" or adapter == "claude-code-cli":
        capabilities = ["code", "review", "qa", "workspace", "files"]
        best_for = ["代码修改", "项目理解", "代码审查", "测试验证"]
        avoid_for = ["无工作区的泛泛问答"]
    if typ == "codex" or adapter == "codex-cli":
        capabilities = ["code", "review", "qa", "workspace"]
        best_for = ["代码修改", "测试修复", "PR/patch 交付"]
        avoid_for = ["Office 文档处理"]

    return AgentCapability(
        agent_id=str(profile.get("id") or profile.get("agentId") or ""),
        label=label,
        adapter=adapter,
        agent_type=typ,
        capabilities=capabilities,
        best_for=best_for,
        avoid_for=avoid_for,
        reachable=reachable,
    )


def build_agent_capabilities(profiles: list[dict]) -> list[dict]:
    return [capability_from_profile(profile).to_dict() for profile in profiles if isinstance(profile, dict)]


def _agent_score(capability: dict, task_type: str, signal: dict, workflow_stage: str = "") -> tuple[int, list[str], list[str]]:
    caps = set(capability.get("capabilities") or [])
    label = capability.get("label") or capability.get("agentId") or "Agent"
    score = 0
    reasons = []
    warnings = []

    if not capability.get("reachable", True):
        score -= 20
        warnings.append(f"{label} 当前状态可能不可用")

    if task_type in {"chat", "research"}:
        if {"analysis", "planning", "research"} & caps:
            score += 8
            reasons.append(f"{label} 适合方案分析、任务拆解和总结")
    if task_type == "code":
        if "code" in caps:
            score += 9
            reasons.append(f"{label} 适合读取项目并处理代码任务")
        if signal.get("hasWorkspace") and "workspace" in caps:
            score += 4
            reasons.append("当前任务已绑定工作区，可传递项目路径")
        if not signal.get("hasWorkspace") and "code" in caps:
            warnings.append("当前未绑定工作区，代码任务可能无法定位项目文件")
    if task_type == "file":
        if "local_files" in caps or "file" in caps:
            score += 9
            reasons.append(f"{label} 适合本地文件和系统级任务")
        if "files" in caps:
            score += 3
            reasons.append(f"{label} 可读取工作区文件")
    if task_type == "review":
        if "review" in caps or "qa" in caps:
            score += 8
            reasons.append(f"{label} 适合做审查、复核和测试验证")
        if "analysis" in caps:
            score += 3
            reasons.append(f"{label} 可补充风险分析")
    if task_type == "workflow":
        if "planning" in caps:
            score += 7
            reasons.append(f"{label} 适合先做任务规划和 Agent 分工")
        if "workflow" in caps or "commands" in caps:
            score += 5
            reasons.append(f"{label} 可承担本地执行环节")

    stage = str(workflow_stage or "").strip().lower()
    if stage in {"draft", "planning"} and "planning" in caps:
        score += 6
        reasons.append(f"当前处于{WORKFLOW_STAGE_LABELS.get(stage, stage)}阶段，优先使用规划/分析型 Agent")
    if stage == "executing" and ({"code", "local_files", "file", "commands", "workflow"} & caps):
        score += 5
        reasons.append("当前处于执行阶段，优先使用可落地执行的 Agent")
    if stage in {"review", "testing"} and ({"review", "qa"} & caps):
        score += 7
        reasons.append(f"当前处于{WORKFLOW_STAGE_LABELS.get(stage, stage)}阶段，优先使用复核/测试能力")
    if stage == "done" and ({"summary", "analysis"} & caps):
        score += 5
        reasons.append("当前处于完成阶段，适合总结交付和沉淀经验")
    if stage == "archived" and ({"summary", "analysis"} & caps):
        score += 4
        reasons.append("当前处于归档阶段，适合整理归档摘要和项目记忆")

    if not reasons:
        score += 1
        reasons.append(f"{label} 可作为通用 Agent 处理")
    return score, reasons, warnings


def _effective_task_type(classification: dict, workflow_stage: str = "", task_spec: dict | None = None) -> str:
    stage = str(workflow_stage or "").strip().lower()
    spec_type = str((task_spec or {}).get("taskType") or (task_spec or {}).get("task_type") or "").strip()
    base_type = spec_type if spec_type in TASK_TYPES else classification["taskType"]
    if stage in {"review", "testing"}:
        return "review"
    if stage in {"done", "archived"}:
        return "chat"
    if stage == "planning":
        return "workflow" if base_type == "workflow" else "chat"
    if stage == "executing":
        return base_type
    return base_type


def _context_for_stage(task_type: str, workflow_stage: str = "") -> str:
    stage = str(workflow_stage or "").strip().lower()
    if stage in {"review", "testing"}:
        return "full"
    if stage == "executing":
        return CONTEXT_BY_TYPE.get(task_type, "standard")
    if stage in {"done", "archived"}:
        return "standard"
    if stage == "planning":
        return "standard" if task_type != "code" else "code"
    return CONTEXT_BY_TYPE.get(task_type, "standard")


def recommend_agent(session: dict | None, profiles: list[dict], message: str = "", active_agent_id: str = "", workflow_stage: str = "", task_spec: dict | None = None) -> dict:
    classification = classify_task(session, message)
    task_type = _effective_task_type(classification, workflow_stage, task_spec)
    signal = classification["signal"]
    capabilities = build_agent_capabilities(profiles)
    ranked = []
    for cap in capabilities:
        score, reasons, warnings = _agent_score(cap, task_type, signal, workflow_stage)
        if active_agent_id and cap.get("agentId") == active_agent_id:
            score += 1
        ranked.append({**cap, "score": score, "reasons": reasons, "warnings": warnings})
    ranked.sort(key=lambda item: item.get("score", 0), reverse=True)

    primary = ranked[0] if ranked else {}
    assistants = []
    for item in ranked[1:]:
        if len(assistants) >= 2:
            break
        if item.get("score", 0) >= 5:
            assistants.append(item.get("agentId"))

    reasons = list(classification.get("evidence") or [])
    if workflow_stage:
        reasons.insert(0, f"当前 Workflow 阶段：{WORKFLOW_STAGE_LABELS.get(str(workflow_stage), str(workflow_stage))}，按阶段目标调整 Agent 推荐")
    reasons.extend(primary.get("reasons") or [])
    warnings = list(primary.get("warnings") or [])
    for item in ranked:
        for warning in item.get("warnings") or []:
            if warning not in warnings:
                warnings.append(warning)

    if task_type == "code" and not signal.get("hasWorkspace"):
        warnings.append("建议先绑定项目工作区，再交给代码 Agent 执行")
    if task_type == "workflow":
        reasons.append("复杂任务建议先规划，再按子任务交给执行 Agent")
    if not primary:
        warnings.append("未找到可用 Agent Profile")

    confidence = classification["confidence"]
    if primary and primary.get("score", 0) >= 9:
        confidence = min(0.96, confidence + 0.08)
    elif primary and primary.get("score", 0) <= 2:
        confidence = max(0.35, confidence - 0.12)

    return {
        "taskType": task_type,
        "taskTypeLabel": TYPE_LABELS.get(task_type, task_type),
        "primaryAgentId": primary.get("agentId", ""),
        "primaryAgentLabel": primary.get("label", ""),
        "assistantAgentIds": assistants,
        "contextStrategy": _context_for_stage(task_type, workflow_stage),
        "confidence": round(confidence, 2),
        "reasons": [truncate_text(item, 180) for item in reasons[:8]],
        "warnings": [truncate_text(item, 180) for item in warnings[:6]],
        "scores": classification.get("scores", {}),
        "capabilities": capabilities,
        "workflowStage": str(workflow_stage or ""),
    }


def _latest_user_goal(session: dict, message: str = "") -> str:
    if message and message.strip():
        return truncate_text(message.strip(), 220)
    if session.get("taskGoal"):
        return truncate_text(str(session.get("taskGoal")).strip(), 220)
    messages = session.get("messages") if isinstance(session.get("messages"), list) else []
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            text = text_from_content(msg.get("content")).strip()
            if text:
                return truncate_text(text, 220)
    return truncate_text(str(session.get("title") or "未命名任务").strip(), 220)


def _dedupe_keep_order(items: list[str], limit: int = 8) -> list[str]:
    seen = set()
    out = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(truncate_text(text, 180))
        if len(out) >= limit:
            break
    return out


def task_spec_from_session(session: dict | None, message: str = "", recommendation: dict | None = None) -> dict:
    """Generate a lightweight Task Spec v1 from local signals.

    This is deliberately heuristic and local-first: it should be fast enough to
    run before every dispatch recommendation, giving Agent Hub a stable task
    contract even when no upstream model is available.
    """
    session = session or {}
    recommendation = recommendation or recommend_agent(session, [], message=message)
    classification = classify_task(session, message)
    task_type = str(recommendation.get("taskType") or classification.get("taskType") or "chat")
    signal = classification.get("signal") or build_task_signal(session, message)
    goal = _latest_user_goal(session, message)
    artifacts = [a for a in session.get("artifacts", []) if isinstance(a, dict)]
    file_refs = []
    for ref in signal.get("fileRefs") or []:
        path = str(ref.get("path") or "").strip()
        if path:
            file_refs.append({"path": path, "role": ref.get("role") or "mentioned", "source": ref.get("source") or ""})
    for artifact in artifacts:
        name = str(artifact.get("name") or "").strip()
        if name:
            file_refs.append({"path": name, "role": "artifact", "source": "attachment"})

    constraints = []
    if session.get("workspacePath"):
        constraints.append(f"默认在项目工作区执行：{session.get('workspacePath')}")
    if artifacts:
        constraints.append("优先使用当前任务已上传或固定的附件资产，不要重复询问文件位置")
    if task_type in {"code", "review"}:
        constraints.append("涉及代码改动时先理解现有结构，避免无关重构；完成后尽量运行可用测试")
    if task_type == "file":
        constraints.append("涉及本地文件处理时保留原文件，优先生成新版本或可回退产物")
    if task_type == "workflow":
        constraints.append("复杂任务先拆分步骤，再按 Agent 能力分派执行与验收")

    acceptance_by_type = {
        "chat": ["回答直接解决当前问题", "结论、依据和下一步清楚"],
        "research": ["形成可执行的调研结论", "明确可借鉴点、风险和后续动作"],
        "code": ["问题原因说明清楚", "代码修改范围可追踪", "关键检查或测试通过/说明未执行原因"],
        "file": ["目标文件处理完成并给出明确路径", "排版/转换/整理结果可直接打开检查"],
        "review": ["列出发现的问题、风险等级和修复建议", "给出是否通过验收的明确结论"],
        "workflow": ["任务拆分、Agent 分工和交付物明确", "每个执行步骤有状态记录和最终汇总"],
    }
    acceptance = acceptance_by_type.get(task_type, acceptance_by_type["chat"])
    final_by_type = {
        "chat": "结构化答复",
        "research": "调研结论与行动建议",
        "code": "可验证的代码修改与检查结果",
        "file": "处理后的本地文件/文档交付物",
        "review": "审查报告与验收结论",
        "workflow": "多 Agent 执行计划、过程记录与最终交付摘要",
    }
    warnings = recommendation.get("warnings") if isinstance(recommendation.get("warnings"), list) else []
    if not warnings and task_type == "code" and not signal.get("hasWorkspace"):
        warnings = ["当前未绑定工作区，代码 Agent 可能无法定位项目文件"]

    return {
        "sessionId": str(session.get("id") or ""),
        "goal": goal,
        "taskType": task_type,
        "stage": "planning" if task_type in {"code", "file", "workflow", "review"} else "draft",
        "constraints": _dedupe_keep_order(constraints, 8),
        "acceptance": _dedupe_keep_order(acceptance, 8),
        "files": file_refs[:12],
        "riskNotes": "；".join(_dedupe_keep_order(warnings, 5)),
        "finalDeliverable": final_by_type.get(task_type, "结构化交付结果"),
    }
