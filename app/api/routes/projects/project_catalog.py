"""The products Studio offers. One entry per project folder under app/api/routes.

Adding a project: add its entry here, give it a routes module, and (if it needs
model links) call model_links_service.ensure_links with its own slots.
"""

from __future__ import annotations

PROJECTS: list[dict] = [
    {
        "slug": "whisperflow",
        "name": "WhisperFlow",
        "tagline": "Speak into any text box.",
        "description": (
            "A Whisper Flow clone. Hindi, English, and Hinglish. Tara writes down what you "
            "said, Qwen turns it into the text you meant, and the apps paste it wherever you "
            "were typing. Try it in the playground, or use the API key from any app."
        ),
        "status": "live",
        "page": "/whisperflow-clone",
        "playground": True,
        "apps": ["windows", "linux", "android"],
        "endpoints": [
            {
                "operation": "transcribe",
                "method": "POST",
                "path": "/api/studio/v1/whisperflow/transcribe",
                "summary": "Send audio (base64 WAV, WebM, Ogg, MP3...) and get a transcript.",
            },
            {
                "operation": "format",
                "method": "POST",
                "path": "/api/studio/v1/whisperflow/format",
                "summary": "Turn a transcript into the text you meant, lists included.",
            },
            {
                "operation": "job",
                "method": "GET",
                "path": "/api/studio/v1/whisperflow/jobs/{job_id}",
                "summary": "Poll a queued request until it is done.",
            },
        ],
    },
    {
        "slug": "chatbot",
        "name": "Chatbot",
        "tagline": "A chat API on the same models.",
        "description": "Coming next. Chat on the LLM gateway, with RAG behind it.",
        "status": "soon",
        "page": "",
        "playground": False,
        "apps": [],
        "endpoints": [],
    },
]


def find_project(slug: str) -> dict | None:
    return next((item for item in PROJECTS if item["slug"] == slug), None)
