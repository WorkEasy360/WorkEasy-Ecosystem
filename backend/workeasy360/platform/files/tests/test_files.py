"""Files: private by default, tenant-scoped, and not servable until scanned."""

from __future__ import annotations

import io
import uuid

import pytest

from workeasy360.contracts.enums import FileStatus
from workeasy360.contracts.errors import (
    FileNotServable,
    FileTooLarge,
    UnsupportedFileType,
)
from workeasy360.platform.files import services
from workeasy360.platform.files.models import File

pytestmark = [pytest.mark.django_db, pytest.mark.security]

PDF = b"%PDF-1.7\n" + b"x" * 200


@pytest.fixture(autouse=True)
def _tmp_storage(settings, tmp_path):
    settings.FILE_STORAGE_ROOT = tmp_path / "files"
    settings.FILE_STORAGE_BACKEND = "local"
    return settings.FILE_STORAGE_ROOT


# --- validation -----------------------------------------------------------


def test_allowed_type_is_accepted():
    services.validate_upload(
        content_type="application/pdf", size_bytes=1024, filename="a.pdf"
    )


@pytest.mark.parametrize(
    "content_type",
    ["image/svg+xml", "text/html", "application/x-msdownload", "text/javascript"],
)
def test_executable_capable_types_are_rejected(content_type):
    """SVG is XML that can carry script, so it is treated as executable and
    rejected rather than served (12-security.md section 10)."""
    with pytest.raises(UnsupportedFileType):
        services.validate_upload(content_type=content_type, size_bytes=1024, filename="x")


def test_unknown_type_is_rejected():
    """An allowlist, not a denylist. A denylist is always incomplete, and the
    gap is whatever the attacker tries next."""
    with pytest.raises(UnsupportedFileType):
        services.validate_upload(
            content_type="application/x-custom", size_bytes=10, filename="x"
        )


def test_oversized_upload_is_rejected(settings):
    settings.FILE_MAX_BYTES = 1024
    with pytest.raises(FileTooLarge):
        services.validate_upload(
            content_type="application/pdf", size_bytes=2048, filename="a.pdf"
        )


def test_empty_upload_is_rejected():
    with pytest.raises(FileTooLarge):
        services.validate_upload(
            content_type="application/pdf", size_bytes=0, filename="a.pdf"
        )


# --- filename and key safety ---------------------------------------------


@pytest.mark.parametrize(
    "raw,expect_absent",
    [
        ("../../etc/passwd", "/"),
        ("..\\..\\windows\\system32", "\\"),
        ("report\x00.pdf", "\x00"),
    ],
)
def test_filename_is_sanitised(raw, expect_absent):
    safe = services.safe_filename(raw)
    assert expect_absent not in safe
    assert safe


def test_filename_is_never_used_as_a_storage_key():
    """Keys are server-generated and tenant-prefixed. A user-controlled key
    is a path-traversal and cross-tenant-read primitive."""
    org_id = uuid.uuid4()
    file_id = uuid.uuid4()
    key = services.build_storage_key(org_id, "crm", file_id)

    assert key == f"org/{org_id}/crm/{file_id}"
    assert ".." not in key


def test_storage_key_module_segment_is_sanitised():
    key = services.build_storage_key(uuid.uuid4(), "../evil", uuid.uuid4())
    assert ".." not in key


def test_store_refuses_a_key_outside_the_root(tmp_path):
    from workeasy360.contracts.errors import FileError
    from workeasy360.platform.files.storage import LocalFileStore

    store = LocalFileStore(tmp_path)
    with pytest.raises(FileError):
        store.put("../escape", io.BytesIO(b"x"), content_type="text/plain")


# --- content sniffing -----------------------------------------------------


def test_content_type_is_detected_from_magic_bytes():
    """The declared extension and Content-Type are attacker-controlled; the
    bytes are not."""
    assert services.detect_content_type(PDF) == "application/pdf"
    assert services.detect_content_type(b"\x89PNG\r\n\x1a\n") == "image/png"
    assert services.detect_content_type(b"not-a-known-format") == ""


def test_content_that_disagrees_with_its_declaration_is_rejected(two_tenants, in_tenant):
    """Declared PDF, actually HTML. Stored, sniffed, then rejected and
    removed."""
    a = two_tenants["a"]

    with in_tenant(a["ctx"]):
        file_id, _ = services.register_upload(
            a["ctx"],
            filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=100,
            module="books",
        )
        # A polyglot that sniffs as an archive would be caught the same way;
        # here the declared type is simply a lie.
        with pytest.raises(UnsupportedFileType):
            services.store_content(
                a["ctx"], file_id, io.BytesIO(b"<html><script>alert(1)</script>")
            )


# --- private by default ---------------------------------------------------


