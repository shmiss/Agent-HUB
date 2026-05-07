from __future__ import annotations

from context_engine import text_from_content, truncate_text


STAGES = ["draft", "planning", "executing", "review", "testing", "done", "archived"]

STAGE_LABELS = {
    "draft": "草稿",
    "planning": "规划",
    "executing": "执行",
    "review": "复核",
    "testing": "测试",
    "done": "完成",
    "archived": "归档",
}

SESSION_STATUS_BY_STAGE = {
    "draft": "todo",
    "planning": "active",
    "executing": "active",
    "review": "handoff",
    "testing": "active",
    "done": "done",
    "archived": "done",
}

NEXT_STEP_BY_STAGE = {
    "draft": "补充任务目标、约束、相关文件和期望交付物。",
    "planning": "确认任务规格和 Agent 分工，然后进入执行。",
    "executing": "按当前任务规格推进执行，并记录 Agent Run。",
    "review": "复核执行结果，检查风险、遗漏和是否满足验收标准。",
    "testing": "运行必要检查或人工验证，确认交付物可用。",
    "done": "任务已完成，可沉淀项目记忆或归档。",
    "archived": "任务已归档，后续仅作为历史上下文和项目记忆使用。",
}

ALLOWED_TRANSITIONS = {
    "draft": ["planning", "executing", "archived"],
    "planning": ["draft", "executing", "review", "archived"],
    "executing": ["planning", "review", "testing", "done", "archived"],
    "review": ["executing", "testing", "done", "archived"],
    "testing": ["executing", "review", "done", "archived"],
    "done": ["review", "archived"],
    "archived": ["draft"],
}


def normalize_stage(stage: str) -> str:
    value = str(stage or "").strip().lower()
    return value if value in STAGES else "draft"


def status_for_stage(stage: str) -> str:
    return SESSION_STATUS_BY_STAGE.get(normalize_stage(stage), "todo")


def next_step_for_stage(stage: str) -> str:
    return NEXT_STEP_BY_STAGE.get(normalize_stage(stage), NEXT_STEP_BY_STAGE["draft"])


def allowed_next(stage: str) -> list[str]:
    return ALLOWED_TRANSITIONS.get(normalize_stage(stage), ALLOWED_TRANSITIONS["draft"])


def stage_meta(stage: str) -> dict:
    value = normalize_stage(stage)
    return {
        "id": value,
        "label": STAGE_LABELS.get(value, value),
        "status": status_for_stage(value),
        "nextStep": next_step_for_stage(value),
        "allowedNext": allowed_next(value),
    }


def workflow_definition() -> dict:
    return {
        "stages": [stage_meta(stage) for stage in STAGES],
        "transitions": ALLOWED_TRANSITIONS,
    }


def _latest_user_text(session: dict) -> str:
    messages = session.get("messages") if isinstance(session.get("messages"), list) else []
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            text = text_from_content(msg.get("content")).strip()
            if text:
                return truncate_text(text, 360)
    return ""


def _task_goal(session: dict, spec: dict) -> str:
    return truncate_text(str(spec.get("goal") or session.get("taskGoal") or _latest_user_text(session) or session.get("title") or "未命名任务"), 360)


def _files_text(spec: dict, session: dict) -> list[str]:
    files = []
    for item in spec.get("files") or []:
        if isinstance(item, dict) and item.get("path"):
            files.append(str(item.get("path")))
        elif isinstance(item, str):
            files.append(item)
    for artifact in session.get("artifacts") or []:
        if isinstance(artifact, dict) and artifact.get("name"):
            files.append(str(artifact.get("name")))
    seen = []
    for item in files:
        if item and item not in seen:
            seen.append(item)
    return seen[:8]


def _acceptance(spec: dict, fallback: list[str] | None = None) -> list[str]:
    values = [str(item) for item in (spec.get("acceptance") or []) if str(item).strip()]
    return values[:8] or (fallback or ["输出满足用户当前目标", "关键结论和下一步明确"])


