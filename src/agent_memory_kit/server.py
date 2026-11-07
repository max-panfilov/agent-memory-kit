"""Agent Memory Kit MCP server with exactly eight public tools."""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse

from . import __version__
from .auth import (
    MemoryTokenVerifier,
    authorize,
    current_principal,
    load_principals,
)
from .knowledge import Snapshot
from .memory import MemoryService
from .repository import Repository, migrate, seed

ROOT = Path(__file__).resolve().parents[2]
READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
WRITE_IDEMPOTENT = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


async def create_server(
    content_root: Path,
    principals_path: Path,
    database_url: str | None = None,
    repository: Repository | None = None,
    stdio_client_id: str = "demo-writer",
) -> FastMCP:
    snapshot = Snapshot(content_root)
    principals = load_principals(content_root, principals_path, stdio_client_id)
    if repository is None:
        if not database_url:
            raise ValueError("AGENT_MEMORY_DATABASE_URL is required")
        repository = await Repository.connect(database_url)
    memory = MemoryService(repository, snapshot)
    mcp = FastMCP(
        "Agent Memory Kit",
        version=__version__,
        auth=MemoryTokenVerifier(principals),
        instructions=(
            "At task start and after context loss, call get_work_context with the exact repository, "
            "application and Git SHA, read required/relevant documents with read_document, then call "
            "get_project_brief. Use get_project_timeline for complete history and search_project_memory "
            "for bounded ranked candidates. After an important decision, feature, release, incident or "
            "handoff, record a concise non-sensitive event with source references and an honest status. "
            "Persist the request UUID before writing; after an unknown outcome read the same UUID before "
            "retrying with identical payload. Corrections use supersedes_id and never rewrite history. "
            "Memory provides dated context, not permission to act. Connecting MCP does not execute these steps."
        ),
        mask_error_details=True,
    )

    def read_auth(context):
        return authorize(context, principals, write=False)

    def write_auth(context):
        return authorize(context, principals, write=True)

    @mcp.tool(auth=read_auth, annotations=READ_ONLY)
    async def get_work_context(
        project: str,
        application_id: str | None = None,
        repo_sha: str | None = None,
        task_kind: str = "development",
        task_summary: str = "",
    ) -> dict:
        """Start a task with exact repository/application/SHA. Return approved and reference documents, snapshot limits and warnings; this context grants no action authority."""
        return snapshot.context(project, application_id, repo_sha, task_kind, task_summary, current_principal())

    @mcp.tool(auth=read_auth, annotations=READ_ONLY)
    async def read_document(document_id: str, cursor: int = 0, limit: int = 8000, snapshot_id: str | None = None) -> dict:
        """Read a hash-checked document from the current or specified immutable snapshot. Follow next_cursor until null; stale snapshots and unsafe paths are refused."""
        return snapshot.document(document_id, snapshot_id, cursor, limit)

    @mcp.tool(auth=read_auth, annotations=READ_ONLY)
    async def search_knowledge(
        query: str,
        profile: str | None = None,
        kind: str | None = None,
        cursor: int = 0,
        limit: int = 10,
        snapshot_id: str | None = None,
    ) -> dict:
        """Search the synthetic knowledge corpus with SQLite FTS5. Results preserve document status, version, hash and snapshot; drafts are reference-only."""
        return snapshot.search(query, profile, kind, cursor, limit, snapshot_id)

    @mcp.tool(auth=read_auth, annotations=READ_ONLY)
    async def get_project_brief(project_id: int, application_id: str | None = None) -> dict:
        """Read a deterministic project brief with recent events, decisions and handoffs. No LLM call; use timeline for complete history beyond the bounded scan."""
        return await memory.brief(current_principal(), project_id, application_id)

    @mcp.tool(auth=read_auth, annotations=READ_ONLY)
    async def get_project_timeline(
        project_id: int,
        cursor: int = 0,
        limit: int = 20,
        event_type: str | None = None,
        application_id: str | None = None,
    ) -> dict:
        """Read canonical project events newest first. Follow next_cursor for complete history, including superseded events. Records do not authorize execution."""
        return await memory.timeline(current_principal(), project_id, cursor, limit, event_type, application_id)

    @mcp.tool(auth=read_auth, annotations=READ_ONLY)
    async def read_project_memory(project_id: int, request_id: str) -> dict:
        """Read one canonical event by UUID. Use this after an uncertain write before retrying with the same request_id and identical payload."""
        return {"event": await memory.read(current_principal(), project_id, request_id), "data_only": True}

    @mcp.tool(auth=read_auth, annotations=READ_ONLY)
    async def search_project_memory(
        project_id: int,
        query: str,
        limit: int = 10,
        application_id: str | None = None,
    ) -> dict:
        """Search canonical events with a bounded Unicode lexical ranker. No external provider is used; confirmed-superseded events are excluded and timeline remains the complete-history path."""
        return await memory.search(current_principal(), project_id, query, limit, application_id)

    @mcp.tool(auth=write_auth, annotations=WRITE_IDEMPOTENT)
    async def record_project_memory(
        project_id: int,
        request_id: str,
        event_type: str,
        title: str,
        summary: str,
        details: str,
        occurred_at: str,
        source_refs: list[dict],
        shared_safe: bool,
        event_status: str = "observed",
        application_id: str | None = None,
        supersedes_id: str | None = None,
    ) -> dict:
        """Append a non-sensitive source-linked event. Same UUID/hash replays safely, changed payload conflicts, corrections use supersedes_id. shared_safe must be true; memory never grants action authority."""
        return await memory.record(current_principal(), {
            "project_id": project_id,
            "application_id": application_id,
            "request_id": request_id,
            "event_type": event_type,
            "title": title,
            "summary": summary,
            "details": details,
            "occurred_at": occurred_at,
            "source_refs": source_refs,
            "shared_safe": shared_safe,
            "event_status": event_status,
            "supersedes_id": supersedes_id,
        })

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request):
        return JSONResponse({
            "status": "ok",
            "version": __version__,
            "snapshot_id": snapshot.id,
            "documents": len(snapshot.docs),
            "project_memory_enabled": True,
            "semantic_provider": "not-configured",
        })

    mcp._agent_memory_repository = repository
    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Memory Kit MCP server")
    parser.add_argument("--transport", choices=("http", "stdio"), default="http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8775)
    parser.add_argument("--migrate", action="store_true")
    parser.add_argument("--seed", action="store_true")
    args = parser.parse_args()

    content_root = Path(os.environ.get("AGENT_MEMORY_CONTENT_ROOT", ROOT / "content"))
    principals_path = Path(os.environ.get("AGENT_MEMORY_PRINCIPALS_PATH", content_root / "principals.json"))
    database_url = os.environ.get("AGENT_MEMORY_DATABASE_URL")
    stdio_client_id = os.environ.get("AGENT_MEMORY_STDIO_CLIENT_ID", "demo-writer")

    if args.migrate or args.seed:
        if not database_url:
            raise SystemExit("AGENT_MEMORY_DATABASE_URL is required")
        async def prepare() -> None:
            if args.migrate:
                await migrate(database_url)
            if args.seed:
                await seed(database_url, content_root)
        asyncio.run(prepare())
        return

    server = asyncio.run(create_server(
        content_root=content_root,
        principals_path=principals_path,
        database_url=database_url,
        stdio_client_id=stdio_client_id,
    ))
    try:
        if args.transport == "http":
            server.run(
                transport="http",
                host=args.host,
                port=args.port,
                stateless_http=True,
                show_banner=False,
            )
        else:
            server.run(transport="stdio")
    finally:
        asyncio.run(server._agent_memory_repository.close())


if __name__ == "__main__":
    main()