def test_newly_registered_file_is_not_servable(two_tenants, in_tenant):
    """Private by default, enforced by the initial status rather than by
    remembering to set one."""
    a = two_tenants["a"]

    with in_tenant(a["ctx"]):
        file_id, _ = services.register_upload(
            a["ctx"],
            filename="a.pdf",
            content_type="application/pdf",
            size_bytes=len(PDF),
            module="books",
        )
        record = File.objects.get(id=file_id)
        assert record.status == FileStatus.PENDING_UPLOAD.value
        assert not record.is_servable

        with pytest.raises(FileNotServable):
            services.download_url(a["ctx"], file_id)


def test_quarantined_file_is_not_servable(two_tenants, in_tenant):
    """Nothing is servable until scanning completes."""
    a = two_tenants["a"]

    with in_tenant(a["ctx"]):
        file_id, _ = services.register_upload(
            a["ctx"],
            filename="a.pdf",
            content_type="application/pdf",
            size_bytes=len(PDF),
            module="books",
        )
        services.store_content(a["ctx"], file_id, io.BytesIO(PDF))

        record = File.objects.get(id=file_id)
        assert record.status == FileStatus.QUARANTINED.value
        with pytest.raises(FileNotServable):
            services.download_url(a["ctx"], file_id)


def test_clean_file_becomes_servable(two_tenants, in_tenant):
    a = two_tenants["a"]

    with in_tenant(a["ctx"]):
        file_id, _ = services.register_upload(
            a["ctx"],
            filename="a.pdf",
            content_type="application/pdf",
            size_bytes=len(PDF),
            module="books",
        )
        services.store_content(a["ctx"], file_id, io.BytesIO(PDF))
        services.mark_scanned(file_id, services.NullScanner().scan("key"))

        url = services.download_url(a["ctx"], file_id)

    assert "sig=" in url and "expires=" in url


def test_infected_file_content_is_deleted(two_tenants, in_tenant):
    from workeasy360.contracts.ports.files import ScanResult

    a = two_tenants["a"]

    with in_tenant(a["ctx"]):
        file_id, _ = services.register_upload(
            a["ctx"],
            filename="a.pdf",
            content_type="application/pdf",
            size_bytes=len(PDF),
            module="books",
        )
        services.store_content(a["ctx"], file_id, io.BytesIO(PDF))
        services.mark_scanned(
            file_id, ScanResult(clean=False, scanner="test", signature="EICAR")
        )

        record = File.objects.get(id=file_id)
        assert record.status == FileStatus.INFECTED.value
        assert not record.is_servable


# --- signed URLs ----------------------------------------------------------


def test_signature_covers_both_key_and_expiry(settings):
    """Signing only the key lets a holder edit the expiry into a permanent
    URL; signing only the expiry lets them swap in another tenant's key."""
    from workeasy360.platform.files.storage import sign_key, verify_signature

    settings.FILE_URL_SIGNING_KEY = "x" * 40
    key = "org/abc/books/def"
    expires = 2**31

    signature = sign_key(key, expires)
    assert verify_signature(key, expires, signature)
    assert not verify_signature("org/other/books/def", expires, signature)
    assert not verify_signature(key, expires + 1, signature)


def test_expired_signature_is_rejected(settings):
    from workeasy360.platform.files.storage import sign_key, verify_signature

    settings.FILE_URL_SIGNING_KEY = "x" * 40
    key = "org/abc/books/def"
    expires = 1  # 1970

    assert not verify_signature(key, expires, sign_key(key, expires))


# --- tenant isolation -----------------------------------------------------


def test_files_are_tenant_scoped(two_tenants, in_tenant):
    a, b = two_tenants["a"], two_tenants["b"]

    with in_tenant(a["ctx"]):
        services.register_upload(
            a["ctx"],
            filename="a.pdf",
            content_type="application/pdf",
            size_bytes=10,
            module="books",
        )
    with in_tenant(b["ctx"]):
        assert File.objects.count() == 0


def test_another_tenants_file_id_is_not_found(two_tenants, in_tenant):
    a, b = two_tenants["a"], two_tenants["b"]

    with in_tenant(a["ctx"]):
        file_id, _ = services.register_upload(
            a["ctx"],
            filename="a.pdf",
            content_type="application/pdf",
            size_bytes=10,
            module="books",
        )

    with in_tenant(b["ctx"]):
        with pytest.raises(File.DoesNotExist):
            services.download_url(b["ctx"], file_id)


def test_key_prefix_is_verified_before_signing(two_tenants, in_tenant):
    """Signing an arbitrary key would be a cross-tenant read wearing a valid
    signature."""
    a = two_tenants["a"]

    with in_tenant(a["ctx"]):
        file_id, _ = services.register_upload(
            a["ctx"],
            filename="a.pdf",
            content_type="application/pdf",
            size_bytes=len(PDF),
            module="books",
        )
        record = File.objects.get(id=file_id)
        assert record.key_belongs_to_tenant()

        record.storage_key = f"org/{uuid.uuid4()}/books/{uuid.uuid4()}"
        assert not record.key_belongs_to_tenant()
        record.save(update_fields=["storage_key", "updated_at"])

        with pytest.raises(FileNotServable, match="does not belong"):
            services.download_url(a["ctx"], file_id)
