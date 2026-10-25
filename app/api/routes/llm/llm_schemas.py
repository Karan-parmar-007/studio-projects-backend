from pydantic import BaseModel


class ChatIn(BaseModel):
    text: str


class FormatIn(BaseModel):
    text: str
    script: str = "roman"
