from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.api.routes.gmaps.gmaps_constants import (
    DAILY_COMPANY_LIMIT,
    MAX_PLACE_NAME,
    MAX_PLACES,
    MAX_SEARCH_TERM,
)

Place = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_PLACE_NAME)]


class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_term: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_SEARCH_TERM)
    ]
    countries: list[Place] = Field(default_factory=list, max_length=MAX_PLACES)
    states: list[Place] = Field(default_factory=list, max_length=MAX_PLACES)
    cities: list[Place] = Field(default_factory=list, max_length=MAX_PLACES)
    # How many companies to collect. Empty means everything left for today.
    limit: int | None = Field(default=None, ge=1, le=DAILY_COMPANY_LIMIT)


class QuotaOut(BaseModel):
    limit: int
    used: int
    remaining: int
    day: str


class PlacesOut(BaseModel):
    items: list[str]
