"""Garage object storage for Studio's app binaries.

Path-style, SigV4, request-scoped clients (same shape as the other apps). Studio
only stores the installer binaries here, in the bucket GARAGE_BUCKET_NAME.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import aioboto3
from botocore.client import BaseClient, Config
from botocore.exceptions import ClientError

from app.config import garage_settings
from app.core.errors import NotFoundError, StorageError

logger = logging.getLogger(__name__)
_SERVICE = "s3"


@dataclass(frozen=True)
class StoredFile:
    key: str
    content: bytes
    content_type: str
    size: int


class GarageSession:
    def __init__(self) -> None:
        self._session = aioboto3.Session()

    async def get_client(self) -> AsyncGenerator[BaseClient, None]:
        if not garage_settings.configured:
            raise StorageError("Garage storage is not configured")
        config = Config(signature_version="s3v4", s3={"addressing_style": "path"})
        async with self._session.client(
            _SERVICE,
            endpoint_url=garage_settings.GARAGE_ENDPOINT_URL,
            aws_access_key_id=garage_settings.GARAGE_ACCESS_KEY,
            aws_secret_access_key=garage_settings.GARAGE_SECRET_KEY,
            region_name=garage_settings.GARAGE_REGION_NAME,
            config=config,
        ) as client:
            yield client


async def put_object(client: BaseClient, key: str, content: bytes, content_type: str) -> None:
    try:
        await client.put_object(
            Bucket=garage_settings.GARAGE_BUCKET_NAME,
            Key=key,
            Body=content,
            ContentType=content_type,
        )
    except ClientError as exc:
        logger.error("Garage put failed for %s: %s", key, exc)
        raise StorageError(f"Could not store '{key}'") from exc


async def get_object(client: BaseClient, key: str) -> StoredFile:
    try:
        response = await client.get_object(Bucket=garage_settings.GARAGE_BUCKET_NAME, Key=key)
        content = await response["Body"].read()
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey"):
            raise NotFoundError("That download is not available") from exc
        logger.error("Garage get failed for %s: %s", key, exc)
        raise StorageError(f"Could not read '{key}'") from exc
    return StoredFile(
        key=key,
        content=content,
        content_type=response.get("ContentType", "application/octet-stream"),
        size=response.get("ContentLength", len(content)),
    )


async def delete_object(client: BaseClient, key: str) -> None:
    try:
        await client.delete_object(Bucket=garage_settings.GARAGE_BUCKET_NAME, Key=key)
    except ClientError as exc:
        logger.warning("Garage delete failed for %s: %s", key, exc)
