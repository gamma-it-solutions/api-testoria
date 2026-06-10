"""Async S3-compatible object storage wrapper used by result attachments."""

from __future__ import annotations

import logging
from typing import Any, cast

import aioboto3
import boto3
from botocore.client import BaseClient, Config
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger(__name__)


_session: aioboto3.Session | None = None
_sync_clients: dict[str, BaseClient] = {}


def _get_session() -> aioboto3.Session:
    global _session
    if _session is None:
        _session = aioboto3.Session()
    return _session


def _public_endpoint() -> str:
    return settings.S3_PUBLIC_ENDPOINT_URL or settings.S3_ENDPOINT_URL


def _client_kwargs(endpoint_url: str | None = None) -> dict[str, Any]:
    endpoint = endpoint_url or settings.S3_ENDPOINT_URL
    return {
        "service_name": "s3",
        "endpoint_url": endpoint,
        "aws_access_key_id": settings.S3_ACCESS_KEY,
        "aws_secret_access_key": settings.S3_SECRET_KEY,
        "region_name": settings.S3_REGION,
        "config": Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    }


async def ensure_bucket(bucket: str | None = None) -> None:
    bucket = bucket or settings.S3_BUCKET_ATTACHMENTS
    session = _get_session()
    async with session.client(**_client_kwargs()) as client:
        try:
            await client.head_bucket(Bucket=bucket)
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code")
            if code not in {"404", "NoSuchBucket", "NoSuchKey"}:
                logger.info("head_bucket failed with %s; attempting create", code)
            await client.create_bucket(Bucket=bucket)


async def put_object(
    key: str,
    body: bytes,
    content_type: str | None = None,
    bucket: str | None = None,
) -> None:
    bucket = bucket or settings.S3_BUCKET_ATTACHMENTS
    session = _get_session()
    async with session.client(**_client_kwargs()) as client:
        extra: dict[str, Any] = {}
        if content_type:
            extra["ContentType"] = content_type
        await client.put_object(Bucket=bucket, Key=key, Body=body, **extra)


async def get_object(key: str, bucket: str | None = None) -> bytes:
    bucket = bucket or settings.S3_BUCKET_ATTACHMENTS
    session = _get_session()
    async with session.client(**_client_kwargs()) as client:
        resp = await client.get_object(Bucket=bucket, Key=key)
        async with resp["Body"] as stream:
            return cast(bytes, await stream.read())


async def delete_object(key: str, bucket: str | None = None) -> None:
    """Idempotent — swallows NoSuchKey."""
    bucket = bucket or settings.S3_BUCKET_ATTACHMENTS
    session = _get_session()
    async with session.client(**_client_kwargs()) as client:
        try:
            await client.delete_object(Bucket=bucket, Key=key)
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code")
            if code in {"NoSuchKey", "404"}:
                return
            raise


async def generate_presigned_url(
    key: str,
    ttl_seconds: int | None = None,
    bucket: str | None = None,
) -> str:
    bucket = bucket or settings.S3_BUCKET_ATTACHMENTS
    ttl = ttl_seconds if ttl_seconds is not None else settings.S3_PRESIGN_TTL_SECONDS
    session = _get_session()
    async with session.client(**_client_kwargs(_public_endpoint())) as client:
        return cast(
            str,
            await client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=ttl,
            ),
        )


def _get_sync_client(endpoint_url: str) -> BaseClient:
    """Per-endpoint cached sync boto3 client used only for presigned URL generation.

    `generate_presigned_url` does not touch the network — it's pure signature
    math — so a sync client is safe inside request handlers / pydantic
    validators without blocking the event loop.
    """
    client = _sync_clients.get(endpoint_url)
    if client is None:
        client = cast(BaseClient, boto3.client(**_client_kwargs(endpoint_url)))
        _sync_clients[endpoint_url] = client
    return client


def generate_presigned_url_sync(
    key: str,
    ttl_seconds: int | None = None,
    bucket: str | None = None,
) -> str:
    bucket = bucket or settings.S3_BUCKET_ATTACHMENTS
    ttl = ttl_seconds if ttl_seconds is not None else settings.S3_PRESIGN_TTL_SECONDS
    client = _get_sync_client(_public_endpoint())
    return cast(
        str,
        client.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=ttl,
        ),
    )
