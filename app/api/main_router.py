from fastapi import APIRouter

from app.api.routes.api_key import api_key_routes
from app.api.routes.downloads import downloads_routes
from app.api.routes.gmaps import gmaps_public_routes, gmaps_routes
from app.api.routes.health import health_routes
from app.api.routes.projects import project_routes
from app.api.routes.services import service_routes
from app.api.routes.llm import llm_public_routes, llm_routes
from app.api.routes.whisperflow import whisperflow_public_routes, whisperflow_routes

main_router = APIRouter(prefix="/api/studio")

# Public
main_router.include_router(health_routes.router)
main_router.include_router(project_routes.router)
main_router.include_router(downloads_routes.router)

# Signed in (SSO cookie + CSRF)
main_router.include_router(service_routes.router)
main_router.include_router(api_key_routes.router)
main_router.include_router(whisperflow_routes.router)
main_router.include_router(llm_routes.router)
main_router.include_router(gmaps_routes.router)

# API key (Authorization: Bearer sk-studio_...), one router per project under /v1
main_router.include_router(whisperflow_public_routes.router)
main_router.include_router(llm_public_routes.router)
main_router.include_router(gmaps_public_routes.router)

# Release upload (token)
main_router.include_router(downloads_routes.admin_router)
