from pydantic import BaseModel


class FormatIn(BaseModel):
    text: str
    tone: str = ""
    script: str = "roman"


class TranscribeIn(BaseModel):
    audio_base64: str = ""
