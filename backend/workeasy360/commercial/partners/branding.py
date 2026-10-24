"""White-label branding: bounded presentation metadata (Phase 7, Task 29-31, 82-84).

**Branding is presentation and only presentation.** It never reaches
authorization, entitlement, tenant resolution, seat accounting or security
policy, and it cannot carry markup: every field is a short validated string, a
hex colour over a fixed token set, or a reference to a file held by the shared
``files`` platform. There is no free HTML, no free CSS and no
partner-supplied URL that the platform - or a visitor's browser, as an image -
would fetch (a remote image URL is a tracking pixel and an exfiltration
channel, so the only images are files we hold).

The logo goes through the platform's own file controls unchanged: type
allowlist (PNG and JPEG only here - **SVG is refused**: it is XML that can
carry script), size ceiling, magic-byte verification, scanning state. A logo
is attachable only once the file is ``clean``.
"""

from __future__ import annotations

import re
from typing import Any, Final
from urllib.parse import urlsplit
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from workeasy360.commercial.partners import access, selectors
from workeasy360.commercial.partners import portal as portal_fields
from workeasy360.commercial.partners.domain import clean_text
from workeasy360.commercial.partners.inputs import Problems
from workeasy360.contracts.errors import PartnerRecordNotFound, PartnerValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

MANAGE = "platform.partner_branding.manage"
VIEW = "platform.partner_branding.view"

#: The only colour tokens a theme may set. A closed set: an unknown token is
#: refused, not ignored, so a typo does not silently do nothing.
THEME_TOKENS: Final = (
    "primary",
    "on_primary",
    "surface",
    "on_surface",
    "accent",
    "border",
)

LOGO_MODULE: Final = "partner_brand"
LOGO_MAX_BYTES: Final = 512 * 1024
LOGO_TYPES: Final = frozenset({"image/png", "image/jpeg"})

_HEX_RE: Final = re.compile(r"^#[0-9a-f]{6}$")


def validate_color(value: Any, *, field: str) -> str:
    text = str(value or "").strip().lower()
    if not _HEX_RE.match(text):
        raise PartnerValidationError(
            f"{field} must be a colour like #1a2b3c.", field=field
        )
    return text


def validate_theme(value: Any) -> dict[str, str]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise PartnerValidationError("theme must be an object.", field="theme")
    unknown = sorted(set(value) - set(THEME_TOKENS))
    if unknown:
        raise PartnerValidationError(
            f"Unknown theme tokens {unknown}. Allowed: {list(THEME_TOKENS)}.",
            field="theme",
        )
    return {
        token: validate_color(value[token], field=f"theme.{token}") for token in value
    }


def validate_support_url(value: Any) -> str:
    """An ``https`` link shown to visitors. Stored and rendered, never fetched."""
    text = clean_text(value, field="support_url", max_length=300, required=False)
    if not text:
        return ""
    try:
        parts = urlsplit(text)
        hostname = parts.hostname
    except ValueError:
        # "https://[bad/" - urlsplit raises; that is a bad address, not a 500
        # (Phase 12, G-23).
        raise PartnerValidationError(
            "support_url must be a plain https:// address.", field="support_url"
        ) from None
    if (
        parts.scheme != "https"
        or not hostname
        or parts.username
        or parts.password
        or " " in text
    ):
        raise PartnerValidationError(
            "support_url must be a plain https:// address.", field="support_url"
        )
    return text


def validate_support_email(value: Any) -> str:
    text = clean_text(value, field="support_email", max_length=254, required=False)
    if not text:
        return ""
    try:
        validate_email(text)
    except ValidationError as exc:
        raise PartnerValidationError(
            "support_email is not a valid address.", field="support_email"
        ) from exc
    return text


def get_brand(ctx: TenantContext) -> dict[str, Any] | None:
    authz.require(ctx, VIEW)
    row = selectors.brand_for(ctx.partner_id)
    return selectors.brand_dict(row) if row is not None else None


@in_scope
def update_brand(
    ctx: TenantContext,
    *,
    brand_name: Any,
    accent_color: Any = "",
    theme: Any = None,
    support_email: Any = "",
    support_url: Any = "",
    logo_file_id: UUID | None = None,
    expected_version: int | None = None,
    partner_id: UUID | None = None,
    portal: Any = None,
) -> dict[str, Any]:
    """Create or replace the partner's brand.

    Validated in full **before** the first write. ``expected_version`` is the
    optimistic token: on an existing brand it must match, so two partner
    administrators editing at once cannot silently overwrite each other; the
    first save of a brand takes none.

    ``partner_id`` is for staff preparing a partner's brand for it (Phase 10d);
    a partner user never passes one. The same validation, the same version
    token and the same audit entry apply - only who may call it differs
    (``access.subject_partner``).

    ``portal`` (the customer-portal words and sender identity,
    ``portal.PORTAL_FIELDS``) is **merged**: the keys sent replace those keys and
    every other stored key stays, so one screen saving its own fields never
    wipes another's. Left out, the stored portal is untouched.
    """
    from workeasy360.commercial.partners.models import PartnerBrand

    subject = access.subject_partner(ctx, partner_id, partner_permission=MANAGE)

    clean_name = clean_text(brand_name, field="brand_name", max_length=80)
    accent = validate_color(accent_color, field="accent_color") if accent_color else ""
    clean_theme = validate_theme(theme)
    email = validate_support_email(support_email)
    url = validate_support_url(support_url)
    portal_problems = Problems()
    clean_portal = portal_fields.parse(portal, portal_problems)
    portal_problems.first()
    if logo_file_id is not None:
        _assert_logo_usable(ctx, logo_file_id, subject)

    with transaction.atomic():
        existing = (
            PartnerBrand.objects.select_for_update().filter(partner_id=subject).first()
        )
        if existing is None:
            if expected_version not in (None, 0):
                raise PartnerRecordNotFound("No brand to update.")
            brand = PartnerBrand(partner_id=subject, updated_by_id=ctx.user_id)
        else:
            if expected_version is None or existing.version != expected_version:
                from workeasy360.commercial.partners.services import (
                    ConcurrentPartnerModification,
                )

                raise ConcurrentPartnerModification(
                    "The brand changed since you read it. Re-read it and retry.",
                    expected_version=expected_version,
                    actual_version=existing.version,
                )
            brand = existing
            brand.version += 1

        brand.brand_name = clean_name
        brand.accent_color = accent
        brand.theme = clean_theme
        brand.support_email = email
        brand.support_url = url
        brand.logo_file_id = logo_file_id
        if clean_portal:
            brand.portal = {**(brand.portal or {}), **clean_portal}
        brand.updated_by_id = ctx.user_id
        brand.save()
        audit.record(
            ctx,
            "partner.branding_changed",
            resource_type="partners.brand",
            resource_id=brand.id,
            # What changed, never contact values.
            changes={
                "partner_id": str(subject),
                "brand_name": clean_name,
                "has_logo": logo_file_id is not None,
                "theme_tokens": sorted(clean_theme),
                "portal_fields": sorted(clean_portal),
                "version": brand.version,
            },
        )
    return selectors.brand_dict(brand)


