#!/usr/bin/env python3
"""End-to-end acceptance through the actual MCP Streamable HTTP client."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone

from fastmcp import Client

URL = os.environ.get("AGENT_MEMORY_URL", "http://127.0.0.1:8775/mcp")
EXPECTED = {
    "record_project_memory",
    "read_project_memory",
    "get_project_brief",
    "get_project_timeline",
    "search_project_memory",
    "get_work_context",
    "read_document",
    "search_knowledge",
}


def event(request_id: str, title: str, **changes) -> dict:
    value = {
        "project_id": 1,
        "application_id": "demo-app",
        "request_id": request_id,
        "event_type": "decision",
        "title": title,
        "summary": "Synthetic MCP acceptance event",
        "details": "Canonical event created by the real MCP protocol smoke client.",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "source_refs": [{"kind": "document", "ref": "docs/protocol.md"}],
        "shared_safe": True,
        "event_status": "observed",
    }
    value.update(changes)
    return value


async def main() -> None:
    writer_token = os.environ["DEMO_WRITER_TOKEN"]
    reader_token = os.environ["DEMO_READER_TOKEN"]
    other_token = os.environ["OTHER_WRITER_TOKEN"]
    event_id = str(uuid.uuid4())
    correction_id = str(uuid.uuid4())
    result = {"tools": 8}

    async with Client(URL, auth=writer_token, mode="legacy") as client:
        assert {tool.name for tool in await client.list_tools()} == EXPECTED
        context = await client.call_tool("get_work_context", {
            "project": "demo", "application_id": "demo-app", "repo_sha": "a" * 40,
        })
        assert context.data["application_id"] == "demo-app"
        created = await client.call_tool("record_project_memory", event(event_id, "MCP release decision"))
        assert created.data["replayed"] is False
        replayed = await client.call_tool("record_project_memory", event(event_id, "MCP release decision"))
        assert replayed.data["replayed"] is True
        conflict = await client.call_tool(
            "record_project_memory", event(event_id, "Changed payload"), raise_on_error=False
        )
        assert conflict.is_error
        result["replay_and_conflict"] = True
        denied = await client.call_tool("record_project_memory", event(str(uuid.uuid4()), "Unsafe", shared_safe=False), raise_on_error=False)
        assert denied.is_error
        correction = event(
            correction_id,
            "Corrected MCP decision",
            event_type="correction",
            event_status="confirmed",
            supersedes_id=event_id,
        )
        await client.call_tool("record_project_memory", correction)
        timeline = await client.call_tool("get_project_timeline", {"project_id": 1, "limit": 50})
        ids = {row["id"] for row in timeline.data["events"]}
        assert {event_id, correction_id} <= ids
        search = await client.call_tool("search_project_memory", {"project_id": 1, "query": "MCP decision"})
        assert all(item["event"]["id"] != event_id for item in search.data["results"])
        result["correction_and_timeline"] = True

    async with Client(URL, auth=reader_token, mode="legacy") as client:
        names = {tool.name for tool in await client.list_tools()}
        assert names == EXPECTED - {"record_project_memory"}
        denied = await client.call_tool(
            "record_project_memory", event(str(uuid.uuid4()), "Read-only write"), raise_on_error=False
        )
        assert denied.is_error
        result["read_only_denied"] = True

    async with Client(URL, auth=other_token, mode="legacy") as client:
        denied = await client.call_tool("get_project_brief", {"project_id": 1}, raise_on_error=False)
        assert denied.is_error
        result["cross_scope_denied"] = True

    for invalid in (None, "definitely-wrong-token"):
        try:
            async with Client(URL, auth=invalid, mode="legacy") as client:
                await client.list_tools()
        except Exception:
            continue
        raise AssertionError("Invalid authentication was accepted")
    result["invalid_auth_denied"] = True
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
