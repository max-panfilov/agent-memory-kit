"""Async PostgreSQL storage with idempotent, serializable event appends."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg

from .errors import PublicError
from .memory import canonical_hash
from .paths import ROOT


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _event(row) -> dict:
    payload = _json(row["canonical_payload"])
    if not isinstance(payload, dict) or payload.get("payload_hash") != canonical_hash(payload):
        raise PublicError("MEMORY_STORED_EVENT_INVALID")
    return {
        **payload,
        "recorded_at": row["recorded_at"].isoformat(),
        "shared_safe": row["shared_safe"],
    }


async def ensure_database(url: str) -> None:
    parts = urlsplit(url)
    if parts.path != "/agent_memory_test":
        return
    admin_url = urlunsplit((parts.scheme, parts.netloc, "/agent_memory", parts.query, parts.fragment))
    admin = await asyncpg.connect(admin_url)
    try:
        exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = 'agent_memory_test'")
        if not exists:
            await admin.execute("CREATE DATABASE agent_memory_test")
    except asyncpg.exceptions.DuplicateDatabaseError:
        pass
    finally:
        await admin.close()


async def migrate(url: str) -> None:
    await ensure_database(url)
    connection = await asyncpg.connect(url)
    try:
        await connection.execute((ROOT / "migrations" / "0001_initial.sql").read_text(encoding="utf-8"))
    finally:
        await connection.close()


async def seed(url: str, content_root: Path) -> None:
    registry = json.loads((content_root / "projects.json").read_text(encoding="utf-8"))
    connection = await asyncpg.connect(url)
    try:
        async with connection.transaction():
            for project in registry["projects"]:
                await connection.execute(
                    "INSERT INTO projects(id, name, aliases) VALUES ($1, $2, $3::jsonb) "
                    "ON CONFLICT (id) DO NOTHING",
                    project["id"], project["name"], json.dumps(project.get("aliases", [])),
                )
                for application in project["applications"]:
                    await connection.execute(
                        "INSERT INTO applications(project, id, name) VALUES ($1, $2, $3) "
                        "ON CONFLICT (project, id) DO NOTHING",
                        project["id"], application["id"], application["name"],
                    )
    finally:
        await connection.close()


class Repository:
    def __init__(self, pool):
        self.pool = pool

    @classmethod
    async def connect(cls, url: str) -> Repository:
        pool = await asyncpg.create_pool(
            url,
            min_size=1,
            max_size=10,
            command_timeout=10,
            server_settings={
                "application_name": "agent-memory-kit",
                "statement_timeout": "5000",
                "lock_timeout": "2000",
                "idle_in_transaction_session_timeout": "5000",
            },
        )
        return cls(pool)

    async def close(self) -> None:
        await self.pool.close()

    async def _read_with_connection(self, connection, project: int, event_id: str):
        row = await connection.fetchrow(
            "SELECT canonical_payload, recorded_at, shared_safe FROM project_events WHERE project = $1 AND id = $2",
            project, event_id,
        )
        return _event(row) if row else None

    async def read(self, project: int, event_id: str) -> dict | None:
        async with self.pool.acquire() as connection:
            return await self._read_with_connection(connection, project, event_id)

    async def append(self, event: dict, actor: str) -> dict:
        event_id = event["id"]
        project = event["project"]
        application = event["application_id"]
        try:
            async with self.pool.acquire() as connection:
                async with connection.transaction(isolation="serializable"):
                    await connection.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", "agent-memory:" + event_id
                    )
                    existing = await connection.fetchrow(
                        "SELECT project, application_id, payload_hash FROM project_events WHERE id = $1", event_id
                    )
                    if existing:
                        if (
                            existing["project"] != project
                            or existing["application_id"] != application
                            or existing["payload_hash"] != event["payload_hash"]
                        ):
                            raise PublicError("MEMORY_REQUEST_ID_CONFLICT")
                        saved = await self._read_with_connection(connection, project, event_id)
                        if not saved:
                            raise PublicError("MEMORY_STORED_EVENT_REQUIRED")
                        return {"event": saved, "replayed": True, "readback_verified": True}
                    if event["supersedes_id"]:
                        previous = await self._read_with_connection(connection, project, event["supersedes_id"])
                        if not previous or previous["application_id"] != application:
                            raise PublicError("SUPERSEDED_MEMORY_NOT_AVAILABLE")
                    await connection.execute(
                        """
                        INSERT INTO project_events(
                            id, project, application_id, event_type, title, summary, details,
                            event_status, occurred_at, source_refs, supersedes_id, payload_hash,
                            shared_safe, canonical_payload
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::timestamptz, $10::jsonb, $11, $12, TRUE, $13::jsonb)
                        """,
                        event_id,
                        project,
                        application,
                        event["event_type"],
                        event["title"],
                        event["summary"],
                        event["details"],
                        event["event_status"],
                        datetime.fromisoformat(event["occurred_at"]),
                        json.dumps(event["source_refs"], ensure_ascii=False),
                        event["supersedes_id"],
                        event["payload_hash"],
                        json.dumps(event, ensure_ascii=False, separators=(",", ":")),
                    )
                    await connection.execute(
                        "INSERT INTO memory_audit(event_id, actor, payload_hash) VALUES ($1, $2, $3)",
                        event_id, actor, event["payload_hash"],
                    )
                    saved = await self._read_with_connection(connection, project, event_id)
                    if not saved or saved["payload_hash"] != event["payload_hash"]:
                        raise PublicError("MEMORY_WRITE_READBACK_REQUIRED")
                    return {"event": saved, "replayed": False, "readback_verified": True}
        except (asyncpg.exceptions.UniqueViolationError, asyncpg.exceptions.SerializationError) as error:
            async with self.pool.acquire() as connection:
                saved = await self._read_with_connection(connection, project, event_id)
            if not saved and isinstance(error, asyncpg.exceptions.SerializationError):
                raise PublicError("MEMORY_WRITE_RETRY_REQUIRED") from error
            if not saved or saved["application_id"] != application or saved["payload_hash"] != event["payload_hash"]:
                raise PublicError("MEMORY_REQUEST_ID_CONFLICT") from error
            return {"event": saved, "replayed": True, "readback_verified": True}
        except (
            asyncpg.exceptions.ConnectionDoesNotExistError,
            asyncpg.exceptions.InterfaceError,
            asyncio.TimeoutError,
            OSError,
        ) as error:
            raise PublicError("MEMORY_WRITE_OUTCOME_UNKNOWN") from error

    async def list(
        self,
        project: int,
        applications: list[str],
        offset: int = 0,
        limit: int = 51,
        event_type: str | None = None,
    ) -> list[dict]:
        if not applications:
            return []
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT canonical_payload, recorded_at, shared_safe
                FROM project_events
                WHERE project = $1
                  AND application_id = ANY($2::text[])
                  AND ($3::text IS NULL OR event_type = $3)
                ORDER BY occurred_at DESC, id DESC
                OFFSET $4 LIMIT $5
                """,
                project, applications, event_type, offset, limit,
            )
        return [_event(row) for row in rows]

    async def count(self, project: int | None = None) -> int:
        async with self.pool.acquire() as connection:
            if project is None:
                return await connection.fetchval("SELECT count(*) FROM project_events")
            return await connection.fetchval("SELECT count(*) FROM project_events WHERE project = $1", project)
