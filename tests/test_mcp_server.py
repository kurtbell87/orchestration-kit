from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "mcp" / "server.py"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


class MCPClient:
    def __init__(self, *, url: str, token: str):
        self.url = url
        self.token = token

    def call_raw(self, *, method: str, params: dict[str, Any], include_auth: bool = True, req_id: int = 1) -> tuple[int, dict[str, Any]]:
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if include_auth:
            headers["Authorization"] = f"Bearer {self.token}"

        req = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8")
                return resp.status, json.loads(body)
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8")
            parsed = json.loads(body) if body else {}
            return err.code, parsed

    def call_tool(self, *, name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        status, body = self.call_raw(method="tools/call", params={"name": name, "arguments": arguments})
        if status != 200:
            raise AssertionError(f"unexpected HTTP status {status}: {body}")
        if "error" in body:
            raise AssertionError(f"jsonrpc error: {body['error']}")

        result = body["result"]
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise AssertionError(f"missing structuredContent: {result}")
        return result, structured


class MCPServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.proc = None
        cls.max_output_bytes = int(os.getenv("ORCHESTRATION_KIT_MCP_MAX_OUTPUT_BYTES", "320"))
        external_url = os.getenv("ORCHESTRATION_KIT_TEST_EXTERNAL_MCP_URL")
        external_token = os.getenv("ORCHESTRATION_KIT_MCP_TOKEN")

        if external_url and external_token:
            cls.client = MCPClient(url=external_url, token=external_token)
            return

        cls.port = free_port()
        cls.token = "mcp-test-token"
        env = os.environ.copy()
        env.update(
            {
                "ORCHESTRATION_KIT_ROOT": str(ROOT),
                "ORCHESTRATION_KIT_MCP_HOST": "127.0.0.1",
                "ORCHESTRATION_KIT_MCP_PORT": str(cls.port),
                "ORCHESTRATION_KIT_MCP_TOKEN": cls.token,
                "ORCHESTRATION_KIT_MCP_MAX_OUTPUT_BYTES": str(cls.max_output_bytes),
                "ORCHESTRATION_KIT_DASHBOARD_AUTOSTART": "0",
            }
        )

        cls.proc = subprocess.Popen(
            ["python3", str(SERVER)],
            cwd=str(ROOT),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        deadline = time.time() + 15
        ready = False
        while time.time() < deadline:
            line = ""
            if cls.proc.stdout is not None:
                line = cls.proc.stdout.readline()
            if "orchestration-kit mcp ready" in line:
                ready = True
                break
            if cls.proc.poll() is not None:
                break
            time.sleep(0.1)

        if not ready:
            output = ""
            if cls.proc.stdout is not None:
                output = cls.proc.stdout.read() or ""
            raise RuntimeError(f"MCP server failed to start. output={output}")

        cls.client = MCPClient(url=f"http://127.0.0.1:{cls.port}/mcp", token=cls.token)

    @classmethod
    def tearDownClass(cls) -> None:
        if getattr(cls, "proc", None) is None:
            return
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    def test_auth_required(self) -> None:
        status, body = self.client.call_raw(method="initialize", params={}, include_auth=False)
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "unauthorized")

    def test_tools_are_pointer_only_and_bounded(self) -> None:
        init_status, init_body = self.client.call_raw(method="initialize", params={})
        self.assertEqual(init_status, 200)
        self.assertIn("result", init_body)

        run_result, run = self.client.call_tool(
            name="orchestrator.run",
            arguments={
                "kit": "research",
                "action": "status",
            },
        )
        self._assert_tool_text_bound(run_result)
        self.assertIsInstance(run.get("run_id"), str)
        self.assertTrue(str(run.get("capsule_path", "")).startswith("runs/"))
        self.assertTrue(str(run.get("manifest_path", "")).startswith("runs/"))
        self.assertTrue(str(run.get("events_path", "")).startswith("runs/"))
        self.assertIsInstance(run.get("log_paths"), list)
        self.assertNotIn("stdout", run)
        self.assertNotIn("stderr", run)

        request_result, request = self.client.call_tool(
            name="orchestrator.request_create",
            arguments={
                "from_kit": "research",
                "to_kit": "research",
                "action": "research.status",
                "run_id": run["run_id"],
                "must_read": [run["capsule_path"], run["manifest_path"]],
                "read_budget": {
                    "max_files": 8,
                    "max_total_bytes": 300000,
                    "allowed_paths": ["runs/*/capsules/*.md", "runs/*/manifests/*.json"],
                },
                "deliverables_expected": ["runs/*/capsules/*.md", "runs/*/manifests/*.json"],
                "priority": "normal",
            },
        )
        self._assert_tool_text_bound(request_result)
        self.assertTrue(str(request.get("request_id", "")).startswith("rq-"))
        self.assertTrue(str(request.get("request_path", "")).startswith("interop/requests/"))

        pump_result, pump = self.client.call_tool(
            name="orchestrator.pump",
            arguments={
                "mode": "once",
                "request_id": request["request_id"],
            },
        )
        self._assert_tool_text_bound(pump_result)
        self.assertIn(pump.get("status"), {"ok", "failed", "blocked"})
        self.assertTrue(str(pump.get("response_path", "")).startswith("interop/responses/"))

        response_path = ROOT / str(pump["response_path"])
        self.assertTrue(response_path.exists())
        response_payload = json.loads(response_path.read_text(encoding="utf-8"))
        self.assertIn("capsule_path", response_payload)
        self.assertIn("manifest_path", response_payload)

        run_info_result, run_info = self.client.call_tool(
            name="orchestrator.run_info",
            arguments={"run_id": "latest"},
        )
        self._assert_tool_text_bound(run_info_result)
        self.assertIsInstance(run_info.get("capsules"), list)
        self.assertIsInstance(run_info.get("manifests"), list)
        self.assertIsInstance(run_info.get("logs"), list)

        log_path = run_info["logs"][0] if run_info["logs"] else run["log_paths"][0]
        query_result, query = self.client.call_tool(
            name="orchestrator.query_log",
            arguments={
                "path": log_path,
                "mode": "tail",
                "n": 40,
            },
        )
        self._assert_tool_text_bound(query_result)
        self.assertEqual(query.get("path"), log_path)
        self.assertLessEqual(len(query.get("snippet", "").encode("utf-8")), self.max_output_bytes)

    def test_query_log_respects_output_cap(self) -> None:
        test_log = ROOT / "runs" / "mcp-test-bounds.log"
        test_log.parent.mkdir(parents=True, exist_ok=True)
        test_log.write_text("x" * 5000 + "\n", encoding="utf-8")

        result, payload = self.client.call_tool(
            name="orchestrator.query_log",
            arguments={
                "path": str(test_log.relative_to(ROOT)),
                "mode": "tail",
                "n": 200,
            },
        )

        self._assert_tool_text_bound(result)
        snippet = payload.get("snippet", "")
        self.assertLessEqual(len(snippet.encode("utf-8")), self.max_output_bytes)

    def _assert_tool_text_bound(self, result: dict[str, Any]) -> None:
        content = result.get("content", [])
        self.assertIsInstance(content, list)
        self.assertGreaterEqual(len(content), 1)
        text = content[0].get("text", "") if isinstance(content[0], dict) else ""
        self.assertLessEqual(len(str(text).encode("utf-8")), self.max_output_bytes)


if __name__ == "__main__":
    unittest.main()
