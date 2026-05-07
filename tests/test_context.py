import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("agent_hub_server", ROOT / "server.py")
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)
import storage


class ContextRulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        self.old_data_dir = server.DATA_DIR
        self.old_db_file = server.DB_FILE
        self.old_config_file = server.CONFIG_FILE
        self.old_storage_data_dir = storage.DATA_DIR
        self.old_storage_db_file = storage.DB_FILE
        server.DATA_DIR = data_dir
        server.DB_FILE = data_dir / "agent-hub.db"
        server.CONFIG_FILE = data_dir / "agent-hub.json"
        storage.configure(data_dir, data_dir / "agent-hub.db")

    def tearDown(self):
        server.DATA_DIR = self.old_data_dir
        server.DB_FILE = self.old_db_file
        server.CONFIG_FILE = self.old_config_file
        storage.configure(self.old_storage_data_dir, self.old_storage_db_file)
        self.tmp.cleanup()

    def test_unpinned_artifacts_are_not_injected_into_context(self):
        server.save_sessions_to_db({
            "activeId": "s1",
            "sessions": [{
                "id": "s1",
                "title": "Context test",
                "createdAt": 1,
                "updatedAt": 2,
                "messages": [
                    {"id": "m1", "role": "user", "content": "请分析附件"},
                ],
                "artifacts": [
                    {
                        "id": "a1",
                        "kind": "file",
                        "name": "pinned.md",
                        "mime": "text/markdown",
                        "text": "PINNED_CONTEXT",
                        "includeInContext": True,
                        "createdAt": 3,
                    },
                    {
                        "id": "a2",
                        "kind": "file",
                        "name": "private.md",
                        "mime": "text/markdown",
                        "text": "PRIVATE_CONTEXT",
                        "includeInContext": False,
                        "createdAt": 4,
                    },
                ],
            }],
        })

        artifact_context = server.session_artifact_context("s1")
        self.assertIn("PINNED_CONTEXT", artifact_context)
        self.assertNotIn("PRIVATE_CONTEXT", artifact_context)

        compacted = server.compact_context_messages(
            [
                {"role": "system", "content": "你是 Agent Hub。"},
                {"role": "user", "content": "继续处理"},
            ],
            "s1",
            "agent-test",
        )
        system_text = compacted[0]["content"]
        self.assertIn("PINNED_CONTEXT", system_text)
        self.assertNotIn("PRIVATE_CONTEXT", system_text)


if __name__ == "__main__":
    unittest.main()
