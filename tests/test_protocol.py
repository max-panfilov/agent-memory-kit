from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from fastmcp import Client

from agent_memory_kit.server import create_server
from support import CONTENT, PRINCIPALS, DatabaseTestCase, make_event, raw_event


class ProtocolTests(DatabaseTestCase):
    async def test_exact_tools_and_memory_flow(self):
        server = await create_server(CONTENT, PRINCIPALS, repository=self.repository)
        async with Client(server) as client:
            tools = {tool.name for tool in await client.list_tools()}
            self.assertEqual(tools, {
                "record_project_memory",
                "read_project_memory",
                "get_project_brief",
                "get_project_timeline",
                "search_project_memory",
                "get_work_context",
                "read_document",
                "search_knowledge",
            })
            event = make_event()
            created = await client.call_tool("record_project_memory", raw_event(event))
            self.assertFalse(created.data["replayed"])
            replay = await client.call_tool("record_project_memory", raw_event(event))
            self.assertTrue(replay.data["replayed"])
            conflict = await client.call_tool(
                "record_project_memory", raw_event(event, title="Changed"), raise_on_error=False
            )
            self.assertTrue(conflict.is_error)
            read = await client.call_tool("read_project_memory", {"project_id": 1, "request_id": event["id"]})
            self.assertEqual(read.data["event"]["payload_hash"], event["payload_hash"])
            context = await client.call_tool("get_work_context", {
                "project": "demo", "application_id": "demo-app", "repo_sha": "a" * 40,
            })
            self.assertEqual(context.data["snapshot_id"], self.snapshot.id)
            document = await client.call_tool("read_document", {"document_id": "memory-contract"})
            self.assertIn("Канонический журнал", document.data["content"])
            search = await client.call_tool("search_project_memory", {"project_id": 1, "query": "Release"})
            self.assertEqual(search.data["results"][0]["event"]["id"], event["id"])

    async def test_stdio_transport_parity(self):
        event = make_event()
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "stdio-acceptance", "version": "0.1.0"},
            }},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                "name": "record_project_memory", "arguments": raw_event(event),
            }},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
                "name": "read_project_memory", "arguments": {"project_id": 1, "request_id": event["id"]},
            }},
        ]
        environment = dict(os.environ)
        environment.update({
            "AGENT_MEMORY_CONTENT_ROOT": str(CONTENT),
            "AGENT_MEMORY_PRINCIPALS_PATH": str(PRINCIPALS),
            "AGENT_MEMORY_STDIO_CLIENT_ID": "demo-writer",
            "PYTHONUNBUFFERED": "1",
        })
        result = subprocess.run(
            [sys.executable, "-m", "agent_memory_kit.server", "--transport", "stdio"],
            input="".join(json.dumps(request, ensure_ascii=False) + "\n" for request in requests),
            text=True,
            capture_output=True,
            timeout=30,
            env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        replies = {}
        for line in result.stdout.splitlines():
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in message:
                replies[message["id"]] = message
        self.assertEqual(set(replies), {1, 2, 3, 4}, "STDOUT:\n" + result.stdout + "\nSTDERR:\n" + result.stderr)
        self.assertEqual(len(replies[2]["result"]["tools"]), 8)
        self.assertIn("result", replies[3], str(replies))
        self.assertFalse(replies[3]["result"]["structuredContent"]["replayed"])
        self.assertEqual(
            replies[4]["result"]["structuredContent"]["event"]["payload_hash"],
            event["payload_hash"],
        )


if __name__ == "__main__":
    unittest.main()
