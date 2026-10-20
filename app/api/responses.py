"""Small HTTP helpers shared by the signed-in and API-key routers."""

from __future__ import annotations

from fastapi.responses import JSONResponse


def present(result: dict):
    """A queued model call is a 202; a finished one is a plain 200 body."""
    if result.get("status") == "queued":
        return JSONResponse(status_code=202, content=result)
    return result


def job_response(body: dict, status: int) -> JSONResponse:
    if status not in (200, 202, 502):
        status = 502
    return JSONResponse(status_code=status, content=body)
