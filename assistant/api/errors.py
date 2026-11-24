"""Error-body helpers and the exception handler that renders them.

Where this sits: HTTP layer. Routes and `api/security.py` raise `api_error(...)`;
`api.main.create_app()` registers `http_exception_handler` once.

Key ideas:
- One error shape for the whole API: ``{"error": {"code": "...", "message": "..."}}``
  (docs/contracts.md section 4.2). Clients branch on the stable `code`, not the prose.
- FastAPI wraps `HTTPException.detail` in ``{"detail": ...}`` by default; the handler below
  unwraps it so the body is exactly the contract shape.

Read next: `api/security.py` (the main source of 401/403/404 errors).
"""
from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


def api_error(status_code: int, code: str, message: str) -> HTTPException:
    """Build (not raise) an `HTTPException` whose detail is the contract error body.

    Use as ``raise api_error(404, "APPROVAL_NOT_FOUND", "unknown approval")``.
    """
    return HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}})


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Return `exc.detail` as the response body when it is already an error body.

    A bare ``HTTPException("text")`` (e.g. raised by the framework for a 405) is wrapped
    into the same shape so clients never see two formats.
    """
    body = exc.detail if isinstance(exc.detail, dict) else {"error": {"code": "ERROR", "message": str(exc.detail)}}
    return JSONResponse(status_code=exc.status_code, content=body)
