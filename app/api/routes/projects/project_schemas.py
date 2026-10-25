from pydantic import BaseModel


class EndpointOut(BaseModel):
    operation: str
    method: str
    path: str
    summary: str


class ProjectOut(BaseModel):
    slug: str
    name: str
    tagline: str
    description: str
    status: str
    enabled: bool = True
    page: str
    playground: bool
    apps: list[str]
    endpoints: list[EndpointOut]
