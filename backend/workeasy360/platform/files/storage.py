"""Storage backends behind the ``FileStore`` port.

Phase 1 shipped a local filesystem backend; Phase 12 adds the S3 adapter the
production settings had always required (``storage_s3.py``) - before it, a
production deployment could only start with ``W360_FILE_STORAGE_BACKEND=s3``
and then failed every file operation, because this factory had no adapter to
return.

Signed URLs are HMAC-signed *application* URLs for every backend, not S3
presigned URLs: content is always served through the platform, which applies
the tenant-prefix and scanned-clean checks on every read and keeps a partner
logo same-origin for the CSP. Both adapters share that scheme
(:class:`SignedApplicationUrls`).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO, Final

from workeasy360.contracts.errors import FileError
from workeasy360.contracts.ports.files import (
    DEFAULT_URL_TTL,
    FileStore,
    PresignedUpload,
    StoredObject,
)

#: Keys are server-generated, but this is validated anyway. Defence in depth
#: costs one regex and removes an entire bug class.
_SAFE_KEY_RE: Final = re.compile(
    r"^org/[0-9a-f-]{36}/[a-z_]+/[0-9a-f-]{36}(\.[a-z0-9]{1,10})?$"
)


def validate_key(key: str) -> None:
    """Refuse any key that is not a server-generated, tenant-prefixed key."""
    if not _SAFE_KEY_RE.match(key):
        raise FileError(
            f"Refusing unsafe storage key {key!r}. Keys are "
            f"server-generated as org/<org>/<module>/<uuid>."
        )


class SignedApplicationUrls:
    """Short-lived HMAC-signed application URLs, shared by every adapter."""

    def presign_download(self, key: str, *, ttl: timedelta = DEFAULT_URL_TTL) -> str:
        validate_key(key)
        expires = int(time.time() + ttl.total_seconds())
        return f"/api/v1/files/content?key={key}&expires={expires}&sig={sign_key(key, expires)}"

    def presign_upload(
        self,
        key: str,
        *,
        content_type: str,
        max_bytes: int,
        ttl: timedelta = DEFAULT_URL_TTL,
    ) -> PresignedUpload:
        validate_key(key)
        expires = int(time.time() + ttl.total_seconds())
        return PresignedUpload(
            url="/api/v1/files/upload",
            method="PUT",
            fields={
                "key": key,
                "expires": str(expires),
                "sig": sign_key(key, expires),
                "content_type": content_type,
            },
            key=key,
            expires_at=datetime.fromtimestamp(expires, tz=timezone.utc),
            max_bytes=max_bytes,
        )


class LocalFileStore(SignedApplicationUrls):
    """Filesystem-backed store for development and tests.

    Not durable across container restarts, which production settings refuse
    to allow (``W360_FILE_STORAGE_BACKEND=local`` fails the production config
    check).
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        validate_key(key)
        # Resolve and confirm containment. Even with the regex above, this is
        # the check that actually stops traversal, because it operates on the
        # resolved path rather than on the string.
        path = (self.root / key).resolve()
        if not str(path).startswith(str(self.root.resolve())):
            raise FileError("Storage key escapes the storage root.")
        return path

    def put(self, key: str, content: BinaryIO, *, content_type: str) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)

        digest = hashlib.sha256()
        size = 0
        with path.open("wb") as handle:
            # Chunked rather than read(): an unbounded read of an
            # attacker-sized upload is a memory-exhaustion primitive.
            while chunk := content.read(64 * 1024):
                digest.update(chunk)
                size += len(chunk)
                handle.write(chunk)

        return StoredObject(
            key=key,
            size_bytes=size,
            content_type=content_type,
            checksum_sha256=digest.hexdigest(),
        )

    def get(self, key: str) -> BinaryIO:
        path = self._path(key)
        if not path.exists():
            raise FileError(f"No stored object for key {key!r}.")
        return path.open("rb")

    def delete(self, key: str) -> None:
        path = self._path(key)
        path.unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


def _signing_key() -> bytes:
    from django.conf import settings

    return str(getattr(settings, "FILE_URL_SIGNING_KEY", "")).encode("utf-8")


def sign_key(key: str, expires: int) -> str:
    """HMAC over the key and its expiry.

    Both are signed. Signing only the key would let a holder edit the expiry
    and hold a permanent URL; signing only the expiry would let them swap the
    key for another tenant's.
    """
    message = f"{key}:{expires}".encode()
    digest = hmac.new(_signing_key(), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def verify_signature(key: str, expires: int, signature: str) -> bool:
    """Constant-time verification, expiry first."""
    if expires < time.time():
        return False
    # compare_digest, not ==: a byte-by-byte comparison leaks the correct
    # prefix through timing and makes forging a signature tractable.
    return hmac.compare_digest(sign_key(key, expires), signature)


def get_store() -> FileStore:
    from django.conf import settings

    backend = getattr(settings, "FILE_STORAGE_BACKEND", "local")
    if backend == "local":
        return LocalFileStore(settings.FILE_STORAGE_ROOT)
    if backend == "s3":
        from workeasy360.platform.files.storage_s3 import s3_store

        return s3_store()
    raise FileError(
        f"Storage backend {backend!r} is configured but has no adapter. "
        f"Implement it against contracts.ports.files.FileStore."
    )
