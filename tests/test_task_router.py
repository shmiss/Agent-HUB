import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import task_router
import task_workflow


PROFILES = [
    {
        "id": "detected-hermes-local",
        "label": "Hermes 本机",
        "type": "hermes",
        "adapter": "openai-chat",
        "reachable": True,
    },
    {
        "id": "detected-openclaw-main",
        "label": "OpenClaw 本机",
        "type": "openclaw",
        "adapter": "openclaw-gateway-rpc",
        "reachable": True,
    },
    {
        "id": "detected-claude-code",
        "label": "Claude Code 本机",
        "type": "claude",
        "adapter": "claude-code-cli",
        "reachable": True,
    },
]


class TaskRouterTest(unittest.TestCase):
    def test_classifies_code_task_and_recommends_claude(self):
        chat = {"workspacePath": str(ROOT), "messages": [{"role": "user", "content": "修复 server.py 里的 HTTP 500 bug，并补测试"}]}
        rec = task_router.recommend_agent(chat, PROFILES)

        self.assertEqual(rec["taskType"], "code")
        self.assertEqual(rec["primaryAgentId"], "detected-claude-code")
        self.assertEqual(rec["contextStrategy"], "code")
        self.assertTrue(rec["reasons"])

    def test_classifies_file_task_and_recommends_openclaw(self):
        rec = task_router.recommend_agent(
            {"messages": [{"role": "user", "content": "把 report.docx 重新排版后导出"}]},
            PROFILES,
        )

        self.assertEqual(rec["taskType"], "file")
        self.assertEqual(rec["primaryAgentId"], "detected-openclaw-main")
        self.assertEqual(rec["contextStrategy"], "file")

    def test_classifies_review_task(self):
        rec = task_router.recommend_agent(
            {"workspacePath": str(ROOT), "messages": [{"role": "user", "content": "检查刚才的改动有没有问题，做一次 review"}]},
            PROFILES,
        )

        self.assertEqual(rec["taskType"], "review")
        self.assertIn(rec["primaryAgentId"], {"detected-claude-code", "detected-hermes-local"})
        self.assertEqual(rec["contextStrategy"], "full")

    def test_classifies_workflow_task_with_planning_agent(self):
        rec = task_router.recommend_agent(
            {"messages": [{"role": "user", "content": "设立任务后自动分配 agent 协作完成，最终交付结果"}]},
            PROFILES,
        )

        self.assertEqual(rec["taskType"], "workflow")
        self.assertEqual(rec["primaryAgentId"], "detected-hermes-local")
        self.assertIn("复杂任务", " ".join(rec["reasons"]))

    def test_falls_back_with_warning_when_claude_unreachable(self):
        profiles = [dict(item) for item in PROFILES]
        profiles[2]["reachable"] = False
        rec = task_router.recommend_agent(
            {"workspacePath": str(ROOT), "messages": [{"role": "user", "content": "修复这个前端 bug"}]},
            profiles,
        )

        self.assertEqual(rec["taskType"], "code")
        self.assertNotEqual(rec["primaryAgentId"], "detected-claude-code")
        self.assertTrue(rec["warnings"])

    def test_generates_task_spec_from_recommendation(self):
        chat = {
            "id": "s-spec",
            "workspacePath": str(ROOT),
            "messages": [{"role": "user", "content": "修复 app.js 里不能停止任务的问题，并补测试"}],
        }
        rec = task_router.recommend_agent(chat, PROFILES)
        spec = task_router.task_spec_from_session(chat, recommendation=rec)

        self.assertEqual(spec["sessionId"], "s-spec")
        self.assertEqual(spec["taskType"], "code")
        self.assertIn("修复 app.js", spec["goal"])
        self.assertTrue(spec["acceptance"])
        self.assertTrue(any("测试" in item for item in spec["acceptance"]))
        self.assertTrue(spec["constraints"])

    def test_workflow_stage_planning_prefers_hermes(self):
        chat = {"workspacePath": str(ROOT), "messages": [{"role": "user", "content": "修复 app.js bug"}]}
        rec = task_router.recommend_agent(chat, PROFILES, workflow_stage="planning", task_spec={"taskType": "code"})

        self.assertEqual(rec["primaryAgentId"], "detected-hermes-local")
        self.assertEqual(rec["workflowStage"], "planning")
        self.assertTrue(any("Workflow 阶段" in item for item in rec["reasons"]))

    def test_workflow_stage_executing_uses_task_type_agent(self):
        chat = {"workspacePath": str(ROOT), "messages": [{"role": "user", "content": "修复 app.js bug"}]}
        rec = task_router.recommend_agent(chat, PROFILES, workflow_stage="executing", task_spec={"taskType": "code"})

        self.assertEqual(rec["taskType"], "code")
        self.assertEqual(rec["primaryAgentId"], "detected-claude-code")

    def test_workflow_stage_review_forces_review_context(self):
        chat = {"workspacePath": str(ROOT), "messages": [{"role": "user", "content": "修复 app.js bug"}]}
        rec = task_router.recommend_agent(chat, PROFILES, workflow_stage="review", task_spec={"taskType": "code"})

        self.assertEqual(rec["taskType"], "review")
        self.assertEqual(rec["contextStrategy"], "full")
        self.assertIn(rec["primaryAgentId"], {"detected-claude-code", "detected-hermes-local"})

    def test_stage_action_generates_execution_instruction(self):
        chat = {"id": "s-action", "taskGoal": "修复发送失败", "workspacePath": str(ROOT), "messages": [{"role": "user", "content": "修复发送失败"}]}
        spec = {"goal": "修复发送失败", "taskType": "code", "stage": "executing", "acceptance": ["测试通过"], "finalDeliverable": "补丁"}
        rec = task_router.recommend_agent(chat, PROFILES, workflow_stage="executing", task_spec=spec)
        action = task_workflow.stage_action(chat, spec, "executing", rec)

        self.assertEqual(action["stage"], "executing")
        self.assertIn("Agent 执行指令包", action["title"])
        self.assertIn("修复发送失败", action["content"])
        self.assertIn("推荐 Agent", action["content"])


if __name__ == "__main__":
    unittest.main()
