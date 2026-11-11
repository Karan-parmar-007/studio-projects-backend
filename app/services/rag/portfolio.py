"""Karan's public portfolio + the Studio catalog as knowledge blocks for the chatbot.

Read from the portfolio API (public endpoints only) at sync time and indexed with
Karan's PDF into one chatbot corpus, so "what does Karan know about X" needs one
model call. Phone numbers are deliberately left out.
"""

from __future__ import annotations

import html
import logging
import re

import httpx

from app.config import rag_settings
from app.services.rag.parse import Block

logger = logging.getLogger(__name__)

_TAG = re.compile(r"<[^>]+>")


def strip_html(value: str) -> str:
    text = re.sub(r"<\s*(br|/p|/li|/h[1-6])\s*/?>", "\n", value or "", flags=re.IGNORECASE)
    text = re.sub(r"<\s*li[^>]*>", "- ", text, flags=re.IGNORECASE)
    text = html.unescape(_TAG.sub("", text))
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _join(*parts: object, sep: str = ", ") -> str:
    return sep.join(str(p).strip() for p in parts if p not in (None, "") and str(p).strip())


def _period(start: object, end: object, current: bool = False) -> str:
    end_text = "present" if current else (str(end) if end else "")
    return _join(start, end_text, sep=" to ") if start or end_text else ""


async def fetch(base: str | None = None, timeout: float = 20) -> dict:
    base = (base or rag_settings.PORTFOLIO_API_URL).rstrip("/")
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.get(f"{base}/portfolio/overview")
        response.raise_for_status()
        overview = response.json()
        details = []
        for project in overview.get("projects") or overview.get("featured_projects") or []:
            slug = project.get("slug")
            if not slug:
                continue
            try:
                detail = await client.get(f"{base}/portfolio/projects/{slug}")
                if detail.status_code == 200:
                    details.append(detail.json())
            except httpx.HTTPError:
                logger.info("portfolio project %s unavailable", slug)
        overview["project_details"] = details
    return overview


def blocks_from(overview: dict, name_hint: str = "Karan") -> list[Block]:
    blocks: list[Block] = []
    profile = overview.get("profile") or {}
    name = profile.get("name") or name_hint

    about = [
        f"{name} — {profile.get('headline')}" if profile.get("headline") else name,
        strip_html(profile.get("about") or ""),
        _join(
            f"Location: {profile['location']}" if profile.get("location") else "",
            "Available for work" if profile.get("available_for_work") else "",
            f"Email: {profile['email']}" if profile.get("email") else "",
            sep=". ",
        ),
    ]
    blocks.append(Block(kind="text", text="\n".join(p for p in about if p), headings=[f"About {name}"]))

    for job in overview.get("work_experience") or []:
        lines = [
            _join(job.get("position"), job.get("company"), sep=" at "),
            _join(job.get("employment_type"), job.get("location"), _period(job.get("start_date"), job.get("end_date"), bool(job.get("is_current")))),
        ]
        for item in job.get("description") or []:
            lines.append(f"- {strip_html(str(item))}")
        blocks.append(
            Block(kind="text", text="\n".join(l for l in lines if l), headings=["Work experience", str(job.get("company") or "")])
        )

    for school in overview.get("education") or []:
        lines = [
            _join(school.get("degree"), school.get("field_of_study"), sep=" in "),
            _join(school.get("school"), _period(school.get("start_year"), school.get("end_year"))),
            _join(school.get("score_label"), school.get("score"), sep=": ") if school.get("score") else "",
            strip_html(school.get("description") or ""),
        ]
        blocks.append(Block(kind="text", text="\n".join(l for l in lines if l), headings=["Education"]))

    for category in overview.get("skill_categories") or []:
        skills = [s.get("name") if isinstance(s, dict) else str(s) for s in category.get("skills") or []]
        if skills:
            blocks.append(
                Block(
                    kind="text",
                    text=f"{name}'s {category.get('name') or 'skills'}: " + ", ".join(s for s in skills if s),
                    headings=["Skills", str(category.get("name") or "")],
                )
            )

    seen: set[str] = set()
    for project in overview.get("project_details") or []:
        slug = str(project.get("slug") or "")
        seen.add(slug)
        lines = [
            _join(project.get("name"), project.get("short_description"), sep=": "),
            f"Status: {project.get('status')}" if project.get("status") else "",
            "Tech: " + ", ".join(project.get("tech_stack") or []) if project.get("tech_stack") else "",
            strip_html(project.get("long_description") or ""),
            _join(
                f"Code: {project['github_url']}" if project.get("github_url") else "",
                f"Try it: {project['try_url']}" if project.get("try_url") else "",
                sep=". ",
            ),
        ]
        blocks.append(
            Block(kind="text", text="\n".join(l for l in lines if l), headings=["Projects", str(project.get("name") or slug)])
        )
    for project in overview.get("projects") or []:
        if str(project.get("slug") or "") in seen:
            continue
        lines = [_join(project.get("name"), project.get("short_description"), sep=": ")]
        if project.get("tech_stack"):
            lines.append("Tech: " + ", ".join(project.get("tech_stack") or []))
        blocks.append(Block(kind="text", text="\n".join(lines), headings=["Projects", str(project.get("name") or "")]))

    links = [
        f"{item.get('label') or item.get('platform')}: {item.get('url')}"
        for item in overview.get("social_media") or []
        if item.get("url")
    ]
    if links:
        blocks.append(Block(kind="text", text=f"Where to find {name} online:\n" + "\n".join(links), headings=["Links"]))
    return blocks


def studio_blocks() -> list[Block]:
    """What Studio offers, from the same catalog the website lists."""
    from app.api.routes.projects.project_catalog import PROJECTS

    blocks = []
    for item in PROJECTS:
        lines = [
            f"{item['name']} ({item['status']}): {item['tagline']}",
            item.get("description") or "",
            f"Page: https://studio.karanparmar.in{item['page']}",
        ]
        if item.get("apps"):
            lines.append("Apps: " + ", ".join(item["apps"]))
        blocks.append(Block(kind="text", text="\n".join(l for l in lines if l), headings=["Studio products", item["name"]]))
    return blocks
