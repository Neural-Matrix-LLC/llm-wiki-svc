"""Cloudflare R2 via boto3's S3-compatible client.

Two R2 specifics worth remembering (plan 6.2): the region must be the literal
string ``"auto"``, and newer boto3 releases send additional checksum headers that
R2 rejects - hence the explicit ``request_checksum_calculation`` config.
"""

from __future__ import annotations

from typing import Any

from llmwiki.storage.base import ObjectNotFound


class R2ObjectStore:
    """S3-compatible object store backed by a single R2 bucket."""

    def __init__(
        self,
        endpoint_url: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
    ) -> None:
        import boto3
        from botocore.config import Config

        try:
            config = Config(
                region_name="auto",
                retries={"max_attempts": 5, "mode": "standard"},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            )
        except TypeError:
            # Older botocore has no checksum knobs; it also does not send the headers.
            config = Config(region_name="auto", retries={"max_attempts": 5, "mode": "standard"})

        self.bucket = bucket
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=config,
        )

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._client.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )

    def get(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except self._client.exceptions.NoSuchKey as exc:
            raise ObjectNotFound(key) from exc
        body: bytes = response["Body"].read()
        return body

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def list(self, prefix: str) -> list[str]:
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(item["Key"] for item in page.get("Contents", []))
        return keys

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)
