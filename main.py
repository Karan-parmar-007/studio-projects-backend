import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from karan_sso.fastapi import CSRFMiddleware

from app.api.main_router import main_router
from app.auth.sso import configure_sso, sso_settings
from app.config import auth_settings, cors_settings
from app.core.errors import register_exception_handlers
from app.db.garage import GarageSession
from app.db.session import PostgresSession

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    postgres = PostgresSession()
    app.state.postgres_session = postgres
    # Garage clients are created per request; nothing to verify at startup.
    app.state.garage_session = GarageSession()
    try:
        await postgres.verify_connection()
        logger.info("Studio database is reachable")
    except Exception:
        logger.exception("Studio database is not reachable")
    yield
    await postgres.dispose()


configure_sso()

_docs = not auth_settings.is_production
app = FastAPI(
    title="Studio",
    lifespan=lifespan,
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)
register_exception_handlers(app)
app.add_middleware(
    CSRFMiddleware,
    exempt_prefixes=(
        "/health",
        "/api/studio/health",
        "/api/studio/projects",
        "/api/studio/admin/downloads",
        "/api/studio/v1",
    ),
    settings=sso_settings,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-CSRF-Token",
        "X-Filename",
        "X-Version",
        "X-Release-Token",
    ],
)
app.include_router(main_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
