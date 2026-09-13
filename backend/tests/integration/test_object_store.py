"""The S3 adapter, against a real object store. Off unless you turn it on.

    make up
    RUN_OBJECT_STORE_TESTS=1 pytest tests/integration/test_object_store.py

`S3ObjectStore` is the only module that imports boto3, it issues **credential-bearing URLs**, and
until CP22 it had no test of any kind. Every attachment test in this suite installs
`InMemoryObjectStore`, which is the right default — it lets the capture path run anywhere — and
which means the adapter that production actually uses had never been executed.

Opt-in for the same reason the provider and mailbox tests are: CI stays offline, and a suite that
needs a service up is a suite that gets skipped in the environment which should run it.

**What a presigned URL is** is the thing worth holding: a URL that carries authority. These tests
use it the way a client does — an ordinary HTTP request with no credentials of its own — because
that is the only way to find out whether it actually grants what it claims.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import time
import urllib.error
import urllib.request
import uuid

import pytest

from app.platform.storage import S3ObjectStore

pytestmark = pytest.mark.integration

ENABLED = os.environ.get("RUN_OBJECT_STORE_TESTS") == "1"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not ENABLED, reason="needs MinIO; set RUN_OBJECT_STORE_TESTS=1 and `make up`"
    ),
]

ENDPOINT = os.environ.get("S3_TEST_ENDPOINT", "http://127.0.0.1:9000")
ACCESS_KEY = os.environ.get("MINIO_ROOT_USER", "workos")
SECRET_KEY = os.environ.get("MINIO_ROOT_PASSWORD", "workos-dev-secret")
MINUTE = dt.timedelta(minutes=1)

CONTENT = b"%PDF-1.4 the revised quote\n"


@pytest.fixture(scope="module")
def bucket() -> str:
    """A bucket of this run's own, created the way a deployment has to create one.

    Worth noticing: nothing in the application creates the bucket. `S3ObjectStore` assumes it
    exists, so a deployment that forgets is a deployment where every upload fails at the first
    attachment — which is recorded here rather than discovered there.
    """
    import boto3
    from botocore.client import Config

    name = f"workos-test-{uuid.uuid4().hex[:12]}"
    client = boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    client.create_bucket(Bucket=name)
    return name


@pytest.fixture
def s3_backed(api, bucket: str):  # type: ignore[no-untyped-def]
    """Point the application at the real store for one test, then put back what was there.

    Restoring the *previous* value rather than `None`: the app is built with an in-memory store and
    the rest of the suite depends on finding it, so a test that cleared the slot would break a
    later one — which is exactly what the first version of this file did.
    """
    previous = getattr(api.app.state, "object_store", None)
    api.app.state.object_store = S3ObjectStore(
        endpoint_url=ENDPOINT,
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        bucket=bucket,
    )
    yield api
    api.app.state.object_store = previous


@pytest.fixture
def store(bucket: str) -> S3ObjectStore:
    return S3ObjectStore(
        endpoint_url=ENDPOINT,
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        bucket=bucket,
    )


def put(url: str, body: bytes, *, media_type: str) -> int:
    request = urllib.request.Request(
        url, data=body, method="PUT", headers={"Content-Type": media_type}
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return int(response.status)


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=15) as response:
        return bytes(response.read())


def a_key() -> str:
    return f"org/{uuid.uuid4().hex}/event/{uuid.uuid4().hex}/{uuid.uuid4().hex}"


# --------------------------------------------------------------------------- the round trip


def test_a_presigned_url_actually_grants_the_upload(store: S3ObjectStore) -> None:
    """The claim the whole design rests on: bytes never pass through the API (ADR-0039)."""
    key = a_key()
    status = put(
        store.presigned_put(key, media_type="application/pdf", expires_in=MINUTE),
        CONTENT,
        media_type="application/pdf",
    )
    assert status in (200, 204)


def test_what_the_store_reports_is_what_was_uploaded(store: S3ObjectStore) -> None:
    """`complete` reads size and checksum from the object, never from the request (ADR-0039).

    So the adapter's `stat` has to agree with the bytes, and the etag has to be the thing a caller
    can compare against — for a single-part upload, MD5 of the content.
    """
    key = a_key()
    put(
        store.presigned_put(key, media_type="application/pdf", expires_in=MINUTE),
        CONTENT,
        media_type="application/pdf",
    )

    stored = store.stat(key)

    assert stored is not None
    assert stored.key == key
    assert stored.size_bytes == len(CONTENT)
    assert stored.etag == hashlib.md5(CONTENT).hexdigest()  # noqa: S324 - S3's etag, not a secret
    assert stored.media_type == "application/pdf"


def test_the_download_url_returns_the_same_bytes(store: S3ObjectStore) -> None:
    key = a_key()
    put(
        store.presigned_put(key, media_type="application/pdf", expires_in=MINUTE),
        CONTENT,
        media_type="application/pdf",
    )
    assert get(store.presigned_get(key, expires_in=MINUTE)) == CONTENT


def test_an_object_that_was_never_uploaded_is_absent_not_an_error(
    store: S3ObjectStore,
) -> None:
    """"Did the client finish" is an ordinary question with an ordinary answer.

    The attachment row stays `pending` on a `None`, which is how an unfinished upload stays visible
    as unfinished rather than becoming a row claiming a file nobody can fetch.
    """
    assert store.stat(a_key()) is None


def test_delete_removes_it(store: S3ObjectStore) -> None:
    key = a_key()
    put(
        store.presigned_put(key, media_type="text/plain", expires_in=MINUTE),
        b"transient",
        media_type="text/plain",
    )
    assert store.stat(key) is not None

    store.delete(key)

    assert store.stat(key) is None


# --------------------------------------------------------------------------- what it will not do


def test_an_expired_url_grants_nothing(store: S3ObjectStore) -> None:
    """A presigned URL is a credential with a deadline, and the deadline has to be real.

    Actually waited out rather than signed with a negative window: a negative one is refused for
    being malformed, which proves boto3 validates its parameters and nothing about expiry.
    """
    url = store.presigned_put(
        a_key(), media_type="text/plain", expires_in=dt.timedelta(seconds=1)
    )
    time.sleep(2)

    with pytest.raises(urllib.error.HTTPError) as refused:
        put(url, b"too late", media_type="text/plain")
    assert refused.value.code == 403


def test_a_url_for_one_key_does_not_work_for_another(store: S3ObjectStore) -> None:
    """The signature covers the key, which is why the service derives it and never accepts one.

    A client that could substitute the key could ask for a URL to its own object and then write to
    somebody else's (`object_key_for`, BR-E-08).
    """
    url = store.presigned_put(a_key(), media_type="text/plain", expires_in=MINUTE)
    tampered = url.replace(url.split("?")[0].rsplit("/", 1)[-1], uuid.uuid4().hex, 1)

    with pytest.raises(urllib.error.HTTPError) as refused:
        put(tampered, b"somewhere else", media_type="text/plain")
    assert refused.value.code == 403


def test_an_unsigned_request_is_refused(store: S3ObjectStore) -> None:
    """The bucket is not public. Without this, none of the signing above would matter."""
    key = a_key()
    put(
        store.presigned_put(key, media_type="text/plain", expires_in=MINUTE),
        b"private",
        media_type="text/plain",
    )
    unsigned = store.presigned_get(key, expires_in=MINUTE).split("?")[0]

    with pytest.raises(urllib.error.HTTPError) as refused:
        get(unsigned)
    assert refused.value.code == 403


def test_a_url_signed_for_one_bucket_does_not_reach_another(bucket: str) -> None:
    """Tenancy is not what separates buckets here — the key is — but a signature that ignored the
    bucket would make the object key the only thing between organizations."""
    elsewhere = S3ObjectStore(
        endpoint_url=ENDPOINT,
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        bucket=f"{bucket}-does-not-exist",
    )
    with pytest.raises(urllib.error.HTTPError) as refused:
        put(
            elsewhere.presigned_put(a_key(), media_type="text/plain", expires_in=MINUTE),
            b"nowhere",
            media_type="text/plain",
        )
    assert refused.value.code in (403, 404)


# --------------------------------------------------------------------------- the whole flow


def test_the_attachment_flow_works_against_a_real_store(
    s3_backed, as_member: dict[str, str], roles: None
) -> None:
    """ADR-0039 end to end, with the adapter production uses rather than the in-memory one.

    Every other attachment test installs `InMemoryObjectStore`, which is the right default — it
    lets the capture path run anywhere — and which means this sequence had never been executed
    against a real object store. The upload here is an ordinary HTTP PUT with no credentials of its
    own, exactly as a client makes it.
    """
    api = s3_backed
    event = api.post(
        "/api/v1/events",
        json={
            "type": "MANUAL_CAPTURE",
            "occurred_at": "2026-09-12T09:00:00+00:00",
            "body_text": "the quote is attached",
        },
        headers=as_member,
    ).json()

    ticket = api.post(
        f"/api/v1/events/{event['id']}/attachments",
        json={"filename": "quote.pdf", "media_type": "application/pdf"},
        headers=as_member,
    )
    assert ticket.status_code == 201, ticket.text
    reserved = ticket.json()
    assert reserved["attachment"]["status"] == "pending"

    # The bytes never pass through the API. This is the whole point of the design.
    assert put(reserved["upload_url"], CONTENT, media_type="application/pdf") in (200, 204)

    completed = api.post(
        f"/api/v1/events/{event['id']}/attachments/"
        f"{reserved['attachment']['id']}/complete",
        headers=as_member,
    )
    assert completed.status_code == 200, completed.text
    # Size and checksum are read from the object, never taken from a request body.
    assert completed.json()["size_bytes"] == len(CONTENT)
    assert completed.json()["checksum"] == hashlib.md5(CONTENT).hexdigest()  # noqa: S324
    assert completed.json()["status"] == "available"

    content = api.get(
        f"/api/v1/events/{event['id']}/attachments/"
        f"{reserved['attachment']['id']}/content",
        headers=as_member,
    )
    assert content.status_code == 200, content.text
    assert get(content.json()["download_url"]) == CONTENT


def test_an_unfinished_upload_stays_visible_as_unfinished(
    s3_backed, as_member: dict[str, str], roles: None
) -> None:
    """A client that never uploads leaves a `pending` row, not one claiming a file nobody can
    fetch. Against a real store, `complete` has to actually fail rather than believe the client."""
    api = s3_backed
    event = api.post(
        "/api/v1/events",
        json={
            "type": "MANUAL_CAPTURE",
            "occurred_at": "2026-09-12T09:00:00+00:00",
            "body_text": "nothing will be uploaded",
        },
        headers=as_member,
    ).json()
    reserved = api.post(
        f"/api/v1/events/{event['id']}/attachments",
        json={"filename": "never.pdf", "media_type": "application/pdf"},
        headers=as_member,
    ).json()

    refused = api.post(
        f"/api/v1/events/{event['id']}/attachments/"
        f"{reserved['attachment']['id']}/complete",
        headers=as_member,
    )

    assert refused.status_code == 422
    assert reserved["attachment"]["status"] == "pending"
