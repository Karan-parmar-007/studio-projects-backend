"""LLM chat and cleanup run only on the Oracle server."""

from app.config import auth_settings

PROJECT = "llm"

SLOTS = (("llm", "remote"),)

OPERATIONS = {
    "chat": ("llm", "/chat"),
    "format": ("llm", "/format"),
}


def default_links() -> dict[tuple[str, str], str]:
    return {("llm", "remote"): auth_settings.LLM_REMOTE_URL}
