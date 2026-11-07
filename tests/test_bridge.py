from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent_memory_kit.bridge import Bridge, load_config


class BridgeTests(unittest.TestCase):
    def test_protected_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "client.json"
            path.write_text(json.dumps({"url": "http://127.0.0.1:8775/mcp", "token": "test"}))
            path.chmod(0o600)
            self.assertEqual(load_config(path)["token"], "test")
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                load_config(path)
            path.chmod(0o600)
            for url in ["http://example.com:8775/mcp", "http://user@127.0.0.1:8775/mcp", "http://127.0.0.1:8775/mcp?token=test"]:
                path.write_text(json.dumps({"url": url, "token": "test"}))
                with self.assertRaises(ValueError):
                    load_config(path)

    def test_real_stdio_transport(self):
        received = []
        lock = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with lock:
                    received.append((message, dict(self.headers)))
                method = message.get("method")
                if method == "fail":
                    self.send_response(503)
                    self.end_headers()
                    self.wfile.write(b"secret backend details")
                    return
                if "id" not in message or "method" not in message:
                    self.send_response(202)
                    self.end_headers()
                    return
                reply = {"jsonrpc": "2.0", "id": message["id"], "result": {"method": method}}
                if method == "initialize":
                    reply["result"]["protocolVersion"] = "2025-06-18"
                if method == "slow":
                    time.sleep(0.05)
                self.send_response(200)
                if method in ("initialize", "fast", "slow"):
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Mcp-Session-Id", "session-" + str(message["id"]))
                    self.end_headers()
                    self.wfile.write(b': comment\n\ndata: {"jsonrpc":"2.0",\ndata: "method":"notifications/progress","params":{}}\n\n')
                    self.wfile.write(("data: " + json.dumps(reply) + "\n\n").encode())
                else:
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(reply).encode())

            def do_DELETE(self):
                with lock:
                    received.append(({"method": "DELETE"}, dict(self.headers)))
                self.send_response(204)
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            runner = "from agent_memory_kit.bridge import Bridge; Bridge(" + repr({
                "url": "http://127.0.0.1:" + str(server.server_port) + "/mcp", "token": "test"}) + ").run()"
            requests = [
                {"id": 1, "method": "initialize", "params": {}},
                {"method": "notifications/initialized"},
                {"id": 2, "method": "slow"}, {"id": 3, "method": "fast"},
                {"method": "notifications/cancelled", "params": {"requestId": 2}},
                {"id": 4, "result": {}},
                {"id": 5, "method": "json"}, {"id": 6, "method": "fail"},
            ]
            result = subprocess.run(
                [sys.executable, "-c", runner],
                input="".join(json.dumps({"jsonrpc": "2.0", **request}) + "\n" for request in requests),
                text=True,
                capture_output=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            replies = [json.loads(line) for line in result.stdout.splitlines()]
            by_id = {reply["id"]: reply for reply in replies if "id" in reply}
            self.assertEqual(set(by_id), {1, 2, 3, 5, 6})
            self.assertIn("error", by_id[6])
            self.assertNotIn("secret", result.stdout + result.stderr)
            self.assertEqual(sum(request["method"] == "fail" for request, headers in received if "method" in request), 1)
            ids = [reply["id"] for reply in replies if "id" in reply]
            self.assertLess(ids.index(3), ids.index(2))
            for request, headers in received:
                self.assertEqual(headers["Authorization"], "Bearer test")
                if request.get("method") != "initialize":
                    self.assertEqual(headers["Mcp-Session-Id"], "session-1")
                    self.assertEqual(headers["MCP-Protocol-Version"], "2025-06-18")
            self.assertEqual(received[-1][0]["method"], "DELETE")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_redirect_does_not_send_token(self):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                calls.append(self.path)
                self.send_response(307)
                self.send_header("Location", "/leak")
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            output = io.StringIO()
            Bridge({"url": f"http://127.0.0.1:{server.server_port}/mcp", "token": "test"}, output).forward(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            )
            self.assertEqual(calls, ["/mcp"])
            self.assertIn("error", json.loads(output.getvalue()))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
