from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from fastapi import Depends, Request

from shared.errors import Forbidden, Unauthorized

API_KEY_HEADER = "X-API-Key"


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def verify_webhook_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def sign_webhook(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Principal:
    """Authenticated API client."""

    name: str
    scopes: frozenset[str]

    def has_scope(self, scope: str) -> bool:
        if "*" in self.scopes or scope in self.scopes:
            return True
        resource = scope.split(":", 1)[0]
        return f"{resource}:*" in self.scopes


class ApiKeyRegistry:
    """In-memory key registry. Services seed it from env or the database."""

    def __init__(self) -> None:
        self._by_hash: dict[str, Principal] = {}

    def register(self, raw_key: str, name: str, scopes: frozenset[str] | set[str]) -> None:
        if not raw_key:
            return
        self._by_hash[hash_api_key(raw_key)] = Principal(name=name, scopes=frozenset(scopes))

    def resolve(self, raw_key: str) -> Principal | None:
        return self._by_hash.get(hash_api_key(raw_key))

    def clear(self) -> None:
        self._by_hash.clear()


def principal_dependency(registry: ApiKeyRegistry):
    """FastAPI dependency returning the authenticated principal."""

    def _dependency(request: Request) -> Principal:
        raw_key = request.headers.get(API_KEY_HEADER)
        if not raw_key:
            raise Unauthorized("MISSING_API_KEY", "X-API-Key header is required.")
        principal = registry.resolve(raw_key)
        if principal is None:
            raise Unauthorized("INVALID_API_KEY", "The provided API key is not recognised.")
        request.state.principal = principal
        return principal

    return _dependency


def require_scope(scope: str, registry: ApiKeyRegistry):
    """FastAPI dependency enforcing a single scope."""
    base = principal_dependency(registry)

    def _dependency(principal: Principal = Depends(base)) -> Principal:
        if not principal.has_scope(scope):
            raise Forbidden(
                "SCOPE_DENIED",
                f"Service account '{principal.name}' lacks the '{scope}' scope.",
                required_scope=scope,
                account=principal.name,
            )
        return principal

    return _dependency
