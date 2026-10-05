"""The S3 adapter behind the ``FileStore`` port (Phase 12).

``config/settings/production.py`` has always refused local storage ("not
durable across container restarts") and required ``s3`` - but until this
module existed ``storage.get_store()`` had no adapter for it, so a production
deployment either could not start or failed every file operation (the partner
logo upload, the branded sign-in page's logo). Found by the Phase 12 audit.

What it does, and deliberately does not do:

- **Bytes only.** Metadata, tenancy, scanning and serving stay in
  ``platform/files``: content is read back through the platform (``get``), never
  handed out as an S3 presigned URL, so every read still passes the tenant-key
  and scanned-clean checks and a logo stays same-origin for the CSP. The signed
  URL scheme is the one the local adapter uses (:class:`SignedApplicationUrls`).
- **No credential in settings.** The client uses boto3's default provider chain
  - the task/instance role in production, ``AWS_*`` variables from the secret
  store elsewhere. Nothing here can log or persist a credential.
- **Encrypted at rest, private.** Every object is written with server-side
  encryption (``W360_S3_SSE``: ``AES256`` or ``aws:kms`` with
  ``W360_S3_KMS_KEY_ID``); the bucket itself must block public access
  (docs/phase-12/04-deployment.md).
- **Bounded.** Short connect/read timeouts and the SDK's standard retry mode,
  so a slow store cannot hold a request thread for long.
"""

from __future__ import annotations

import hashlib
import io
import tempfile
from functools import lru_cache
from typing import Any, BinaryIO

from workeasy360.contracts.errors import FileError
from workeasy360.contracts.ports.files import StoredObject
from workeasy360.platform.files.storage import SignedApplicationUrls, validate_key

#: Spill uploads larger than this to disk while hashing, never hold them in RAM.
_SPOOL_MAX_MEMORY = 1024 * 1024
_CHUNK = 64 * 1024


class _Body(io.RawIOBase):
    """A readable, closeable, context-managed view of an S3 response body."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        data = self._stream.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)

    def close(self) -> None:
        try:
            self._stream.close()
        finally:
            super().close()


class S3FileStore(SignedApplicationUrls):
    def __init__(
        self,
        client: Any,
        *,
        bucket: str,
        prefix: str = "",
        sse: str = "AES256",
        kms_key_id: str = "",
    ) -> None:
        if not bucket:
            raise FileError("W360_S3_BUCKET is required for the s3 storage backend.")
        if sse not in ("AES256", "aws:kms"):
            raise FileError("W360_S3_SSE must be 'AES256' or 'aws:kms'.")
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self.sse = sse
        self.kms_key_id = kms_key_id

    def _object_key(self, key: str) -> str:
        validate_key(key)
        return f"{self.prefix}{key}"

    @staticmethod
    def _is_missing(exc: Exception) -> bool:
        response = getattr(exc, "response", None) or {}
        code = str(response.get("Error", {}).get("Code", ""))
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return code in ("404", "NoSuchKey", "NotFound") or status == 404

    def put(self, key: str, content: BinaryIO, *, content_type: str) -> StoredObject:
        object_key = self._object_key(key)
        digest = hashlib.sha256()
        size = 0
        # Hash while copying into a spooled buffer: the checksum is of the
        # bytes actually sent, and a large upload never sits whole in memory.
        with tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX_MEMORY) as spool:
            while chunk := content.read(_CHUNK):
                digest.update(chunk)
                size += len(chunk)
                spool.write(chunk)
            spool.seek(0)
            extra: dict[str, str] = {"ServerSideEncryption": self.sse}
            if self.sse == "aws:kms" and self.kms_key_id:
                extra["SSEKMSKeyId"] = self.kms_key_id
            try:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=object_key,
                    Body=spool,
                    ContentLength=size,
                    ContentType=content_type,
                    **extra,
                )
            except Exception as exc:
                raise FileError(f"Could not store object: {type(exc).__name__}.") from exc
        return StoredObject(
            key=key,
            size_bytes=size,
            content_type=content_type,
            checksum_sha256=digest.hexdigest(),
        )

    def get(self, key: str) -> BinaryIO:
        object_key = self._object_key(key)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=object_key)
        except Exception as exc:
            if self._is_missing(exc):
                raise FileError(f"No stored object for key {key!r}.") from exc
            raise FileError(f"Could not read object: {type(exc).__name__}.") from exc
        return io.BufferedReader(_Body(response["Body"]))  # type: ignore[return-value]

    def delete(self, key: str) -> None:
        object_key = self._object_key(key)
        try:
            self.client.delete_object(Bucket=self.bucket, Key=object_key)
        except Exception as exc:
            if self._is_missing(exc):
                return
            raise FileError(f"Could not delete object: {type(exc).__name__}.") from exc

    def exists(self, key: str) -> bool:
        object_key = self._object_key(key)
        try:
            self.client.head_object(Bucket=self.bucket, Key=object_key)
        except Exception as exc:
            if self._is_missing(exc):
                return False
            raise FileError(f"Could not check object: {type(exc).__name__}.") from exc
        return True


@lru_cache(maxsize=1)
def _client(endpoint_url: str, region: str) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.session.Session().client(
        "s3",
        region_name=region or None,
        endpoint_url=endpoint_url or None,
        config=Config(
            connect_timeout=3,
            read_timeout=10,
            retries={"mode": "standard", "max_attempts": 3},
            # Path-style only where an explicit endpoint (an S3-compatible
            # store) is configured; AWS itself uses virtual-hosted style.
            s3={"addressing_style": "path" if endpoint_url else "auto"},
        ),
    )


def s3_store() -> S3FileStore:
    from django.conf import settings

    return S3FileStore(
        _client(settings.S3_ENDPOINT_URL, settings.S3_REGION),
        bucket=settings.S3_BUCKET,
        prefix=settings.S3_KEY_PREFIX,
        sse=settings.S3_SSE,
        kms_key_id=settings.S3_KMS_KEY_ID,
    )
