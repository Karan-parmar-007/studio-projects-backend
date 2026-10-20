from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Request
from botocore.client import BaseClient
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    postgres = request.app.state.postgres_session
    async for session in postgres.get_session():
        yield session


DbDep = Annotated[AsyncSession, Depends(get_db)]


async def get_garage(request: Request):
    garage = request.app.state.garage_session
    async for client in garage.get_client():
        yield client


GarageClientDep = Annotated["BaseClient", Depends(get_garage)]
