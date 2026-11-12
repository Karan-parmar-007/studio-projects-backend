"""The assistant's model links: one shared pair for the chatbot and Ask a Document.

"local" = Karan's PC gateway (through its tunnel), "remote" = the LLM server. Same table and
editor as WhisperFlow's links, owned by Studio instead of one user.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.model_links import model_links_service
from app.api.routes.model_links.model import ProviderLink
from app.services import llm_client


async def list_links(session: AsyncSession) -> list[ProviderLink]:
    return await model_links_service.ensure_links(
        session, llm_client.LINK_OWNER, llm_client.LINK_PROJECT, llm_client.LINK_SLOTS, llm_client.default_links()
    )


async def save_link(session: AsyncSession, link_id: uuid.UUID, url: str, enabled: bool) -> ProviderLink:
    await list_links(session)  # make sure the pair exists before editing
    row = await model_links_service.update_link(
        session, llm_client.LINK_OWNER, llm_client.LINK_PROJECT, link_id, url, enabled
    )
    llm_client.forget_links()  # takes effect on the next question, not in 15 seconds
    llm_client._alive.pop(row.url, None)
    return row
