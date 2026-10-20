"""Pick which saved link a request should try.

Toggle first. A link is only dialed when a request actually needs the model.
Local wins when its toggle is on and a URL is saved. If that call fails, the
formatter may use the server link. Speech stays on the local machine.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LinkChoice:
    role: str
    placement: str
    url: str


@dataclass(frozen=True)
class LinkRow:
    role: str
    placement: str
    url: str
    enabled: bool


def _row(links: list[LinkRow], role: str, placement: str) -> LinkRow | None:
    for link in links:
        if link.role == role and link.placement == placement:
            return link
    return None


def explain(links: list[LinkRow], role: str) -> list[dict]:
    """Why each saved row will or will not be called. Does not touch the network."""
    notes: list[dict] = []
    for placement in ("local", "remote"):
        row = _row(links, role, placement)
        if row is None:
            notes.append({"role": role, "placement": placement, "state": "missing"})
            continue
        if not row.enabled:
            notes.append({"role": role, "placement": placement, "state": "toggle_off"})
            continue
        if not row.url.strip():
            notes.append({"role": role, "placement": placement, "state": "no_link"})
            continue
        notes.append({"role": role, "placement": placement, "state": "ready", "url": row.url.strip()})
    return notes


def candidates(links: list[LinkRow], role: str) -> list[LinkChoice]:
    """Ordered links worth calling. Speech never falls through to the server."""
    ready: list[LinkChoice] = []
    for note in explain(links, role):
        if note["state"] != "ready":
            continue
        if role == "speech" and note["placement"] != "local":
            continue
        ready.append(LinkChoice(role=role, placement=note["placement"], url=note["url"]))
    return ready