def stage_action(session: dict | None, spec: dict | None, stage: str = "", recommendation: dict | None = None) -> dict:
    """Generate a local-first stage action draft.

    This intentionally avoids upstream model calls. It creates a structured
    artifact that can be inserted into the chat, used as a checklist, or sent to
    the recommended Agent in later versions.
    """
    session = session or {}
    spec = spec or {}
    recommendation = recommendation or {}
    current = normalize_stage(stage or spec.get("stage") or "draft")
    goal = _task_goal(session, spec)
    agent = recommendation.get("primaryAgentLabel") or recommendation.get("primaryAgentId") or "当前推荐 Agent"
    context = recommendation.get("contextStrategy") or session.get("contextStrategy") or "standard"
    files = _files_text(spec, session)
    acceptance = _acceptance(spec)
    constraints = [str(item) for item in (spec.get("constraints") or []) if str(item).strip()][:6]
    deliverable = str(spec.get("finalDeliverable") or "阶段交付结果")

    title_by_stage = {
        "draft": "需求澄清清单",
        "planning": "执行计划草案",
        "executing": "Agent 执行指令包",
        "review": "Review Checklist",
        "testing": "测试 / 验收清单",
        "done": "最终交付摘要",
        "archived": "归档与项目记忆草案",
    }
    title = title_by_stage.get(current, "阶段动作")

    lines = [f"# {title}", "", f"- 阶段：{STAGE_LABELS.get(current, current)}", f"- 任务目标：{goal}", f"- 推荐 Agent：{agent}", f"- 上下文策略：{context}", f"- 预期交付：{deliverable}"]
    if files:
        lines += ["", "## 相关文件", *[f"- {item}" for item in files]]
    if constraints:
        lines += ["", "## 执行约束", *[f"- {item}" for item in constraints]]

    if current == "draft":
        lines += [
            "",
            "## 需要补齐的问题",
            "1. 最终要交付什么文件、代码修改或结论？",
            "2. 是否绑定了正确的项目工作区或目标文件？",
            "3. 有哪些不能改、不能覆盖、不能联网或必须保留的边界？",
            "4. 验收标准是什么，做到什么程度算完成？",
        ]
    elif current == "planning":
        lines += [
            "",
            "## 建议执行步骤",
            "1. 确认 Task Spec：目标、约束、相关文件和验收标准。",
            f"2. 采用推荐 Agent：{agent}。",
            "3. 将 Context Package 注入给执行 Agent。",
            "4. 执行后进入 Review 阶段，检查是否满足验收标准。",
            "5. 必要时进入 Testing 阶段做命令、文件或人工验收。",
        ]
    elif current == "executing":
        lines += [
            "",
            "## 可发送给 Agent 的执行指令",
            f"请基于 Agent Hub 注入的 Context Package 完成任务：{goal}",
            "要求：严格遵守执行约束；优先使用相关文件和工作区；完成后说明改动/产物路径/检查结果。",
        ]
    elif current == "review":
        lines += ["", "## Review Checklist", *[f"- [ ] {item}" for item in acceptance]]
        lines += ["- [ ] 是否有遗漏、幻觉、路径错误或未说明的失败？", "- [ ] 是否需要切回执行阶段修复？"]
    elif current == "testing":
        lines += [
            "",
            "## 验收动作",
            "- [ ] 打开或检查最终产物。",
            "- [ ] 运行可用测试/检查命令，或说明无法运行的原因。",
            "- [ ] 对照验收标准逐项确认。",
            *[f"- [ ] {item}" for item in acceptance],
        ]
    elif current == "done":
        lines += [
            "",
            "## 完成摘要模板",
            f"- 已完成：{goal}",
            "- 关键结果：",
            "- 产物/改动路径：",
            "- 已执行检查：",
            "- 后续建议：",
        ]
    elif current == "archived":
        lines += [
            "",
            "## 建议沉淀为项目记忆",
            f"- 任务经验：{goal}",
            "- 有效 Agent / 模型组合：",
            "- 关键文件路径或命令：",
            "- 避免踩坑：",
        ]

    return {
        "stage": current,
        "stageLabel": STAGE_LABELS.get(current, current),
        "title": title,
        "recommendedAgentId": recommendation.get("primaryAgentId") or "",
        "recommendedAgentLabel": agent,
        "contextStrategy": context,
        "content": "\n".join(lines).strip(),
    }
