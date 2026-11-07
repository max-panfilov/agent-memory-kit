"""Canonical event validation and deterministic project-memory views."""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .errors import PublicError

if TYPE_CHECKING:
    from .auth import Principal
    from .knowledge import Snapshot
    from .repository import Repository

EVENT_TYPES = {
    "milestone", "decision", "feature", "communication",
    "release", "incident", "handoff", "correction",
}
EVENT_STATES = {"proposed", "observed", "confirmed"}
SOURCE_KINDS = {"git", "plane", "directus_note", "user_instruction", "ci", "release", "document"}
MAX_SCAN = 1000
SECRET = re.compile(
    r"-----BEGIN .*PRIVATE KEY|\bsk-[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._-]{16,}|"
    r"(?:password|api[_-]?key|token)\s*[:=]\s*[\"']?[A-Za-z0-9_+/-]{12,}",
    re.IGNORECASE,
)


def canonical_hash(event: dict) -> str:
    payload = {k: v for k, v in event.items() if k not in {"recorded_at", "shared_safe", "payload_hash"}}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _tokens(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold(), re.UNICODE)


def _lexical_scores(rows: list[dict], query: str) -> dict[str, float]:
    terms = set(_tokens(query))
    if not terms or not rows:
        return {}
    documents = []
    document_frequency = Counter()
    for row in rows:
        counts = Counter()
        length = 0
        for field, weight in (
            ("title", 3), ("summary", 2), ("details", 1), ("application_id", 1),
            ("event_type", 1), ("event_status", 1), ("occurred_at", 1), ("recorded_at", 1),
        ):
            field_counts = Counter(_tokens(str(row.get(field) or "")))
            length += sum(field_counts.values()) * weight
            for token in terms & field_counts.keys():
                counts[token] += field_counts[token] * weight
        documents.append((counts, length))
        document_frequency.update(counts.keys())
    if len(rows) >= 10:
        terms = {term for term in terms if document_frequency[term] <= len(rows) * 0.8}
    average = sum(length for _, length in documents) / len(rows) or 1
    scores = {}
    for row, (counts, length) in zip(rows, documents):
        score = 0.0
        for term in sorted(terms & counts.keys()):
            frequency = counts[term]
            inverse_frequency = math.log(
                1 + (len(rows) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5)
            )
            score += inverse_frequency * (frequency * 2.2) / (
                frequency + 1.2 * (0.25 + 0.75 * length / average)
            )
        if score:
            scores[row["id"]] = score
    return scores


def validate_event(
    project: int,
    application_id: str,
    request_id: str,
    event_type: str,
    title: str,
    summary: str,
    details: str,
    occurred_at: str,
    source_refs: list[dict],
    event_status: str = "observed",
    supersedes_id: str | None = None,
) -> dict:
    try:
        event_id = str(uuid.UUID(request_id))
    except (ValueError, TypeError, AttributeError):
        raise PublicError("INVALID_MEMORY_REQUEST_ID") from None
    if not isinstance(project, int) or isinstance(project, bool) or project < 1:
        raise PublicError("INVALID_PROJECT")
    if not isinstance(application_id, str) or not re.fullmatch(r"[a-zA-Z0-9_.:/-]{1,120}", application_id):
        raise PublicError("INVALID_APPLICATION")
    if event_type not in EVENT_TYPES or event_status not in EVENT_STATES:
        raise PublicError("INVALID_MEMORY_TYPE_OR_STATUS")
    for text, maximum in ((title, 160), (summary, 1000), (details, 8000)):
        if not isinstance(text, str) or not text.strip() or len(text) > maximum:
            raise PublicError("INVALID_MEMORY_TEXT")
        if SECRET.search(text):
            raise PublicError("POSSIBLE_SECRET_REFUSED")
    try:
        date = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError()
    except (ValueError, AttributeError):
        raise PublicError("MEMORY_TIMESTAMP_REQUIRES_TIMEZONE") from None
    if not isinstance(source_refs, list) or not 1 <= len(source_refs) <= 8:
        raise PublicError("MEMORY_SOURCE_REQUIRED")
    normalized_sources = []
    for source in source_refs:
        if not isinstance(source, dict) or set(source) != {"kind", "ref"} or source["kind"] not in SOURCE_KINDS:
            raise PublicError("INVALID_MEMORY_SOURCE")
        ref = source["ref"]
        if not isinstance(ref, str) or not ref.strip() or len(ref) > 500 or SECRET.search(ref):
            raise PublicError("INVALID_MEMORY_SOURCE")
        normalized_sources.append({"kind": source["kind"], "ref": ref.strip()})
    if supersedes_id:
        try:
            supersedes_id = str(uuid.UUID(supersedes_id))
        except (ValueError, TypeError):
            raise PublicError("INVALID_SUPERSEDES_ID") from None
        if supersedes_id == event_id:
            raise PublicError("MEMORY_CANNOT_SUPERSEDE_SELF")
    event = {
        "id": event_id,
        "project": project,
        "application_id": application_id,
        "event_type": event_type,
        "title": title.strip(),
        "summary": summary.strip(),
        "details": details.strip(),
        "event_status": event_status,
        "occurred_at": date.astimezone(timezone.utc).isoformat(),
        "source_refs": normalized_sources,
        "supersedes_id": supersedes_id or None,
    }
    event["payload_hash"] = canonical_hash(event)
    return event


