from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class DomainError(Exception):
    def __init__(self, message: str = "Domain error") -> None:
        self.message = message
        super().__init__(message)


class NotFoundError(DomainError):
    pass


class ConflictError(DomainError):
    pass


class ValidationError(DomainError):
    pass


class StorageError(DomainError):
    pass


class ModelOfflineError(DomainError):
    def __init__(self, message: str, checks: list[dict] | None = None) -> None:
        self.checks = checks or []
        super().__init__(message)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def not_found_handler(_request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": exc.message})

    @app.exception_handler(ConflictError)
    async def conflict_handler(_request: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": exc.message})

    @app.exception_handler(ValidationError)
    async def validation_handler(_request: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": exc.message})

    @app.exception_handler(ModelOfflineError)
    async def offline_handler(_request: Request, exc: ModelOfflineError) -> JSONResponse:
        checks = []
        for item in exc.checks:
            if isinstance(item, dict):
                checks.append({key: value for key, value in item.items() if key != "url"})
            else:
                checks.append(item)
        return JSONResponse(
            status_code=503,
            content={"detail": exc.message, "code": "model_offline", "checks": checks},
        )

    @app.exception_handler(StorageError)
    async def storage_handler(_request: Request, exc: StorageError) -> JSONResponse:
        logger.error("Storage error: %s", exc.message)
        return JSONResponse(status_code=503, content={"detail": exc.message})

    @app.exception_handler(Exception)
    async def unhandled_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})
