import tempfile
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import storage


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_data_dir = storage.DATA_DIR
        self.old_db_file = storage.DB_FILE
        data_dir = Path(self.tmp.name)
        storage.configure(data_dir, data_dir / "agent-hub.db")

    def tearDown(self):
        storage.configure(self.old_data_dir, self.old_db_file)
        self.tmp.cleanup()

    def test_save_and_load_sessions_round_trip_messages_and_runs(self):
        saved = storage.save_sessions_to_db({
            "activeId": "s1",
            "sessions": [{
                "id": "s1",
                "title": "Storage test",
                "createdAt": 1,
                "updatedAt": 2,
                "messages": [
                    {"id": "m1", "role": "user", "content": "hello", "createdAt": 3},
                    {"id": "m2", "role": "assistant", "content": "world", "agentId": "hermes", "createdAt": 4},
                ],
            }],
        })

        self.assertEqual(saved["activeId"], "s1")
        self.assertEqual(saved["sessions"][0]["messages"][1]["agentId"], "hermes")

        storage.record_agent_run("s1", "hermes", "openai-chat", "success", saved["sessions"][0]["messages"], "done", latency_ms=12, debug={"model": "m1", "workspacePath": "/tmp/demo"})
        runs = storage.load_agent_runs("s1")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["agent_id"], "hermes")
        self.assertEqual(runs[0]["latency_ms"], 12)
        self.assertEqual(runs[0]["debug"]["model"], "m1")

    def test_message_order_is_preserved_even_when_created_at_is_mixed(self):
        storage.save_sessions_to_db({
            "activeId": "s-order",
            "sessions": [{
                "id": "s-order",
                "title": "Order test",
                "createdAt": 100,
                "updatedAt": 999999,
                "messages": [
                    {"id": "u1", "role": "user", "content": "第一问", "createdAt": 1000000},
                    {"id": "a1", "role": "assistant", "content": "第一答"},
                    {"id": "u2", "role": "user", "content": "第二问", "createdAt": 1000001},
                    {"id": "a2", "role": "assistant", "content": "第二答"},
                ],
            }],
        })

        loaded = storage.load_sessions_from_db()["sessions"][0]["messages"]
        self.assertEqual([m["id"] for m in loaded], ["u1", "a1", "u2", "a2"])
        self.assertEqual([m["messageOrder"] for m in loaded], [1, 2, 3, 4])

    def test_create_and_load_handoffs(self):
        storage.save_sessions_to_db({
            "activeId": "s1",
            "sessions": [{
                "id": "s1",
                "title": "Handoff test",
                "summary": "已经完成需求分析",
                "createdAt": 1,
                "updatedAt": 2,
                "messages": [{"id": "m1", "role": "user", "content": "继续做", "createdAt": 3}],
            }],
        })
        result = storage.create_handoff({
            "sessionId": "s1",
            "fromAgentId": "hermes",
            "toAgentId": "openclaw",
            "fromAgentLabel": "Hermes 本机",
            "toAgentLabel": "OpenClaw 本机",
            "chat": {"summary": "已经完成需求分析"},
        })
        self.assertIn("Hermes 本机 → OpenClaw 本机", result["handoff"])
        rows = storage.load_handoffs("s1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["from_agent_id"], "hermes")
        self.assertEqual(rows[0]["to_agent_id"], "openclaw")
        self.assertIn("已经完成需求分析", rows[0]["handoff_summary"])

    def test_load_context_package_from_storage(self):
        storage.save_sessions_to_db({
            "activeId": "s1",
            "sessions": [{
                "id": "s1",
                "title": "Package storage",
                "taskGoal": "统一上下文",
                "summary": "已有摘要",
                "workspacePath": str(ROOT),
                "workspaceName": "hermes-enterprise-ui",
                "workspaceGitRepo": True,
                "workspaceGitBranch": "test",
                "workspaceGitDirty": False,
                "workspaceCheckedAt": 123,
                "contextStrategy": "full",
                "createdAt": 1,
                "updatedAt": 2,
                "artifacts": [{"id": "a1", "kind": "text", "name": "note.txt", "mime": "text/plain", "text": "资料内容", "includeInContext": True, "createdAt": 3}],
                "messages": [{"id": "m1", "role": "user", "content": "继续", "createdAt": 4}],
            }],
        })
        storage.record_agent_run("s1", "hermes", "openai-chat", "success", [], "输出摘要", latency_ms=5)
        storage.create_handoff({"sessionId": "s1", "fromAgentId": "hermes", "toAgentId": "openclaw", "chat": {"summary": "已有摘要"}})
        result = storage.load_context_package("s1", "openclaw", "OpenClaw 本机")
        self.assertEqual(result["package"]["sessionId"], "s1")
        self.assertEqual(result["package"]["strategy"], "full")
        self.assertIn("统一上下文", result["text"])
        self.assertIn("note.txt", result["text"])
        self.assertIn("输出摘要", result["text"])
        self.assertIn(str(ROOT), result["text"])

    def test_project_memories_save_search_extract_and_context_package(self):
        workspace = str(ROOT)
        saved = storage.save_project_memory({
            "workspacePath": workspace,
            "kind": "command",
            "title": "测试命令",
            "content": "python3 -m unittest discover -s tests",
            "confidence": 0.9,
        })
        self.assertEqual(saved["kind"], "command")
        rows = storage.load_project_memories(workspace, "测试", 10)
        self.assertEqual(len(rows), 1)
        self.assertIn("unittest", rows[0]["content"])

        storage.save_sessions_to_db({
            "activeId": "s-memory",
            "sessions": [{
                "id": "s-memory",
                "title": "记忆提取",
                "taskGoal": "沉淀项目经验",
                "workspacePath": workspace,
                "createdAt": 1,
                "updatedAt": 2,
                "messages": [{"id": "m1", "role": "user", "content": "运行命令 python3 -m unittest discover -s tests", "createdAt": 3}],
            }],
        })
        extracted = storage.extract_project_memories("s-memory")
        self.assertTrue(extracted)
        package = storage.load_context_package("s-memory")
        self.assertIn("## 项目记忆", package["text"])
        self.assertIn("测试命令", package["text"])

    def test_task_spec_and_dispatch_recommendation_round_trip(self):
        storage.save_sessions_to_db({
            "activeId": "s-router",
            "sessions": [{
                "id": "s-router",
                "title": "Router",
                "createdAt": 1,
                "updatedAt": 2,
                "messages": [{"id": "m1", "role": "user", "content": "修复 bug", "createdAt": 3}],
            }],
        })
        spec = storage.save_task_spec({
            "sessionId": "s-router",
            "goal": "修复 bug",
            "taskType": "code",
            "stage": "planning",
            "acceptance": ["测试通过"],
            "files": ["server.py"],
        })
        self.assertEqual(spec["taskType"], "code")
        self.assertEqual(storage.load_task_spec("s-router")["acceptance"], ["测试通过"])

        rec = storage.record_dispatch_recommendation("s-router", {
            "taskType": "code",
            "primaryAgentId": "claude",
            "assistantAgentIds": ["hermes"],
            "contextStrategy": "code",
            "confidence": 0.8,
            "reasons": ["代码任务"],
            "warnings": ["需要工作区"],
        })
        accepted = storage.accept_dispatch_recommendation("s-router", rec["id"])
        self.assertTrue(accepted["accepted"])
        self.assertEqual(accepted["primaryAgentId"], "claude")
        loaded = storage.load_sessions_from_db()["sessions"][0]
        self.assertEqual(loaded["activeAgentId"], "claude")
        self.assertEqual(loaded["contextStrategy"], "code")

    def test_task_workflow_transition_updates_stage_session_and_events(self):
        storage.save_sessions_to_db({
            "activeId": "s-flow",
            "sessions": [{
                "id": "s-flow",
                "title": "Workflow",
                "taskGoal": "完成任务流转",
                "createdAt": 1,
                "updatedAt": 2,
                "messages": [{"id": "m1", "role": "user", "content": "开始", "createdAt": 3}],
            }],
        })
        storage.save_task_spec({"sessionId": "s-flow", "goal": "完成任务流转", "taskType": "workflow", "stage": "planning"})

        workflow = storage.transition_task_stage({"sessionId": "s-flow", "stage": "executing", "note": "进入执行"})

        self.assertEqual(workflow["currentStage"], "executing")
        self.assertEqual(workflow["events"][0]["fromStage"], "planning")
        self.assertEqual(workflow["events"][0]["toStage"], "executing")
        self.assertEqual(storage.load_task_spec("s-flow")["stage"], "executing")
        loaded = storage.load_sessions_from_db()["sessions"][0]
        self.assertEqual(loaded["taskStatus"], "active")
        self.assertEqual(loaded["nextStep"], "进入执行")


if __name__ == "__main__":
    unittest.main()
