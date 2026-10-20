"""Call a model gateway for an operation, trying the ready links in order.

Project-agnostic: a project passes the link rows it owns, the link role the
operation needs, and the path on the gateway. A queued answer (202) is relayed
and remembered so the caller can poll it through Studio.
"""

from __future__ import annotations

import logging

import httpx

from app.config import auth_settings
from app.core.errors import ModelOfflineError, NotFoundError
from app.services.routing import LinkRow, candidates, explain

logger = logging.getLogger(__name__)

# job id -> (gateway base url, owner id). In-process on purpose: one uvicorn worker.
_JOBS: dict[str, tuple[str, str]] = {}


def remember_job(job_id: str, base: str, owner_id: str) -> None:
    _JOBS[job_id] = (base.rstrip("/"), owner_id)


def _flow_headers() -> dict[str, str]:
    token = auth_settings.FLOW_TOKEN.strip()
    if not token:
        return {}
    return {"X-Flow-Token": token}


async def read_job(job_id: str, owner_id: str) -> tuple[dict, int]:
    row = _JOBS.get(job_id)
    if row is None or row[1] != owner_id:
        raise NotFoundError("Unknown request")
    base = row[0]
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.get(f"{base}/jobs/{job_id}", headers=_flow_headers())
    except httpx.HTTPError as exc:
        raise ModelOfflineError("The model did not answer.") from exc
    try:
        body = response.json()
    except ValueError:
        body = {"detail": response.text, "status": "error"}
    return body, response.status_code


async def run_operation(
    rows: list[LinkRow],
    role: str,
    path: str,
    payload: dict,
    owner_id: str,
    project: str,
) -> dict:
    checks = explain(rows, role)
    options = candidates(rows, role)
    if not options:
        raise ModelOfflineError("The app is currently not working.", checks)

    attempts = list(checks)
    timeout = auth_settings.MODEL_TIMEOUT_SECONDS
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for choice in options:
            url = choice.url.rstrip("/") + path
            try:
                response = await client.post(url, json=payload, headers=_flow_headers())
            except httpx.HTTPError as exc:
                logger.info("%s: %s link %s failed: %s", project, choice.placement, url, exc)
                attempts.append(
                    {"role": choice.role, "placement": choice.placement, "state": "unreachable"}
                )
                continue
            if response.status_code == 202:
                queued = response.json()
                if queued.get("id"):
                    remember_job(str(queued["id"]), choice.url, owner_id)
                return {
                    "status": "queued",
                    "detail": queued.get("detail") or "Your request is queued.",
                    "lane": queued.get("lane"),
                    "position": queued.get("position"),
                    "job_id": queued.get("id"),
                    "poll": f"/api/studio/v1/{project}/jobs/{queued.get('id')}",
                    "lanes": queued.get("lanes"),
                    "provider": {"role": choice.role, "placement": choice.placement},
                }
            if response.status_code >= 400:
                attempts.append(
                    {
                        "role": choice.role,
                        "placement": choice.placement,
                        "state": "error",
                        "status": response.status_code,
                    }
                )
                continue
            try:
                body = response.json()
            except ValueError:
                body = {"text": response.text}
            if not isinstance(body, dict):
                body = {"text": body}
            return {
                "text": body.get("text") or body.get("polished") or body.get("content") or "",
                "raw": body,
                "provider": {"role": choice.role, "placement": choice.placement},
            }

    raise ModelOfflineError("The app is currently not working.", attempts)
