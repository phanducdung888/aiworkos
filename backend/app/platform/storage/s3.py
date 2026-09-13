"""The S3/MinIO adapter.

The only module in the system that imports boto3. Everything else depends on `ObjectStore`, so
replacing the store means replacing this file and nothing above it.

It holds three clients, not one, because there are three routes to the same store and a presigned
URL cannot be rewritten after signing — the signature covers the host (ADR-0063).

* `stat` and `delete` go straight to the store over the internal endpoint. This process is not a
  client and needs no proxy.
* An **upload** URL is handed to a connector: a service on the application network, which reaches
  the object proxy by its service name.
* A **download** URL is handed to a browser: a person on a laptop, which reaches the same proxy by
  a published address and cannot resolve a Docker service name at all.

CP24 signed both for one address and CP25 found the consequence in a browser: the download URL
pointed at `objects:9000`, which is not a name any browser will ever resolve (ADR-0067).
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
        upload_endpoint_url: str | None = None,
    ) -> None:
        """Three endpoints, because three different kinds of client reach the store.

        `endpoint_url` is how this process talks to the store — inside the data network, where the
        store lives and where nothing else is allowed.

        `upload_endpoint_url` is how a **connector** reaches it: a service on the application
        network, addressing the object proxy by its service name.

        `public_endpoint_url` is how a **browser** reaches it: a published address on a person's
        machine, which cannot resolve a Docker service name.

        The split exists because a presigned URL cannot be rewritten after it is signed. The
        signature covers the host, so handing a client a URL signed for a name it cannot reach
        produces either a DNS failure or a 403, and neither is fixable after the fact.

        Each falls back to the one below it, so a deployment with a single reachable address —
        which is what a cloud deployment with a public bucket has — behaves exactly as before.
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
        # The same credentials in every case: the proxy forwards to the same store, so a URL signed
        # for any of these names is honoured by it. What differs is only the host the signature
        # covers, and therefore who can use the URL.
        self._downloads: S3Client = (
            self._client if not public_endpoint_url else client_for(public_endpoint_url)
        )
        self._uploads: S3Client = (
            self._downloads if not upload_endpoint_url else client_for(upload_endpoint_url)
        )

    def presigned_put(self, key: str, *, media_type: str, expires_in: dt.timedelta) -> str:
        """Signed for the uploader's route: today a connector, on the application network."""
        return self._uploads.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": key, "ContentType": media_type},
            ExpiresIn=int(expires_in.total_seconds()),
        )

    def presigned_get(self, key: str, *, expires_in: dt.timedelta) -> str:
        """Signed for the address a person's browser can reach, because that is who opens a file."""
        return self._downloads.generate_presigned_url(
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
