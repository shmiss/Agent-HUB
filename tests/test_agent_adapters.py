import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import agent_adapters


def assert_chat_contract(testcase, result):
    testcase.assertIsInstance(result.response.get("id"), str)
    testcase.assertEqual(result.response.get("object"), "chat.completion")
    testcase.assertIsInstance(result.response.get("created"), int)
    testcase.assertIsInstance(result.response.get("model"), str)
    testcase.assertEqual(result.response["choices"][0]["message"]["role"], "assistant")
    testcase.assertIn("content", result.response["choices"][0]["message"])
    testcase.assertIn("_ui", result.response)
    testcase.assertIn("latencyMs", result.response["_ui"])
    testcase.assertIn("agentLabel", result.response["_ui"])
    for key in ("session_id", "agent_id", "adapter", "status", "input_messages", "output_text", "latency_ms"):
        testcase.assertIn(key, result.run)


def assert_health_contract(testcase, result, status):
    testcase.assertIn(status, {200, 501, 502})
    for key in ("ok", "ui", "baseUrl", "model", "activeAgent", "latencyMs"):
        testcase.assertIn(key, result)
    testcase.assertIsInstance(result["ok"], bool)
    testcase.assertIsInstance(result["latencyMs"], int)


def fake_runtime():
    def run_cli(command, timeout=0, cwd=None):
        return {"result": "CLAUDE_OK"} if command and command[0] == "claude" else {"result": {"payloads": [{"text": "CLI_OK"}]}}
    return agent_adapters.AgentRuntime(
        headers=lambda key: {"Content-Type": "application/json"},
        ensure_hermes_api_server=lambda profile, base: {"attempted": False},
        run_cli_json=run_cli,
        openclaw_message_from_messages=lambda messages: "hello",
        openclaw_result_to_text=lambda payload: payload["result"]["payloads"][0]["text"],
        openclaw_gateway_agent_turn=lambda profile, message, session_id, timeout_sec=300: {"result": {"payloads": [{"text": "RPC_OK"}]}},
        openclaw_gateway_pool_status=lambda profile: {"authenticated": True},
        openclaw_gateway_url=lambda profile: "ws://127.0.0.1:18789",
        openclaw_agent_configured=lambda agent_id: True,
        openclaw_gateway_port_from_config=lambda: 18789,
        is_port_open=lambda host, port, timeout: True,
    )


