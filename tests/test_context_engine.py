import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import context_engine


class ContextEngineTest(unittest.TestCase):
    def test_compact_context_messages_adds_summary_handoff_and_artifacts(self):
        messages = [
            {"role": "system", "content": "你是 Agent Hub。"},
            {"role": "user", "content": "第一轮需求"},
            {"role": "assistant", "content": "第一轮回复"},
            {"role": "user", "content": "继续"},
        ]

        compacted = context_engine.compact_context_messages(
            messages,
            summary="任务摘要",
            handoff="交接摘要",
            artifact_context="[Agent Hub 会话附件资产]\n1. spec.md [file]\n   摘要: 资料摘要",
        )

        self.assertEqual(compacted[0]["role"], "system")
        self.assertIn("任务摘要", compacted[0]["content"])
        self.assertIn("交接摘要", compacted[0]["content"])
        self.assertIn("资料摘要", compacted[0]["content"])
        self.assertLessEqual(len(compacted), 4)

    def test_text_from_content_handles_multimodal_parts(self):
        text = context_engine.text_from_content([
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
        ])

        self.assertIn("hello", text)
        self.assertIn("[图片附件]", text)

    def test_context_package_to_text_contains_task_assets_runs_and_handoffs(self):
        chat = {
            "id": "s1",
            "title": "上下文包测试",
            "taskGoal": "完成多 Agent 接力",
            "taskStatus": "active",
            "nextStep": "交给 OpenClaw 执行",
            "summary": "Hermes 已完成分析",
            "workspacePath": "/tmp/agent-hub-demo",
            "workspaceName": "agent-hub-demo",
            "workspaceGitRepo": True,
            "workspaceGitBranch": "main",
            "workspaceGitDirty": True,
            "messages": [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "可以继续"}],
        }
        package = context_engine.context_package_from_chat(
            chat,
            artifacts=[{"id": "a1", "name": "需求.pdf", "kind": "file", "mime": "application/pdf", "text": "需求摘要", "includeInContext": True}],
            runs=[{"agent_id": "hermes", "adapter": "openai-chat", "status": "success", "output_summary": "分析完成", "latency_ms": 10}],
            handoffs=[{"from_agent_id": "hermes", "to_agent_id": "openclaw", "handoff_summary": "请继续执行"}],
            memories=[{"kind": "decision", "title": "发布约定", "content": "修改后必须运行 release.sh", "confidence": 0.9}],
            task_spec={"goal": "完成多 Agent 接力", "taskType": "workflow", "stage": "planning", "acceptance": ["能切换 Agent 且上下文不丢"], "finalDeliverable": "接力结果"},
            target_agent_id="openclaw",
            target_agent_label="OpenClaw 本机",
        )
        text = context_engine.context_package_to_text(package)
        self.assertEqual(package["schema"], "agent-hub.context-package.v1")
        self.assertIn("完成多 Agent 接力", text)
        self.assertIn("需求.pdf", text)
        self.assertIn("分析完成", text)
        self.assertIn("hermes → openclaw", text)
        self.assertIn("/tmp/agent-hub-demo", text)
        self.assertIn("branch=main", text)
        self.assertIn("## 项目记忆", text)
        self.assertIn("release.sh", text)
        self.assertIn("## 任务规格 Task Spec", text)
        self.assertIn("能切换 Agent", text)

    def test_context_package_promotes_file_paths(self):
        chat = {
            "id": "s2",
            "title": "文件接力",
            "taskGoal": "重排 Word",
            "messages": [
                {"role": "user", "content": "file: /Users/me/demo/source.docx 请排版"},
                {"role": "assistant", "content": "已生成！新文件位置：/Users/me/demo/source-公众号版.docx"},
            ],
        }
        package = context_engine.context_package_from_chat(chat, runs=[])
        text = context_engine.context_package_to_text(package)
        self.assertIn("## 关键文件路径", text)
        self.assertIn("/Users/me/demo/source.docx", text)
        self.assertIn("/Users/me/demo/source-公众号版.docx", text)
        self.assertIn("不要再询问文件在哪里", text)

    def test_context_strategy_light_omits_heavy_sections(self):
        chat = {
            "id": "s3",
            "title": "策略测试",
            "taskGoal": "快速问答",
            "contextStrategy": "light",
            "messages": [{"role": "user", "content": "one"}, {"role": "assistant", "content": "two"}],
        }
        package = context_engine.context_package_from_chat(
            chat,
            runs=[{"agent_id": "a", "status": "success", "output_summary": "run"}],
            memories=[{"kind": "command", "title": "cmd", "content": "make test"}],
            strategy="light",
        )
        text = context_engine.context_package_to_text(package)
        self.assertEqual(package["strategy"], "light")
        self.assertNotIn("## 最近执行记录", text)
        self.assertNotIn("## 项目记忆", text)

    def test_context_strategy_full_includes_memories_and_runs(self):
        chat = {
            "id": "s4",
            "title": "策略测试",
            "taskGoal": "复杂任务",
            "messages": [{"role": "user", "content": "one"}],
        }
        package = context_engine.context_package_from_chat(
            chat,
            runs=[{"agent_id": "a", "status": "success", "output_summary": "run"}],
            memories=[{"kind": "command", "title": "cmd", "content": "make test"}],
            strategy="full",
        )
        text = context_engine.context_package_to_text(package)
        self.assertEqual(package["strategy"], "full")
        self.assertIn("## 最近执行记录", text)
        self.assertIn("## 项目记忆", text)


if __name__ == "__main__":
    unittest.main()
