"""The S3/MinIO adapter.

The only module in the system that imports boto3. Everything else depends on `ObjectStore`, so
replacing the store means replacing this file and nothing above it.

It holds two clients, not one. Presigned URLs are signed for the endpoint a *client* can reach —
a reverse proxy, or a public bucket address — while `stat` and `delete` go straight to the store
over the internal one. A presigned URL cannot be rewritten after signing, because the signature
covers the host, so the two addresses have to be known at signing time (ADR-0063).
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
        public_endpoint_url: str | None = None,
    ) -> None:
        """Two endpoints, because the API and its clients reach the store by different routes.

        `endpoint_url` is how this process talks to the store — inside the data network, where the
        store lives and where nothing else is allowed. `public_endpoint_url` is the host a *client*
        can reach: a reverse proxy in front of the store, or the store's own public address in a
        cloud deployment.

        The split exists because a presigned URL cannot be rewritten after it is signed. The
        signature covers the host, so handing a client a URL signed for the internal name and then
        editing it produces a 403 — the URL has to be signed for the name the client will use. Hence
        two clients rather than one plus string surgery.

        Defaults to the internal endpoint, so a deployment that has one reachable address behaves
        exactly as it did before.
        """
        self._bucket = bucket
        # MinIO speaks SigV4 and serves buckets as a path prefix rather than a subdomain, which is
        # also what a developer's `localhost:9000` needs in order to resolve at all.
        config = Config(signature_version="s3v4", s3={"addressing_style": "path"})

        def client_for(url: str) -> S3Client:
            return boto3.client(
                "s3",
                endpoint_url=url,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
                config=config,
            )

        self._client: S3Client = client_for(endpoint_url)
        # The same credentials either way: the proxy forwards to the same store, so a URL signed
        # for the public name is honoured by the private one. What differs is only the host the
        # signature covers.
        self._signer: S3Client = (
            self._client if public_endpoint_url is None else client_for(public_endpoint_url)
        )

    def presigned_put(self, key: str, *, media_type: str, expires_in: dt.timedelta) -> str:
        """Signed for the public host: whoever receives this has to be able to reach it."""
        return self._signer.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": key, "ContentType": media_type},
            ExpiresIn=int(expires_in.total_seconds()),
        )

    def presigned_get(self, key: str, *, expires_in: dt.timedelta) -> str:
        """Signed for the public host, for the same reason `presigned_put` is."""
        return self._signer.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=int(expires_in.total_seconds()),
        )

    def stat(self, key: str) -> StoredObject | None:
        """Asked over the internal endpoint. This process is not a client and needs no proxy."""
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