class AgentAdaptersTest(unittest.TestCase):
    def test_openclaw_cli_turn_uses_common_response_shape(self):
        active = {
            "adapter": "openclaw-cli",
            "label": "OpenClaw 本机",
            "agentId": "main",
            "model": "local-model",
            "baseUrl": "openclaw",
        }

        result = agent_adapters.execute_chat_turn(
            active,
            {"session_id": "s1", "messages": [{"role": "user", "content": "你好"}], "timeout": 1},
            "",
            "",
            "fallback-model",
            fake_runtime(),
        )

        self.assertEqual(result.response["choices"][0]["message"]["content"], "CLI_OK")
        assert_chat_contract(self, result)
        self.assertEqual(result.response["_ui"]["adapter"], "openclaw-cli")
        self.assertEqual(result.run["session_id"], "s1")
        self.assertEqual(result.run["agent_id"], "main")

    def test_openclaw_rpc_turn_reports_connection_reuse(self):
        active = {
            "adapter": "openclaw-gateway-rpc",
            "label": "OpenClaw RPC",
            "agentId": "main",
            "model": "local-model",
        }

        result = agent_adapters.execute_chat_turn(
            active,
            {"session_id": "s2", "messages": [{"role": "user", "content": "你好"}], "timeout": 1},
            "",
            "",
            "fallback-model",
            fake_runtime(),
        )

        self.assertEqual(result.response["choices"][0]["message"]["content"], "RPC_OK")
        assert_chat_contract(self, result)
        self.assertEqual(result.response["_ui"]["adapter"], "openclaw-gateway-rpc")
        self.assertTrue(result.response["_ui"]["rpcReused"])
        self.assertEqual(result.run["adapter"], "openclaw-gateway-rpc")

    def test_unsupported_adapter_raises_typed_error(self):
        with self.assertRaises(agent_adapters.UnsupportedAgentAdapter):
            agent_adapters.execute_chat_turn(
                {"adapter": "unknown"},
                {"messages": []},
                "",
                "",
                "model",
                fake_runtime(),
            )

    def test_claude_code_cli_turn_uses_print_json_mode(self):
        active = {
            "adapter": "claude-code-cli",
            "label": "Claude Code 本机",
            "type": "claude",
            "agentId": "claude-code",
            "baseUrl": "claude",
            "model": "sonnet",
        }

        result = agent_adapters.execute_chat_turn(
            active,
            {
                "session_id": "s3",
                "messages": [
                    {"role": "system", "content": "系统上下文"},
                    {"role": "user", "content": "你好"},
                ],
                "timeout": 1,
            },
            "claude",
            "",
            "sonnet",
            fake_runtime(),
        )

        self.assertEqual(result.response["choices"][0]["message"]["content"], "CLAUDE_OK")
        assert_chat_contract(self, result)
        self.assertEqual(result.response["_ui"]["adapter"], "claude-code-cli")
        self.assertIn("--bare", result.response["_ui"]["command"])
        self.assertEqual(result.run["agent_id"], "claude-code")

    def test_claude_code_cli_embeds_context_package_in_user_prompt(self):
        captured = {}
        runtime = fake_runtime()
        def capture_command(command, timeout=0, cwd=None):
            captured["command"] = command
            captured["cwd"] = cwd
            return {"result": "CLAUDE_OK"}
        runtime.run_cli_json = capture_command
        active = {
            "adapter": "claude-code-cli",
            "label": "Claude Code 本机",
            "agentId": "claude-code",
            "baseUrl": "claude",
            "model": "sonnet",
        }

        agent_adapters.execute_chat_turn(
            active,
            {
                "session_id": "s4",
                "messages": [
                    {"role": "system", "content": "[Agent Hub Context Package v1]\n## 关键文件路径\n1. [输出文件] /Users/me/demo/out.docx"},
                    {"role": "user", "content": "方案1"},
                ],
                "timeout": 1,
            },
            "claude",
            "",
            "sonnet",
            runtime,
        )

        prompt = captured["command"][captured["command"].index("-p") + 1]
        self.assertIn("Agent Hub Context Package v1", prompt)
        self.assertIn("/Users/me/demo/out.docx", prompt)
        self.assertIn("[用户当前请求]\n方案1", prompt)

    def test_claude_code_cli_maps_workspace_auto_policy(self):
        captured = {}
        runtime = fake_runtime()
        def capture_command(command, timeout=0, cwd=None):
            captured["command"] = command
            captured["cwd"] = cwd
            return {"result": "CLAUDE_OK"}
        runtime.run_cli_json = capture_command
        active = {
            "adapter": "claude-code-cli",
            "label": "Claude Code 本机",
            "agentId": "claude-code",
            "baseUrl": "claude",
            "model": "sonnet",
        }

        agent_adapters.execute_chat_turn(
            active,
            {
                "session_id": "s5",
                "messages": [{"role": "user", "content": "继续"}],
                "workspace_path": str(ROOT),
                "approval_policy": "workspace-auto",
                "timeout": 1,
            },
            "claude",
            "",
            "sonnet",
            runtime,
        )

        self.assertIn("--add-dir", captured["command"])
        self.assertIn(str(ROOT), captured["command"])
        self.assertIn("acceptEdits", captured["command"])
        self.assertEqual(captured["cwd"], str(ROOT))

    def test_openclaw_rpc_health_uses_common_status_shape(self):
        result, status = agent_adapters.health_check(
            {
                "adapter": "openclaw-gateway-rpc",
                "label": "OpenClaw RPC",
                "agentId": "main",
                "binaryPath": "/bin/echo",
            },
            "ws://127.0.0.1:18789",
            "fallback-model",
            fake_runtime(),
        )

        self.assertEqual(status, 200)

    def test_cancelable_cli_runner_terminates_process(self):
        import server

        session_id = "cancel-test"
        run_id = "run-1"

        def cancel_soon():
            time.sleep(0.3)
            server.RUN_CANCEL_FLAGS.add(server.run_key(session_id, run_id))

        threading.Thread(target=cancel_soon, daemon=True).start()
        with self.assertRaisesRegex(RuntimeError, "任务已停止"):
            server.run_cli_json_cancelable(
                ["python3", "-c", "import time; time.sleep(5); print('{}')"],
                timeout=10,
                session_id=session_id,
                run_id=run_id,
            )
        server.clear_cancel(session_id, run_id)

    def test_openclaw_rpc_health_reports_gateway_down(self):
        runtime = fake_runtime()
        runtime.is_port_open = lambda host, port, timeout: False
        result, status = agent_adapters.health_check(
            {"adapter": "openclaw-gateway-rpc", "agentId": "main"},
            "ws://127.0.0.1:18789",
            "fallback-model",
            runtime,
        )

        self.assertEqual(status, 502)
        assert_health_contract(self, result, status)
        self.assertFalse(result["ok"])
        self.assertIn("not reachable", result["openclawError"])

    def test_unsupported_adapter_health_returns_501(self):
        result, status = agent_adapters.health_check(
            {"adapter": "unknown"},
            "",
            "fallback-model",
            fake_runtime(),
        )

        self.assertEqual(status, 501)
        assert_health_contract(self, result, status)
        self.assertFalse(result["ok"])

    def test_claude_code_cli_health_reports_binary(self):
        result, status = agent_adapters.health_check(
            {
                "adapter": "claude-code-cli",
                "label": "Claude Code 本机",
                "binaryPath": "/bin/echo",
                "model": "sonnet",
            },
            "/bin/echo",
            "sonnet",
            fake_runtime(),
        )

        self.assertEqual(status, 200)
        assert_health_contract(self, result, status)
        self.assertTrue(result["ok"])
        self.assertEqual(result["claude"]["mode"], "print-json")


if __name__ == "__main__":
    unittest.main()
