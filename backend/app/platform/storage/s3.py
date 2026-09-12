"""The S3/MinIO adapter.

The only module in the system that imports boto3. Everything else depends on `ObjectStore`, so
replacing the store means replacing this file and nothing above it.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING, Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.platform.storage.port import StoredObject

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

logger = logging.getLogger(__name__)


class S3ObjectStore:
    """`ObjectStore` over an S3-compatible endpoint."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
    ) -> None:
        self._bucket = bucket
        self._client: S3Client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            # MinIO speaks SigV4 and serves buckets as a path prefix rather than a subdomain, which
            # is also what a developer's `localhost:9000` needs in order to resolve at all.
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def presigned_put(self, key: str, *, media_type: str, expires_in: dt.timedelta) -> str:
        return self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": key, "ContentType": media_type},
            ExpiresIn=int(expires_in.total_seconds()),
        )

    def presigned_get(self, key: str, *, expires_in: dt.timedelta) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=int(expires_in.total_seconds()),
        )

    def stat(self, key: str) -> StoredObject | None:
        try:
            head: Any = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            # A missing object is the ordinary answer to "did the client finish uploading", not an
            # exceptional condition. Anything else is, and is re-raised rather than read as absence.
            if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return StoredObject(
            key=key,
            size_bytes=int(head["ContentLength"]),
            etag=str(head["ETag"]).strip('"'),
            media_type=head.get("ContentType"),
        )

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)
