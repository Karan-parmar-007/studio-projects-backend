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
        "description": "Speak, and the text shows up. Hindi, English, and Hinglish.",
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
        "slug": "llm",
        "name": "LLM",
        "tagline": "Send a message. Get a reply.",
        "description": "A chatbot. Ask a question, or send text to clean up.",
        "status": "live",
        "page": "/products/llm",
        "playground": True,
        "apps": [],
        "endpoints": [
            {
                "operation": "chat",
                "method": "POST",
                "path": "/api/studio/v1/llm/chat",
                "summary": "Send a message and get a reply.",
            },
            {
                "operation": "format",
                "method": "POST",
                "path": "/api/studio/v1/llm/format",
                "summary": "Clean up text: punctuation, lists, and Hindi script.",
            },
            {
                "operation": "job",
                "method": "GET",
                "path": "/api/studio/v1/llm/jobs/{job_id}",
                "summary": "Poll a queued reply until it is done.",
            },
        ],
    },
    {
        "slug": "gmaps",
        "name": "Maps Scraper",
        "tagline": "Find companies on Google Maps.",
        "description": (
            "Search Google Maps by business type and place, and get names, phones, "
            "websites and ratings as they are found. United States only. "
            "50 companies per user per day."
        ),
        "status": "live",
        "page": "/products/gmaps",
        "playground": True,
        "apps": [],
        "endpoints": [
            {
                "operation": "run",
                "method": "POST",
                "path": "/api/studio/v1/gmaps/runs/stream",
                "summary": "Start a search. Results stream back as NDJSON, one event per line.",
            },
            {
                "operation": "quota",
                "method": "GET",
                "path": "/api/studio/v1/gmaps/quota",
                "summary": "How many of today's 50 companies you have left.",
            },
            {
                "operation": "states",
                "method": "GET",
                "path": "/api/studio/v1/gmaps/locations/states",
                "summary": "States you can search in.",
            },
            {
                "operation": "cities",
                "method": "GET",
                "path": "/api/studio/v1/gmaps/locations/cities",
                "summary": "Cities, optionally only in the given ?state=... (repeatable).",
            },
        ],
    },
    {
        "slug": "rag",
        "name": "Ask a Document",
        "tagline": "Ask questions, get answers with the pictures.",
        "description": (
            "Ask a long illustrated book anything, or upload your own document (up to 5 MB, kept "
            "for an hour). Answers cite the passages they used and show the figures that matter."
        ),
        "status": "live",
        "page": "/products/rag",
        "playground": True,
        "apps": [],
        "endpoints": [
            {
                "operation": "ask",
                "method": "POST",
                "path": "/api/studio/rag/ask",
                "summary": "Ask the book or your upload. The answer streams back as NDJSON events.",
            },
            {
                "operation": "upload",
                "method": "POST",
                "path": "/api/studio/rag/upload",
                "summary": "Upload one document (multipart, field 'file'). Delete it to upload another.",
            },
            {
                "operation": "status",
                "method": "GET",
                "path": "/api/studio/rag/status",
                "summary": "What you can use, the book, and your upload's progress.",
            },
        ],
    },
    {
        "slug": "chatbot",
        "name": "Assistant",
        "tagline": "Ask about Karan and Studio.",
        "description": (
            "The assistant on this site and the portfolio. It knows Karan's work and Studio's "
            "products, and when you are signed in it can set up a Maps Scraper search for you."
        ),
        "status": "live",
        "page": "/products/chatbot",
        "playground": False,
        "apps": [],
        "endpoints": [
            {
                "operation": "chat",
                "method": "POST",
                "path": "/api/studio/chatbot/chat",
                "summary": "Send a message with recent history. The reply streams back as NDJSON events.",
            },
        ],
    },
]


def find_project(slug: str) -> dict | None:
    return next((item for item in PROJECTS if item["slug"] == slug), None)
