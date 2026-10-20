"""WhisperFlow: speech on the user's PC, text on the PC first and the VPS second."""

from app.config import auth_settings

PROJECT = "whisperflow"

# Which model links this project keeps, in display order.
SLOTS = (
    ("speech", "local"),
    ("llm", "local"),
    ("llm", "remote"),
)

# Operation -> (link role, path on the gateway)
OPERATIONS = {
    "transcribe": ("speech", "/transcribe"),
    "format": ("llm", "/format"),
}


def default_links() -> dict[tuple[str, str], str]:
    return {
        ("speech", "local"): auth_settings.SPEECH_LOCAL_URL,
        ("llm", "local"): auth_settings.LLM_LOCAL_URL,
        ("llm", "remote"): auth_settings.LLM_REMOTE_URL,
    }
