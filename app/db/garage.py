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


# ---------------------------------------------------------------- RAG helpers


def garage_client():
    """`async with garage_client() as client:` outside a request (worker, sweeper)."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _ctx():
        session = GarageSession()
        async for client in session.get_client():
            yield client

    return _ctx()


async def download_to_file(client: BaseClient, key: str, path: str) -> int:
    """Stream an object to disk without holding it in memory. Returns bytes written."""
    try:
        response = await client.get_object(Bucket=garage_settings.GARAGE_BUCKET_NAME, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey"):
            raise NotFoundError("That file is gone") from exc
        raise StorageError(f"Could not read '{key}'") from exc
    written = 0
    with open(path, "wb") as handle:
        async for chunk in response["Body"].iter_chunks(1 << 16):
            handle.write(chunk)
            written += len(chunk)
    return written


async def open_stream(client: BaseClient, key: str) -> tuple[object, str, int]:
    """(async byte iterator, content type, length) for streaming a stored object out."""
    try:
        response = await client.get_object(Bucket=garage_settings.GARAGE_BUCKET_NAME, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey"):
            raise NotFoundError("That image is gone") from exc
        raise StorageError(f"Could not read '{key}'") from exc

    async def chunks():
        async for chunk in response["Body"].iter_chunks(1 << 16):
            yield chunk

    return chunks(), response.get("ContentType", "application/octet-stream"), int(response.get("ContentLength") or 0)


async def copy_object(client: BaseClient, source_key: str, target_key: str) -> None:
    bucket = garage_settings.GARAGE_BUCKET_NAME
    try:
        await client.copy_object(Bucket=bucket, Key=target_key, CopySource={"Bucket": bucket, "Key": source_key})
    except ClientError as exc:
        logger.error("Garage copy failed %s -> %s: %s", source_key, target_key, exc)
        raise StorageError(f"Could not copy '{source_key}'") from exc


async def delete_prefix(client: BaseClient, prefix: str) -> int:
    """Delete every object under a prefix. Returns how many were deleted. Retry-safe."""
    if not prefix or not prefix.endswith("/"):
        raise ValueError("prefix must end with '/'")
    bucket = garage_settings.GARAGE_BUCKET_NAME
    deleted = 0
    token: str | None = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        try:
            page = await client.list_objects_v2(**kwargs)
        except ClientError as exc:
            raise StorageError(f"Could not list '{prefix}'") from exc
        keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
        if keys:
            try:
                await client.delete_objects(Bucket=bucket, Delete={"Objects": keys, "Quiet": True})
            except ClientError:
                for key in keys:  # some S3 servers lack batch delete
                    await delete_object(client, key["Key"])
            deleted += len(keys)
        if not page.get("IsTruncated"):
            return deleted
        token = page.get("NextContinuationToken")
