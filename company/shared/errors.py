"""The company's one error envelope: `{"error": {"code", "message", "details"}}`.

Where this sits: raised by routers and services anywhere; `install_error_handlers`
(called by `create_app`) turns the exceptions into JSON responses.

Key ideas:
- Machine-readable `code` plus human `message`: clients branch on the code
  (`SCOPE_DENIED`, `ILLEGAL_TRANSITION`, ...) and never parse prose.
- Business errors are exceptions mapped to HTTP statuses in one place, so
  route code reads as the happy path.
- Even framework validation errors (422) are rewrapped in the same envelope.

Read next: `auth.py`.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """Business error rendered with the shared error envelope."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def as_payload(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class NotFound(ApiError):
    """404: the requested thing does not exist."""
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(code, message, status_code=404, details=details)


class Conflict(ApiError):
    """409: the request clashes with current state."""
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(code, message, status_code=409, details=details)


class Unauthorized(ApiError):
    """401: missing or unknown credentials."""
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(code, message, status_code=401, details=details)


class Forbidden(ApiError):
    """403: known credentials without the needed permission."""
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(code, message, status_code=403, details=details)


class UpstreamUnavailable(ApiError):
    """503: a dependency could not be reached."""
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(code, message, status_code=503, details=details)


def install_error_handlers(app: FastAPI) -> None:
    """Render `ApiError` and request-validation errors with the shared envelope."""
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.as_payload())

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request body or query parameters are invalid.",
                    "details": {"errors": exc.errors()},
                }
            },
        )