class MemoryService:
    def __init__(self, repository: Repository, snapshot: Snapshot):
        self.repository = repository
        self.snapshot = snapshot

    def _applications(self, principal: Principal, project: int, application_id: str | None, write: bool = False) -> list[str]:
        return self.snapshot.allowed_applications(principal, project, application_id, write=write)

    async def record(self, principal: Principal, raw: dict) -> dict:
        if raw.get("shared_safe") is not True:
            raise PublicError("SHARED_MEMORY_REQUIRES_NON_SENSITIVE_CONTENT")
        project = raw.get("project_id")
        applications = self._applications(principal, project, raw.get("application_id"), write=True)
        if len(applications) != 1:
            raise PublicError("AMBIGUOUS_APPLICATION")
        event = validate_event(
            project=project,
            application_id=applications[0],
            request_id=raw.get("request_id"),
            event_type=raw.get("event_type"),
            title=raw.get("title"),
            summary=raw.get("summary"),
            details=raw.get("details"),
            occurred_at=raw.get("occurred_at"),
            source_refs=raw.get("source_refs"),
            event_status=raw.get("event_status", "observed"),
            supersedes_id=raw.get("supersedes_id"),
        )
        result = await self.repository.append(event, principal.client_id)
        result.update({"indexing": "not-configured", "data_only": True})
        return result

    async def read(self, principal: Principal, project_id: int, request_id: str) -> dict | None:
        self._applications(principal, project_id, None)
        try:
            event_id = str(uuid.UUID(request_id))
        except (ValueError, TypeError, AttributeError):
            raise PublicError("INVALID_MEMORY_REQUEST_ID") from None
        row = await self.repository.read(project_id, event_id)
        if row and not self.snapshot.application_allowed(principal, project_id, row["application_id"]):
            raise PublicError("MEMORY_NOT_AVAILABLE")
        return row

    async def timeline(
        self,
        principal: Principal,
        project_id: int,
        cursor: int = 0,
        limit: int = 20,
        event_type: str | None = None,
        application_id: str | None = None,
    ) -> dict:
        applications = self._applications(principal, project_id, application_id)
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0 or not 1 <= limit <= 50:
            raise PublicError("INVALID_PAGINATION")
        if event_type and event_type not in EVENT_TYPES:
            raise PublicError("INVALID_MEMORY_TYPE_OR_STATUS")
        rows = await self.repository.list(
            project_id, applications, offset=cursor, limit=limit + 1, event_type=event_type
        )
        return {
            "project_id": project_id,
            "application_id": application_id,
            "events": rows[:limit],
            "next_cursor": cursor + limit if len(rows) > limit else None,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "data_only": True,
            "warnings": [
                "Recorded status is source-reported, not independently verified. "
                "Timeline includes superseded events; records never authorize execution."
            ],
        }

    async def _scan(self, principal: Principal, project_id: int, application_id: str | None) -> tuple[list[dict], bool]:
        applications = self._applications(principal, project_id, application_id)
        rows = await self.repository.list(project_id, applications, offset=0, limit=MAX_SCAN + 1)
        return rows[:MAX_SCAN], len(rows) > MAX_SCAN

    async def brief(self, principal: Principal, project_id: int, application_id: str | None = None) -> dict:
        card = self.snapshot.project_card(project_id)
        rows, truncated = await self._scan(principal, project_id, application_id)
        replaced = {
            row["supersedes_id"] for row in rows
            if row["supersedes_id"] and row["event_status"] == "confirmed"
        }
        active = [row for row in rows if row["id"] not in replaced]
        return {
            "project": card,
            "application_id": application_id,
            "recent_events": active[:12],
            "decisions": [row for row in active if row["event_type"] == "decision"][:15],
            "handoffs": [row for row in active if row["event_type"] == "handoff"][:3],
            "scanned_events": len(rows),
            "history_truncated": truncated,
            "data_only": True,
            "warnings": [
                "Brief is a deterministic view of recorded events, not a newly verified project audit. "
                "Proposed/observed decisions are not accepted policy. Read event details/sources before acting."
            ],
        }

    async def search(
        self, principal: Principal, project_id: int, query: str, limit: int = 10, application_id: str | None = None
    ) -> dict:
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 1000 or not 1 <= limit <= 20:
            raise PublicError("INVALID_MEMORY_QUERY")
        if SECRET.search(query):
            raise PublicError("POSSIBLE_SECRET_REFUSED")
        rows, truncated = await self._scan(principal, project_id, application_id)
        replaced = {
            row["supersedes_id"] for row in rows
            if row["supersedes_id"] and row["event_status"] == "confirmed"
        }
        active = [row for row in rows if row["id"] not in replaced]
        scores = _lexical_scores(active, query)
        by_id = {row["id"]: row for row in active}
        ordered = sorted(scores, key=lambda event_id: (-scores[event_id], event_id))[:limit]
        return {
            "project_id": project_id,
            "application_id": application_id,
            "results": [{"event": by_id[event_id], "score": scores[event_id]} for event_id in ordered],
            "ranking": "canonical-bm25-v1",
            "history_truncated": truncated,
            "scanned_events": len(rows),
            "data_only": True,
            "warnings": [
                "Scores are lexical ranking values, not semantic similarity. "
                "Search may omit events; use timeline for complete history. Only canonical events are returned."
            ],
        }
