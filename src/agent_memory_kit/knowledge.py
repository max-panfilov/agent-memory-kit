"""Synthetic project registry and immutable, hash-checked knowledge snapshot."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from .errors import PublicError

TASK_KINDS = {"development", "review", "diagnosis", "release", "content"}


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class Snapshot:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.registry = self.json("projects.json")
        self._validate(self.registry, "projects.schema.json")
        self.bindings = self.registry["projects"]
        catalog = self.json("documents.json")
        self._validate(catalog, "documents.schema.json")
        self.docs = {}
        for document in catalog["documents"]:
            content = self.safe_read(document["path"])
            if digest(content.encode("utf-8")) != document["sha256"]:
                raise PublicError("INVALID_DOCUMENT_HASH")
            if document["status"] == "approved":
                approval = document.get("approval") or {}
                if approval.get("content_sha256") != document["sha256"]:
                    raise PublicError("INVALID_APPROVAL")
            self.docs[document["id"]] = {**document, "content": content}
        for project in self.bindings:
            for application in project["applications"]:
                for key in ("required_documents", "reference_documents"):
                    for document_id in application.get(key, []):
                        if document_id not in self.docs:
                            raise PublicError("INVALID_CONTEXT_DOCUMENT")
        identity = json.dumps(
            {
                "documents": {key: value["sha256"] for key, value in self.docs.items()},
                "registry": self.registry,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.id = digest(identity)
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.execute('CREATE VIRTUAL TABLE search USING fts5(id UNINDEXED, title, content, tokenize="unicode61")')
        for document in self.docs.values():
            self.db.execute("INSERT INTO search VALUES (?, ?, ?)", (document["id"], document["title"], document["content"]))
        self.db.commit()

    def _validate(self, value: dict, schema_name: str) -> None:
        schema_path = self.root.parent / "schemas" / schema_name
        if schema_path.is_symlink() or not schema_path.is_file():
            raise PublicError("INVALID_SCHEMA")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)

    def safe_read(self, path: str) -> str:
        candidate = self.root / path
        resolved = candidate.resolve()
        if candidate.is_symlink() or not resolved.is_relative_to(self.root) or not resolved.is_file():
            raise PublicError("REFUSED_SOURCE")
        if resolved.stat().st_size > 512_000:
            raise PublicError("SOURCE_TOO_LARGE")
        return resolved.read_text(encoding="utf-8")

    def json(self, path: str):
        return json.loads(self.safe_read(path))

    def resolve_project(self, project: str | int) -> dict:
        value = str(project).strip().casefold()
        matches = []
        for binding in self.bindings:
            identifiers = {str(binding["id"]).casefold(), binding["name"].casefold(), binding["repository"].casefold()}
            identifiers.update(alias.casefold() for alias in binding.get("aliases", []))
            if value in identifiers:
                matches.append(binding)
        if not matches:
            raise PublicError("PROJECT_NOT_AVAILABLE")
        if len(matches) > 1:
            raise PublicError("AMBIGUOUS_PROJECT")
        return matches[0]

    def allowed_applications(
        self, principal, project: str | int, application_id: str | None, write: bool = False
    ) -> list[str]:
        binding = self.resolve_project(project)
        project_id = binding["id"]
        if project_id not in principal.projects:
            raise PublicError("PROJECT_NOT_AVAILABLE")
        required_scope = "memory:write" if write else "memory:read"
        if required_scope not in principal.scopes:
            raise PublicError("MEMORY_WRITE_FORBIDDEN" if write else "MEMORY_READ_FORBIDDEN")
        available = {application["id"] for application in binding["applications"]}
        permitted = set(principal.applications.get(project_id, set()))
        if application_id is not None:
            if application_id not in available or application_id not in permitted:
                raise PublicError("APPLICATION_NOT_AVAILABLE")
            return [application_id]
        return sorted(available & permitted)

    def application_allowed(self, principal, project: str | int, application_id: str) -> bool:
        binding = self.resolve_project(project)
        return application_id in principal.applications.get(binding["id"], set())

    def project_card(self, project: str | int) -> dict:
        binding = self.resolve_project(project)
        return {
            "id": binding["id"],
            "name": binding["name"],
            "repository": binding["repository"],
            "applications": [application["id"] for application in binding["applications"]],
        }

    def ref(self, document: dict) -> dict:
        result = {
            key: document.get(key)
            for key in ("id", "title", "version", "kind", "status", "sha256", "path", "review_due")
        }
        result["source_repository"] = document.get("source_repository", "example-org/agent-memory-kit")
        result["repository_path"] = document["path"]
        return result

    def require_snapshot(self, snapshot_id: str | None) -> None:
        if snapshot_id and snapshot_id != self.id:
            raise PublicError("CONTENT_VERSION_GONE")

    def document(self, document_id: str, snapshot_id: str | None = None, cursor: int = 0, limit: int = 8000) -> dict:
        self.require_snapshot(snapshot_id)
        document = self.docs.get(document_id)
        if not document:
            raise PublicError("DOCUMENT_NOT_AVAILABLE")
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0 or not 1 <= limit <= 12000 or cursor > len(document["content"]):
            raise PublicError("INVALID_PAGINATION")
        content = document["content"][cursor:cursor + limit]
        return {
            **self.ref(document),
            "snapshot_id": self.id,
            "content": content,
            "cursor_unit": "unicode_characters",
            "next_cursor": cursor + len(content) if cursor + len(content) < len(document["content"]) else None,
            "source_refs": document.get("source_refs", []),
            "data_only": document["kind"] in {"case", "business-context", "research", "infrastructure"},
        }

    def search(
        self,
        query: str,
        profile: str | None = None,
        kind: str | None = None,
        cursor: int = 0,
        limit: int = 10,
        snapshot_id: str | None = None,
    ) -> dict:
        self.require_snapshot(snapshot_id)
        if not isinstance(query, str) or len(query) > 500 or not isinstance(cursor, int) or cursor < 0 or not 1 <= limit <= 20:
            raise PublicError("INVALID_SEARCH")
        words = re.findall(r"[^\W_]+", query, re.UNICODE)[:12]
        if not words:
            raise PublicError("INVALID_QUERY")
        expression = " OR ".join('"' + word.replace('"', "") + '"*' for word in words)
        rows = self.db.execute(
            'SELECT id, snippet(search, 2, "", "", " … ", 32) FROM search WHERE search MATCH ? ORDER BY bm25(search), id',
            (expression,),
        ).fetchall()
        allowed = [
            (self.docs[document_id], excerpt)
            for document_id, excerpt in rows
            if (not profile or profile in self.docs[document_id].get("profiles", []))
            and (not kind or kind == self.docs[document_id]["kind"])
        ]
        selected = allowed[cursor:cursor + limit]
        return {
            "snapshot_id": self.id,
            "results": [{**self.ref(document), "excerpt": excerpt[:1500]} for document, excerpt in selected],
            "next_cursor": cursor + limit if cursor + limit < len(allowed) else None,
            "drafts_are_reference_only": True,
        }

    def context(
        self,
        project: str,
        application_id: str | None,
        repo_sha: str | None,
        task_kind: str,
        task_summary: str,
        principal,
    ) -> dict:
        if len(task_summary) > 2000:
            raise PublicError("INVALID_TASK_SUMMARY")
        if task_kind not in TASK_KINDS:
            raise PublicError("INVALID_TASK_KIND")
        if repo_sha and not re.fullmatch(r"[a-f0-9]{40}", repo_sha):
            raise PublicError("INVALID_REPO_SHA")
        binding = self.resolve_project(project)
        applications = self.allowed_applications(principal, binding["id"], application_id)
        if len(applications) != 1:
            raise PublicError("AMBIGUOUS_APPLICATION")
        application = next(item for item in binding["applications"] if item["id"] == applications[0])
        required = [self.ref(self.docs[key]) for key in application.get("required_documents", [])]
        reference = [self.ref(self.docs[key]) for key in application.get("reference_documents", [])]
        return {
            "schema_version": "0.1.0",
            "status": "requires_review" if reference else "ok",
            "snapshot_id": self.id,
            "project": binding["name"],
            "project_id": binding["id"],
            "application_id": application["id"],
            "repository": binding["repository"],
            "repo_sha": repo_sha,
            "task_kind": task_kind,
            "required_documents": required,
            "reference_documents": reference,
            "warnings": [
                "Draft documents are reference-only, not approved rules.",
                "This context is dated source data and does not grant execution or deployment permission.",
            ],
            "grants_permissions": False,
            "instructions": "Read required documents and relevant references with read_document in this snapshot.",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
