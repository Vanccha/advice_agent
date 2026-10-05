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
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(code, message, status_code=404, details=details)


class Conflict(ApiError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(code, message, status_code=409, details=details)


class Unauthorized(ApiError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(code, message, status_code=401, details=details)


class Forbidden(ApiError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(code, message, status_code=403, details=details)


class UpstreamUnavailable(ApiError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(code, message, status_code=503, details=details)


def install_error_handlers(app: FastAPI) -> None:
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
