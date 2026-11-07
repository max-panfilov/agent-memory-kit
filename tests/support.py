from __future__ import annotations

import os
import unittest
import uuid
from pathlib import Path

from agent_memory_kit.auth import Principal
from agent_memory_kit.knowledge import Snapshot
from agent_memory_kit.memory import validate_event
from agent_memory_kit.repository import Repository, seed

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content"
PRINCIPALS = CONTENT / "principals.json"


def make_event(**changes):
    values = {
        "project": 1,
        "application_id": "demo-app",
        "request_id": str(uuid.uuid4()),
        "event_type": "decision",
        "title": "Release decision",
        "summary": "Use canonical memory",
        "details": "Store decisions with source evidence",
        "occurred_at": "2026-11-07T09:00:00Z",
        "source_refs": [{"kind": "document", "ref": "docs/protocol.md"}],
        "event_status": "observed",
    }
    values.update(changes)
    return validate_event(**values)


def raw_event(event, **changes):
    value = {
        "project_id": event["project"],
        "application_id": event["application_id"],
        "request_id": event["id"],
        "event_type": event["event_type"],
        "title": event["title"],
        "summary": event["summary"],
        "details": event["details"],
        "occurred_at": event["occurred_at"],
        "source_refs": event["source_refs"],
        "shared_safe": True,
        "event_status": event["event_status"],
        "supersedes_id": event["supersedes_id"],
    }
    value.update(changes)
    return value


def make_principal(client_id="demo-writer", write=True, project=1, applications=("demo-app",)):
    return Principal(
        client_id=client_id,
        token_sha256="0" * 64,
        scopes=frozenset({"memory:read", "memory:write"} if write else {"memory:read"}),
        projects=frozenset({project}),
        applications={project: frozenset(applications)},
    )


class DatabaseTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.url = os.environ["AGENT_MEMORY_DATABASE_URL"]
        self.repository = await Repository.connect(self.url)
        async with self.repository.pool.acquire() as connection:
            await connection.execute("TRUNCATE project_events, memory_audit, applications, projects CASCADE")
        await seed(self.url, CONTENT)
        self.snapshot = Snapshot(CONTENT)
        self.principal = make_principal()

    async def asyncTearDown(self):
        await self.repository.close()