def public_logo(file_id: str | None) -> tuple[bytes, str] | None:
    """The bytes and content type of a brand's logo, or ``None``.

    Served by the platform itself, same-origin, rather than through a signed
    object-store URL: the production CSP allows images only from ``'self'`` and
    ``data:``, and a cross-origin URL would be blocked. The files platform
    still applies every guard (scanned clean, tenant key prefix, size ceiling).
    Any failure is "no logo": a missing image must not break the sign-in page it
    decorates (Task 118).
    """
    if not file_id:
        return None
    try:
        from uuid import UUID

        from workeasy360.platform.authz.control_plane import (
            acting_scope,
            platform_context,
        )
        from workeasy360.platform.files import selectors as file_selectors

        ctx = platform_context(user_id=UUID(int=0))
        with acting_scope(ctx):
            return file_selectors.servable_content(
                UUID(str(file_id)), max_bytes=LOGO_MAX_BYTES
            )
    except Exception:
        return None


@in_scope
def upload_logo(
    ctx: TenantContext,
    *,
    content: bytes,
    content_type: str,
    partner_id: UUID | None = None,
) -> UUID:
    """Store a logo through the shared files platform and record whose it is.

    The bytes are verified against the declared type by the files platform
    (magic bytes must match), the size is capped here, and the file is scanned
    before it can be attached. Returns the file id to pass to ``update_brand``.
    """
    import io

    from workeasy360.commercial.partners.models import PartnerLogoUpload
    from workeasy360.platform.authz.control_plane import acting_scope
    from workeasy360.platform.files import services as files

    subject = access.subject_partner(ctx, partner_id, partner_permission=MANAGE)
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared not in LOGO_TYPES:
        raise PartnerValidationError(
            "A logo must be a PNG or a JPEG image. SVG is not accepted.",
            field="content_type",
        )
    if not content or len(content) > LOGO_MAX_BYTES:
        raise PartnerValidationError(
            f"A logo must be between 1 byte and {LOGO_MAX_BYTES // 1024} KB.",
            field="content",
        )

    with acting_scope(ctx), transaction.atomic():
        file_id, _grant = files.register_upload(
            ctx,
            filename="logo.png" if declared == "image/png" else "logo.jpg",
            content_type=declared,
            size_bytes=len(content),
            module=LOGO_MODULE,
        )
        files.store_content(ctx, file_id, io.BytesIO(content))
        files.scan_now(file_id)
        PartnerLogoUpload.objects.create(
            file_id=file_id, partner_id=subject, uploaded_by_id=ctx.user_id
        )
        audit.record(
            ctx,
            "partner.logo_uploaded",
            resource_type="partners.logo",
            resource_id=file_id,
            changes={
                "partner_id": str(subject),
                "size_bytes": len(content),
                "content_type": declared,
            },
        )
    return file_id


def _assert_logo_usable(ctx: TenantContext, file_id: UUID, partner_id: UUID) -> None:
    """The file must be this partner's own upload, a small PNG/JPEG, and clean."""
    from workeasy360.commercial.partners.models import PartnerLogoUpload
    from workeasy360.platform.authz.control_plane import acting_scope
    from workeasy360.platform.files import selectors as file_selectors

    owned = PartnerLogoUpload.objects.filter(
        file_id=file_id, partner_id=partner_id
    ).exists()
    with acting_scope(ctx):
        meta = file_selectors.file_facts(file_id) if owned else None
    if meta is None:
        # One message whether the id is unknown or belongs to another partner.
        raise PartnerValidationError("No such logo file.", field="logo_file_id")
    problems = []
    if meta["module"] != LOGO_MODULE:
        problems.append("it was not uploaded as a partner logo")
    if meta["detected_content_type"] not in LOGO_TYPES:
        problems.append("it is not a PNG or JPEG image")
    if meta["size_bytes"] > LOGO_MAX_BYTES:
        problems.append(f"it is larger than {LOGO_MAX_BYTES // 1024} KB")
    if not meta["is_servable"]:
        problems.append("it has not been scanned clean")
    if problems:
        raise PartnerValidationError(
            "That file cannot be used as a logo: " + "; ".join(problems) + ".",
            field="logo_file_id",
        )
