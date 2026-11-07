"""Hashed bearer-token verification and per-tool principal selection."""
from __future__ import annotations

import hashlib
import hmac
import json
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from fastmcp.server.auth import AccessToken, AuthContext, TokenVerifier
from jsonschema import Draft202012Validator, FormatChecker

from .errors import PublicError


@dataclass(frozen=True)
class Principal:
    client_id: str
    token_sha256: str
    scopes: frozenset[str]
    projects: frozenset[int]
    applications: dict[int, frozenset[str]]


class PrincipalRegistry:
    def __init__(self, principals: list[Principal], stdio_client_id: str):
        self.by_hash = {principal.token_sha256: principal for principal in principals}
        self.by_client = {principal.client_id: principal for principal in principals}
        if len(self.by_hash) != len(principals) or len(self.by_client) != len(principals):
            raise PublicError("INVALID_PRINCIPALS")
        try:
            self.stdio = self.by_client[stdio_client_id]
        except KeyError:
            raise PublicError("INVALID_STDIO_PRINCIPAL") from None


class MemoryTokenVerifier(TokenVerifier):
    def __init__(self, registry: PrincipalRegistry):
        super().__init__(required_scopes=["memory:read"])
        self.registry = registry

    async def verify_token(self, token: str) -> AccessToken | None:
        observed = hashlib.sha256(token.encode("utf-8")).hexdigest()
        for expected, principal in self.registry.by_hash.items():
            if hmac.compare_digest(observed, expected):
                return AccessToken(
                    token=token,
                    client_id=principal.client_id,
                    scopes=sorted(principal.scopes),
                )
        return None


def load_principals(root: Path, path: Path, stdio_client_id: str) -> PrincipalRegistry:
    schema = json.loads((root / "schemas" / "principals.schema.json").read_text(encoding="utf-8"))
    value = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    principals = []
    for item in value["principals"]:
        applications = {
            int(project): frozenset(applications)
            for project, applications in item["applications"].items()
        }
        projects = frozenset(int(project) for project in item["projects"])
        if projects != frozenset(applications):
            raise PublicError("INVALID_PRINCIPAL_SCOPE")
        principals.append(
            Principal(
                client_id=item["client_id"],
                token_sha256=item["token_sha256"],
                scopes=frozenset(item["scopes"]),
                projects=projects,
                applications=applications,
            )
        )
    return PrincipalRegistry(principals, stdio_client_id)


_current: ContextVar[Principal | None] = ContextVar("agent_memory_principal", default=None)


def authorize(context: AuthContext, registry: PrincipalRegistry, write: bool = False) -> bool:
    token = context.token
    if token is None:
        principal = registry.stdio
        scopes = principal.scopes
    else:
        principal = registry.by_client.get(token.client_id)
        if principal is None:
            return False
        scopes = set(token.scopes)
    required = "memory:write" if write else "memory:read"
    if required not in scopes or required not in principal.scopes:
        return False
    _current.set(principal)
    return True


def current_principal() -> Principal:
    principal = _current.get()
    if principal is None:
        raise PublicError("MEMORY_PRINCIPAL_REQUIRED")
    return principal
