"""The S3 adapter (Phase 12): bytes in, bytes out, encrypted, never an unsafe key.

Driven through botocore's ``Stubber``, which asserts the exact API calls and
parameters without a network - so these run everywhere the suite runs. The
same adapter was also exercised against a live S3-compatible store in the
Phase 12 staging rehearsal (docs/phase-12/phase-12-status.md).
"""

from __future__ import annotations

import hashlib
import io

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber

from workeasy360.contracts.errors import FileError
from workeasy360.platform.files import storage
from workeasy360.platform.files.storage_s3 import S3FileStore

pytestmark = [pytest.mark.security]

ORG = "7c1a4a4e-2f0b-4c63-9b1e-3d1f0f7f8a01"
KEY = f"org/{ORG}/partner_branding/0b9f4f36-44b7-4c0e-9a57-7d5c9e1c2f10.png"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 300


@pytest.fixture
def client():
    return boto3.session.Session().client(
        "s3",
        region_name="eu-west-2",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )


def _store(client, **kwargs):
    return S3FileStore(client, bucket="w360-files", prefix="files", **kwargs)


def test_put_encrypts_prefixes_and_returns_the_checksum_of_the_bytes_sent(client):
    store = _store(client)
    with Stubber(client) as stub:
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "w360-files",
                "Key": f"files/{KEY}",
                "Body": ANY,
                "ContentLength": len(PNG),
                "ContentType": "image/png",
                "ServerSideEncryption": "AES256",
            },
        )
        stored = store.put(KEY, io.BytesIO(PNG), content_type="image/png")
        stub.assert_no_pending_responses()
    assert stored.key == KEY
    assert stored.size_bytes == len(PNG)
    assert stored.checksum_sha256 == hashlib.sha256(PNG).hexdigest()


def test_kms_encryption_names_the_key(client):
    store = _store(client, sse="aws:kms", kms_key_id="alias/w360-files")
    with Stubber(client) as stub:
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "w360-files",
                "Key": f"files/{KEY}",
                "Body": ANY,
                "ContentLength": len(PNG),
                "ContentType": "image/png",
                "ServerSideEncryption": "aws:kms",
                "SSEKMSKeyId": "alias/w360-files",
            },
        )
        store.put(KEY, io.BytesIO(PNG), content_type="image/png")
        stub.assert_no_pending_responses()


def test_get_returns_a_context_managed_readable_body(client):
    store = _store(client)
    with Stubber(client) as stub:
        stub.add_response(
            "get_object",
            {"Body": StreamingBody(io.BytesIO(PNG), len(PNG))},
            {"Bucket": "w360-files", "Key": f"files/{KEY}"},
        )
        with store.get(KEY) as handle:
            assert handle.read(len(PNG) + 1) == PNG


def test_a_missing_object_is_a_file_error_not_a_crash(client):
    store = _store(client)
    with Stubber(client) as stub:
        stub.add_client_error(
            "get_object", service_error_code="NoSuchKey", http_status_code=404
        )
        with pytest.raises(FileError, match="No stored object"):
            store.get(KEY)


def test_a_store_failure_names_no_detail_of_the_store(client):
    store = _store(client)
    with Stubber(client) as stub:
        stub.add_client_error(
            "put_object", service_error_code="AccessDenied", http_status_code=403
        )
        with pytest.raises(FileError) as caught:
            store.put(KEY, io.BytesIO(PNG), content_type="image/png")
    assert "w360-files" not in str(caught.value)


def test_delete_is_idempotent(client):
    store = _store(client)
    with Stubber(client) as stub:
        stub.add_response(
            "delete_object", {}, {"Bucket": "w360-files", "Key": f"files/{KEY}"}
        )
        stub.add_client_error(
            "delete_object", service_error_code="NoSuchKey", http_status_code=404
        )
        store.delete(KEY)
        store.delete(KEY)


def test_exists(client):
    store = _store(client)
    with Stubber(client) as stub:
        stub.add_response(
            "head_object", {}, {"Bucket": "w360-files", "Key": f"files/{KEY}"}
        )
        stub.add_client_error(
            "head_object", service_error_code="404", http_status_code=404
        )
        assert store.exists(KEY) is True
        assert store.exists(KEY) is False


@pytest.mark.parametrize(
    "key",
    [
        "../etc/passwd",
        f"org/{ORG}/../../other",
        "org/not-a-uuid/partner_branding/x.png",
        f"/org/{ORG}/partner_branding/0b9f4f36-44b7-4c0e-9a57-7d5c9e1c2f10.png",
    ],
)
def test_an_unsafe_key_is_refused_before_any_call(client, key):
    """No stubbed response is queued: a call reaching the client would fail
    the Stubber, so the refusal provably happens first."""
    store = _store(client)
    with Stubber(client):
        for operation in (
            lambda: store.put(key, io.BytesIO(PNG), content_type="image/png"),
            lambda: store.get(key),
            lambda: store.delete(key),
            lambda: store.exists(key),
            lambda: store.presign_download(key),
        ):
            with pytest.raises(FileError, match="unsafe storage key"):
                operation()


def test_downloads_stay_application_urls_signed_by_the_platform(client, settings):
    """Content is served through the platform (tenant + scan checks), never as
    an S3 presigned URL that bypasses them."""
    settings.FILE_URL_SIGNING_KEY = "k" * 40
    url = _store(client).presign_download(KEY)
    assert url.startswith("/api/v1/files/content?key=")
    assert "amazonaws" not in url and "X-Amz" not in url


def test_the_factory_returns_the_s3_adapter_when_configured(settings):
    settings.FILE_STORAGE_BACKEND = "s3"
    settings.S3_BUCKET = "w360-files"
    settings.S3_REGION = "eu-west-2"
    settings.S3_ENDPOINT_URL = ""
    settings.S3_KEY_PREFIX = ""
    settings.S3_SSE = "AES256"
    settings.S3_KMS_KEY_ID = ""
    assert isinstance(storage.get_store(), S3FileStore)


def test_a_missing_bucket_is_refused():
    with pytest.raises(FileError, match="W360_S3_BUCKET"):
        S3FileStore(object(), bucket="")
