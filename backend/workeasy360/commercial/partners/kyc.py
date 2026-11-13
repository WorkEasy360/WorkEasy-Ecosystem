"""Partner KYC: the application a partner must have approved before it may act.

**Two separate status systems, on purpose.** ``PartnerAccount.status`` says whether
a partner may operate (``draft``, ``active``, ``suspended``, ``terminated``). KYC
status says where its application stands: ``draft``, ``submitted``, ``under_review``,
``correction_required``, ``approved``, ``rejected``. One never stands in for the other:
a suspended partner's KYC stays approved, and suspension cancels nothing of its
customers'.

**The rule.** Registering grants nothing. A draft partner holds no permission at all
(``resolver.py``), and leaving draft needs an approved application
(``assert_may_activate``, called by the staff status command and by a parent partner
activating a sub-partner). Approval records the decision and the capabilities the
partner asked for; an Admin holding ``platform.staff_partner.manage`` then activates
it. KYC approved + authorized activation = active partner (2026-11-10).

**A partner that was already active when this arrived stays active.** Its KYC reads
``draft``: marking it approved would forge a decision nobody made, and every decision
here records a real reviewer, date and reason. The queue can list them.

**Requirements are data.** Which documents and fields are required is what an Admin
entered (``KycRequirement``); nothing here names one. With none entered, an application
is business details alone.

**Documents are restricted.** Held by the files platform under the platform's own
scope, never given a public URL, and read only through ``document_content``: staff
holding ``platform.staff_kyc.review``, or the partner's own administrators. Every read
by staff writes a row in the access log and an audit entry, whether or not anyone
ever looks at it. There is no malware scanner behind the files platform today
(``NullScanner``): a reviewer should treat a document as untrusted.
"""

from __future__ import annotations

import io
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from workeasy360.commercial.partners import selectors
from workeasy360.commercial.partners.domain import clean_text
from workeasy360.contracts.enums import PartnerStatus, PrincipalKind
from workeasy360.contracts.errors import (
    InvalidStateTransition,
    PartnerRecordNotFound,
    PartnerValidationError,
    PermissionDenied,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import acting_scope, in_scope
from workeasy360.platform.observability.metrics import metrics

VIEW = "platform.staff_kyc.view"
REVIEW = "platform.staff_kyc.review"

OPERATIONS = "partner_kyc_operations_total"

#: What a KYC document may be: the bytes are checked against this by the files
#: platform as well, never only the declared type.
DOCUMENT_TYPES = frozenset({"application/pdf", "image/png", "image/jpeg"})
DOCUMENT_MAX_BYTES = 10 * 1024 * 1024
FILES_MODULE = "partner_kyc"

#: The business details an application carries. Everything else in a body is
#: ignored, so a client cannot set a status or a reviewer by adding a field.
DETAIL_FIELDS = {
    "legal_name": 200,
    "business_type": 64,
    "registration_number": 64,
    "tax_number": 64,
    "address_line": 300,
    "city": 100,
    "region": 100,
    "postal_code": 16,
    "country": 2,
    "website": 300,
    "phone": 32,
}

DECISIONS = {
    "approve": "approved",
    "request_correction": "correction_required",
    "reject": "rejected",
}

#: Where each decision may be taken from.
DECIDABLE = ("submitted", "under_review")

_WORDS = {
    "approved": "application approved",
    "correction_required": "corrections requested",
    "rejected": "application rejected",
    "submitted": "application submitted",
}


def required() -> bool:
    """Whether activation needs an approved application. On everywhere except the
    older test suites, whose fixtures predate it; production refuses to start with
    it off (``config/settings/production.py``)."""
    return bool(getattr(settings, "PARTNER_KYC_REQUIRED", True))


def _models() -> Any:
    from workeasy360.commercial.partners import models

    return models


def _record(partner_id: UUID, *, lock: bool = False) -> Any:
    """This partner's application, created in draft the first time it is asked for."""
    m = _models()
    if not m.PartnerAccount.objects.filter(id=partner_id).exists():
        raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))
    m.PartnerKyc.objects.get_or_create(partner_id=partner_id)
    rows = m.PartnerKyc.objects
    if lock:
        rows = rows.select_for_update()
    return rows.get(partner_id=partner_id)


def status_of(partner_id: UUID) -> str:
    """The application's status; ``draft`` when none was ever started."""
    m = _models()
    row = m.PartnerKyc.objects.filter(partner_id=partner_id).values("status").first()
    return row["status"] if row else "draft"


def statuses_for(partner_ids: Any) -> dict[UUID, str]:
    """Each partner's application status in one query; ``draft`` when none was
    ever started."""
    ids = list(partner_ids)
    if not ids:
        return {}
    m = _models()
    found = dict(
        m.PartnerKyc.objects.filter(partner_id__in=ids).values_list(
            "partner_id", "status"
        )
    )
    return {pid: found.get(pid, "draft") for pid in ids}


def standing(partner_status: str, kyc_status: str) -> dict[str, Any]:
    """What a list or record page says about a partner's KYC beside its status.

    ``legacy`` marks a partner that is active or suspended without an approved
    application. Since activation needs one, such a partner was activated before
    KYC was required (or on a development database with the gate off). It keeps
    its status: approving it now would forge a review nobody made, so the page says
    so instead of showing nothing. ``ready_to_activate`` is a draft whose
    application is approved, waiting for an Admin's activation."""
    return {
        "status": kyc_status,
        "legacy": partner_status in ("active", "suspended") and kyc_status != "approved",
        "ready_to_activate": partner_status == "draft" and kyc_status == "approved",
        "required": required(),
    }


def legacy_restricted() -> bool:
    """Whether the Admin chose to confine partners made active before KYC existed
    to onboarding until their application is approved (ADR-063). Off by default,
    which keeps them trading as before; never an approval written on their behalf."""
    if not required():
        return False
    from workeasy360.platform.administration.site import partner_policies

    return partner_policies().get("legacy_kyc") == "restrict"


def cleared(partner_id: UUID, *, status: str) -> bool:
    """May this partner trade now: referral links and attribution, customers,
    plan activations, the Platform Wallet, payout requests (ADR-063)?

    Only an **active** partner. With KYC required, activation already needs an
    approved application, so an active partner without one is legacy: cleared
    unless the Admin's policy restricts legacy partners, in which case only an
    approved application clears it."""
    if status != "active":
        return False
    if not legacy_restricted():
        return True
    return status_of(partner_id) == "approved"


def onboarding_state(partner_id: UUID, *, status: str) -> dict[str, Any]:
    """What the partner portal shows while a partner cannot trade yet: its KYC and
    account states and the reason it is confined to onboarding, if it is."""
    kyc_status = status_of(partner_id)
    is_cleared = cleared(partner_id, status=status)
    if is_cleared:
        reason = None
    elif status == "draft":
        reason = "kyc_pending" if kyc_status != "approved" else "activation_pending"
    elif status == "active":
        reason = "legacy_kyc_required"
    else:
        reason = f"account_{status}"
    return {
        "kyc_status": kyc_status,
        "account_status": status,
        "cleared": is_cleared,
        "reason": reason,
        "legacy": standing(status, kyc_status)["legacy"],
        "kyc_required": required(),
    }


ACTIVATION_NEEDS_KYC = "KYC approval"


def activation_gap(partner_id: UUID) -> dict[str, str] | None:
    """The activation checklist's KYC line for a draft partner, or ``None`` when
    nothing about KYC stands in the way."""
    if not required():
        return None
    status = status_of(partner_id)
    if status == "approved":
        return None
    words = {
        "draft": "the partner has not sent its KYC application yet",
        "submitted": "the KYC application is waiting for review",
        "under_review": "the KYC application is being reviewed",
        "correction_required": "the partner has to correct its KYC application",
        "rejected": "the KYC application was rejected",
    }
    return {
        "key": "kyc",
        "label": ACTIVATION_NEEDS_KYC,
        "detail": words.get(status, "the KYC application is not approved"),
    }


def assert_may_activate(partner_id: UUID) -> None:
    """Refuse a draft partner's activation until its application is approved.

    The one rule every activation path calls: the staff status command, a parent
    partner activating a sub-partner, and nothing else may move a partner out of
    draft. Draft, submitted, under review, correction required and rejected are all
    refused; only ``approved`` passes."""
    if not required():
        return
    if status_of(partner_id) != "approved":
        raise PartnerValidationError(
            "This partner's KYC must be approved before it can be activated.",
            field="kyc",
        )


# --- requirements (what an Admin asks for) -----------------------------------------


def requirement_json(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "key": row.key,
        "label": row.label,
        "help_text": row.help_text,
        "is_required": row.is_required,
        "is_active": row.is_active,
        "display_order": row.display_order,
    }


def _active_requirements() -> list[Any]:
    m = _models()
    return list(
        m.KycRequirement.objects.filter(is_active=True).order_by("display_order", "label")
    )


def requirements(ctx: TenantContext) -> list[dict[str, Any]]:
    authz.require(ctx, VIEW)
    m = _models()
    return [
        requirement_json(row)
        for row in m.KycRequirement.objects.order_by("display_order", "label")
    ]


def _clean_requirement(body: dict[str, Any]) -> dict[str, Any]:
    label = clean_text(body.get("label"), max_length=120, field="label")
    order = body.get("display_order", 0)
    if isinstance(order, bool) or not isinstance(order, int) or not 0 <= order <= 999:
        raise PartnerValidationError("Order must be 0 to 999.", field="display_order")
    return {
        "label": label,
        "help_text": clean_text(
            body.get("help_text"), max_length=300, field="help_text", required=False
        ),
        "is_required": bool(body.get("is_required", True)),
        "is_active": bool(body.get("is_active", True)),
        "display_order": order,
    }


def _key_from(label: str) -> str:
    import re

    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40] or "document"
    m = _models()
    taken = set(
        m.KycRequirement.objects.filter(key__startswith=base).values_list(
            "key", flat=True
        )
    )
    n = 1
    while True:
        candidate = base if n == 1 else f"{base}_{n}"
        if candidate not in taken:
            return candidate
        n += 1


@in_scope
def create_requirement(ctx: TenantContext, body: dict[str, Any]) -> dict[str, Any]:
    authz.require(ctx, REVIEW)
    m = _models()
    fields = _clean_requirement(body)
    with transaction.atomic():
        row = m.KycRequirement.objects.create(key=_key_from(fields["label"]), **fields)
        audit.record(
            ctx,
            "partner.kyc_requirement_created",
            resource_type="partners.kyc_requirement",
            resource_id=row.id,
            changes={"key": row.key, "is_required": row.is_required},
        )
    return requirement_json(row)


@in_scope
def update_requirement(
    ctx: TenantContext, requirement_id: UUID, body: dict[str, Any]
) -> dict[str, Any]:
    authz.require(ctx, REVIEW)
    m = _models()
    fields = _clean_requirement(body)
    with transaction.atomic():
        row = (
            m.KycRequirement.objects.select_for_update().filter(id=requirement_id).first()
        )
        if row is None:
            raise PartnerRecordNotFound("No such requirement.")
        for name, value in fields.items():
            setattr(row, name, value)
        row.save()
        audit.record(
            ctx,
            "partner.kyc_requirement_updated",
            resource_type="partners.kyc_requirement",
            resource_id=row.id,
            changes={"is_active": row.is_active, "is_required": row.is_required},
        )
    return requirement_json(row)


# --- what a partner sends -------------------------------------------------------------


def _assert_own_administrator(ctx: TenantContext, partner_id: UUID) -> None:
    """The partner's own owner or administrator, whatever the partner's status: a
    draft partner holds no permission at all, and this is the one thing it must do.
    Nobody else, and a staff guest view is not the partner."""
    from workeasy360.commercial.partners import capabilities
    from workeasy360.commercial.partners.models import PartnerMember

    allowed = (
        ctx.principal is PrincipalKind.PARTNER
        and ctx.partner_id == partner_id
        and not ctx.is_break_glass
        and PartnerMember.objects.filter(
            partner_id=partner_id,
            user_id=ctx.user_id,
            status="active",
            role__in=sorted(capabilities.ADMIN_ROLES),
        ).exists()
    )
    if not allowed:
        raise PermissionDenied(
            "Only this partner's administrators can do that.",
            permission="partner.kyc",
            reason="not_partner_administrator",
        )


def _assert_partner_member(ctx: TenantContext, partner_id: UUID) -> None:
    if ctx.principal is not PrincipalKind.PARTNER or ctx.partner_id != partner_id:
        raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))


def _editable(row: Any) -> None:
    if row.status not in ("draft", "correction_required"):
        raise InvalidStateTransition(
            entity="partners.PartnerKyc", from_status=row.status, to_status="draft"
        )


def _document_json(row: Any, scans: dict[Any, str] | None = None) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "requirement_key": row.requirement_key,
        "filename": row.filename,
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "uploaded_at": row.uploaded_at.isoformat(),
        # The malware scan (2026-11-10): clean, infected, pending (waiting for a
        # scanner's verdict - it cannot be opened yet) or not_required (served on
        # the allowlist check alone, only where the scan rule is off).
        "scan": (scans or {}).get(row.file_id, "pending"),
    }


def _scan_states(ctx: TenantContext, file_ids: Any) -> dict[Any, str]:
    """Each document file's scan state, read in the platform scope the files live in."""
    from workeasy360.platform.files import selectors as file_selectors

    ids = [i for i in file_ids if i]
    if not ids:
        return {}
    with acting_scope(ctx):
        return file_selectors.scan_states(ids)


def unscanned_documents(ctx: TenantContext, partner_id: UUID) -> list[str]:
    """The current documents that have not passed the malware scan, by requirement
    key. Empty when the scan rule is off. Approval waits until this is empty."""
    from workeasy360.platform.files.scanning import is_strict

    if not is_strict(FILES_MODULE):
        return []
    m = _models()
    rows = list(
        m.PartnerKycDocument.objects.filter(
            partner_id=partner_id, removed_at__isnull=True
        )
    )
    scans = _scan_states(ctx, [r.file_id for r in rows])
    return [r.requirement_key for r in rows if scans.get(r.file_id) != "clean"]


def _decision_json(row: Any, names: dict[Any, str] | None = None) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "decision": row.decision,
        "from_status": row.from_status,
        "to_status": row.to_status,
        "reason": row.reason,
        "flagged_requirements": list(row.flagged_requirements or []),
        "decided_by": str(row.decided_by_id) if row.decided_by_id else None,
        "decided_by_name": (names or {}).get(row.decided_by_id, ""),
        "decided_at": row.decided_at.isoformat(),
    }


def _latest_correction(partner_id: UUID) -> dict[str, Any] | None:
    m = _models()
    row = (
        m.PartnerKycDecision.objects.filter(
            partner_id=partner_id, to_status="correction_required"
        )
        .order_by("-decided_at")
        .first()
    )
    return (
        {
            "reason": row.reason,
            "flagged_requirements": list(row.flagged_requirements or []),
            "decided_at": row.decided_at.isoformat(),
        }
        if row
        else None
    )


def application(ctx: TenantContext, partner_id: UUID) -> dict[str, Any]:
    """What a partner sees of its own application. Never who reviewed it.

    A staff guest view (Login as Guest, ADR-062) sees where the application
    stands and nothing it contains: no details (PAN, GSTIN, addresses), no
    document names, no decision text."""
    _assert_partner_member(ctx, partner_id)
    m = _models()
    row = _record(partner_id)
    if ctx.is_break_glass:
        return {
            "status": row.status,
            "version": row.version,
            "details": {},
            "requested_experience": row.requested_experience,
            "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
            "decided_at": row.decided_at.isoformat() if row.decided_at else None,
            "decision_reason": "",
            "correction": None,
            "requirements": [requirement_json(r) for r in _active_requirements()],
            "documents": [],
            "editable": False,
            "may_edit": False,
            "redacted": True,
        }
    documents = list(
        m.PartnerKycDocument.objects.filter(
            partner_id=partner_id, removed_at__isnull=True
        ).order_by("uploaded_at")
    )
    scans = _scan_states(ctx, [d.file_id for d in documents])
    return {
        "status": row.status,
        "version": row.version,
        "details": dict(row.details or {}),
        "requested_experience": row.requested_experience,
        "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "decision_reason": row.decision_reason
        if row.status in ("correction_required", "rejected")
        else "",
        "correction": _latest_correction(partner_id)
        if row.status == "correction_required"
        else None,
        "requirements": [requirement_json(r) for r in _active_requirements()],
        "documents": [_document_json(d, scans) for d in documents],
        "editable": row.status in ("draft", "correction_required"),
        "may_edit": _may_edit(ctx, partner_id),
        # ADR-063 (the specification's 11.1): the decisions made so far, with
        # their reasons and dates - never who made them.
        "history": [
            {
                "decision": d.decision,
                "from_status": d.from_status,
                "to_status": d.to_status,
                "reason": d.reason,
                "decided_at": d.decided_at.isoformat(),
            }
            for d in m.PartnerKycDecision.objects.filter(partner_id=partner_id).order_by(
                "-decided_at"
            )[:50]
        ],
    }


def _may_edit(ctx: TenantContext, partner_id: UUID) -> bool:
    try:
        _assert_own_administrator(ctx, partner_id)
    except PermissionDenied:
        return False
    return True


def _clean_details(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise PartnerValidationError("Send the business details.", field="details")
    details: dict[str, str] = {}
    for name, limit in DETAIL_FIELDS.items():
        details[name] = clean_text(
            raw.get(name), max_length=limit, field=name, required=False
        )
    if details["country"] and not (
        len(details["country"]) == 2 and details["country"].isalpha()
    ):
        raise PartnerValidationError("Choose a country.", field="country")
    details["country"] = details["country"].upper()
    return details


@in_scope
def update_details(
    ctx: TenantContext, partner_id: UUID, raw: Any, *, expected_version: Any = None
) -> dict[str, Any]:
    _assert_own_administrator(ctx, partner_id)
    details = _clean_details(raw)
    with transaction.atomic():
        row = _record(partner_id, lock=True)
        _editable(row)
        _check_version(row, expected_version)
        row.details = details
        row.version += 1
        row.save(update_fields=["details", "version", "updated_at"])
    return application(ctx, partner_id)


def _check_version(row: Any, expected: Any) -> None:
    if expected is None:
        return
    if (
        isinstance(expected, bool)
        or not isinstance(expected, int)
        or expected != row.version
    ):
        raise PartnerValidationError(
            "This application changed in the meantime. Reload and try again.",
            field="expected_version",
        )


@in_scope
def upload_document(
    ctx: TenantContext,
    partner_id: UUID,
    *,
    requirement_key: Any,
    filename: Any,
    content: bytes,
    content_type: str,
) -> dict[str, Any]:
    """One document for one requirement. Replaces the one already there: the old file
    stays held and listed as removed, so what a reviewer saw is never lost."""
    from workeasy360.platform.files import services as files

    _assert_own_administrator(ctx, partner_id)
    m = _models()
    key = str(requirement_key or "").strip()
    requirement = m.KycRequirement.objects.filter(key=key, is_active=True).first()
    if requirement is None:
        raise PartnerValidationError(
            "Choose a document to upload.", field="requirement_key"
        )
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared not in DOCUMENT_TYPES:
        raise PartnerValidationError(
            "A document must be a PDF, a PNG or a JPEG.", field="content_type"
        )
    if not content or len(content) > DOCUMENT_MAX_BYTES:
        raise PartnerValidationError(
            f"A document must be at most {DOCUMENT_MAX_BYTES // (1024 * 1024)} MB.",
            field="content",
        )
    name = str(filename or "document").strip()[:200] or "document"
    with acting_scope(ctx), transaction.atomic():
        row = _record(partner_id, lock=True)
        _editable(row)
        file_id, _grant = files.register_upload(
            ctx,
            filename=name,
            content_type=declared,
            size_bytes=len(content),
            module=FILES_MODULE,
        )
        files.store_content(ctx, file_id, io.BytesIO(content))
        files.scan_now(file_id)
        now = timezone.now()
        m.PartnerKycDocument.objects.filter(
            partner_id=partner_id, requirement_key=key, removed_at__isnull=True
        ).update(removed_at=now)
        document = m.PartnerKycDocument.objects.create(
            partner_id=partner_id,
            requirement_key=key,
            file_id=file_id,
            filename=name,
            content_type=declared,
            size_bytes=len(content),
            uploaded_by_id=ctx.user_id,
        )
        audit.record(
            ctx,
            "partner.kyc_document_uploaded",
            resource_type="partners.kyc_document",
            resource_id=document.id,
            # A key and a size only: never a file name, which can carry a person's.
            changes={
                "partner_id": str(partner_id),
                "requirement": key,
                "size_bytes": len(content),
            },
        )
    return _document_json(document, _scan_states(ctx, [file_id]))


@in_scope
def remove_document(ctx: TenantContext, partner_id: UUID, document_id: UUID) -> None:
    _assert_own_administrator(ctx, partner_id)
    m = _models()
    with transaction.atomic():
        row = _record(partner_id, lock=True)
        _editable(row)
        changed = m.PartnerKycDocument.objects.filter(
            id=document_id, partner_id=partner_id, removed_at__isnull=True
        ).update(removed_at=timezone.now())
        if not changed:
            raise PartnerRecordNotFound("No such document.")
        audit.record(
            ctx,
            "partner.kyc_document_removed",
            resource_type="partners.kyc_document",
            resource_id=document_id,
            changes={"partner_id": str(partner_id)},
        )


def missing_requirements(partner_id: UUID) -> list[str]:
    """The labels of what is still needed before this application can be sent."""
    m = _models()
    held = set(
        m.PartnerKycDocument.objects.filter(
            partner_id=partner_id, removed_at__isnull=True
        ).values_list("requirement_key", flat=True)
    )
    return [
        r.label for r in _active_requirements() if r.is_required and r.key not in held
    ]


def _tell_staff_submitted(partner_id: UUID) -> None:
    from workeasy360.commercial.partners.selectors import partner_names
    from workeasy360.platform.administration.notices import tell_staff

    name = partner_names([partner_id]).get(partner_id, "A partner")
    tell_staff(
        "A partner application is waiting for review",
        f"{name} submitted its KYC application.",
    )


@in_scope
def submit(ctx: TenantContext, partner_id: UUID) -> dict[str, Any]:
    _assert_own_administrator(ctx, partner_id)
    with transaction.atomic():
        row = _record(partner_id, lock=True)
        _editable(row)
        details = row.details or {}
        if not details.get("legal_name") or not details.get("country"):
            raise PartnerValidationError(
                "Enter the legal name and country before submitting.", field="details"
            )
        missing = missing_requirements(partner_id)
        if missing:
            raise PartnerValidationError(
                "Still needed: " + ", ".join(missing) + ".", field="documents"
            )
        before = row.status
        row.status = "submitted"
        row.submitted_at = timezone.now()
        row.version += 1
        row.save(update_fields=["status", "submitted_at", "version", "updated_at"])
        audit.record(
            ctx,
            "partner.kyc_submitted",
            resource_type="partners.partner",
            resource_id=partner_id,
            changes={"from": before, "to": "submitted"},
        )
    metrics.increment(OPERATIONS, operation="submit", outcome="ok")
    _tell_staff_submitted(partner_id)
    return application(ctx, partner_id)


# --- the review ----------------------------------------------------------------------------


def awaiting_review_count() -> int:
    m = _models()
    return m.PartnerKyc.objects.filter(status__in=DECIDABLE).count()


def recent_decisions(*, limit: int = 8) -> list[dict[str, Any]]:
    """For the dashboard's activity list, whose caller has authorized."""
    m = _models()
    rows = list(
        m.PartnerKycDecision.objects.select_related("partner")
        .exclude(decision="start_review")
        .order_by("-decided_at")[:limit]
    )
    return [
        {
            "partner_id": str(row.partner_id),
            "partner_name": row.partner.display_name,
            "decision": row.to_status,
            "words": _WORDS.get(row.to_status, row.to_status),
            "decided_at": row.decided_at.isoformat(),
        }
        for row in rows
    ]


def _user_names(ids: set[Any]) -> dict[Any, str]:
    from workeasy360.platform.identity.selectors import display_names

    return display_names({i for i in ids if i})


def queue(
    ctx: TenantContext,
    *,
    status: str | None = None,
    search: str | None = None,
    limit: Any = 25,
    offset: Any = 0,
) -> dict[str, Any]:
    """Applications, oldest waiting first. Partners that never started one appear
    under ``draft`` only because a row exists once anyone asked for it; the queue's
    default is what waits for a reviewer."""
    authz.require(ctx, VIEW)
    m = _models()
    known = {choice for choice, _label in m.PartnerKyc.Status.choices}
    if status and status != "all" and status not in known:
        raise PartnerValidationError("Unknown status.", field="status")
    size = max(1, min(_int(limit, 25), 100))
    start = max(0, _int(offset, 0))
    rows = m.PartnerKyc.objects.select_related("partner")
    if status and status != "all":
        rows = rows.filter(status=status)
    elif not status:
        rows = rows.filter(status__in=DECIDABLE)
    if search:
        term = search.strip()[:100]
        rows = rows.filter(
            Q(partner__display_name__icontains=term)
            | Q(partner__legal_name__icontains=term)
            | Q(partner__contact_email__icontains=term)
            | Q(partner__code__icontains=term)
        )
    total = rows.count()
    page = list(rows.order_by("submitted_at", "created_at")[start : start + size])
    names = _user_names(
        {row.decided_by_id for row in page} | {row.reviewer_id for row in page}
    )
    return {
        "results": [_queue_json(row, names) for row in page],
        "total": total,
        "limit": size,
        "offset": start,
        "counts": status_counts(),
        # Approved applications whose partner an Admin has not activated yet.
        "awaiting_activation": awaiting_activation_count(),
        # Partners active or suspended without an approved application: activated
        # before KYC was required. They keep their status; the page names them.
        "legacy_active": legacy_active_count(),
    }


def awaiting_activation_count() -> int:
    m = _models()
    return m.PartnerKyc.objects.filter(
        status="approved", partner__status=PartnerStatus.DRAFT.value
    ).count()


#: The partner list's ``kyc=`` filter: ``legacy`` (active or suspended with no
#: approved application) and ``ready`` (a draft whose application is approved).
LIST_FILTERS = ("legacy", "ready", "approved", "not_approved")


def list_filter(value: str | None) -> Any:
    """A ``Q`` over ``PartnerAccount`` for the list's ``kyc=`` filter, or ``None``."""
    from django.db.models import Exists, OuterRef

    if not value:
        return None
    if value not in LIST_FILTERS:
        raise PartnerValidationError("Unknown KYC filter.", field="kyc")
    m = _models()
    approved = Exists(
        m.PartnerKyc.objects.filter(partner=OuterRef("pk"), status="approved")
    )
    if value == "legacy":
        return Q(
            status__in=[PartnerStatus.ACTIVE.value, PartnerStatus.SUSPENDED.value]
        ) & ~Q(approved)
    if value == "ready":
        return Q(status=PartnerStatus.DRAFT.value) & Q(approved)
    return Q(approved) if value == "approved" else ~Q(approved)


def legacy_active_count() -> int:
    m = _models()
    return (
        m.PartnerAccount.objects.filter(
            status__in=[PartnerStatus.ACTIVE.value, PartnerStatus.SUSPENDED.value]
        )
        .exclude(kyc__status="approved")
        .count()
    )


def status_counts() -> dict[str, int]:
    from django.db.models import Count

    m = _models()
    return {
        row["status"]: row["n"]
        for row in m.PartnerKyc.objects.values("status").annotate(n=Count("partner_id"))
    }


def _int(raw: Any, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _queue_json(row: Any, names: dict[Any, str]) -> dict[str, Any]:
    return {
        "partner_id": str(row.partner_id),
        "partner_name": row.partner.display_name,
        "partner_code": row.partner.code,
        "partner_status": row.partner.status,
        # For the separate activation step (``expected_version`` on the status command).
        "partner_version": row.partner.version,
        "contact_email": row.partner.contact_email,
        "status": row.status,
        "requested_experience": row.requested_experience,
        "source": row.source,
        "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "reviewer_name": names.get(row.reviewer_id, ""),
        "decided_by_name": names.get(row.decided_by_id, ""),
        "created_at": row.created_at.isoformat(),
    }


def detail(ctx: TenantContext, partner_id: UUID) -> dict[str, Any]:
    """One application: business details, documents, every decision, every read."""
    authz.require(ctx, VIEW)
    m = _models()
    row = _record(partner_id)
    decisions = list(
        m.PartnerKycDecision.objects.filter(partner_id=partner_id).order_by("-decided_at")
    )
    access = list(
        m.PartnerKycDocumentAccess.objects.filter(partner_id=partner_id).order_by(
            "-accessed_at"
        )[:50]
    )
    names = _user_names(
        {d.decided_by_id for d in decisions}
        | {a.accessed_by_id for a in access}
        | {row.reviewer_id, row.decided_by_id}
    )
    documents = list(
        m.PartnerKycDocument.objects.filter(partner_id=partner_id).order_by(
            "-uploaded_at"
        )
    )
    scans = _scan_states(ctx, [d.file_id for d in documents])
    may_review = authz.check(ctx, REVIEW).allowed
    return {
        **_queue_json(row, names),
        "version": row.version,
        "details": dict(row.details or {}),
        "decision_reason": row.decision_reason,
        "requirements": [requirement_json(r) for r in _active_requirements()],
        "documents": [
            {
                **_document_json(d, scans),
                "removed_at": d.removed_at.isoformat() if d.removed_at else None,
            }
            for d in documents
        ],
        "missing": missing_requirements(partner_id),
        "decisions": [_decision_json(d, names) for d in decisions],
        "document_access": [
            {
                "document_id": str(a.document_id),
                "accessed_by_name": names.get(a.accessed_by_id, ""),
                "accessed_at": a.accessed_at.isoformat(),
            }
            for a in access
        ],
        "may_review": may_review,
        "may_decide": may_review and row.status in DECIDABLE,
    }


def _record_decision(
    ctx: TenantContext,
    row: Any,
    *,
    decision: str,
    to_status: str,
    reason: str,
    flagged: list[str] | None = None,
) -> None:
    m = _models()
    m.PartnerKycDecision.objects.create(
        partner_id=row.partner_id,
        decision=decision,
        from_status=row.status,
        to_status=to_status,
        reason=reason,
        flagged_requirements=flagged or [],
        decided_by_id=ctx.user_id,
    )


@in_scope
def start_review(ctx: TenantContext, partner_id: UUID) -> dict[str, Any]:
    """Say someone has picked this up, and who."""
    authz.require(ctx, REVIEW)
    with transaction.atomic():
        row = _record(partner_id, lock=True)
        if row.status != "submitted":
            raise InvalidStateTransition(
                entity="partners.PartnerKyc",
                from_status=row.status,
                to_status="under_review",
            )
        _record_decision(
            ctx,
            row,
            decision="start_review",
            to_status="under_review",
            reason="Review started.",
        )
        row.status = "under_review"
        row.reviewer_id = ctx.user_id
        row.version += 1
        row.save(update_fields=["status", "reviewer", "version", "updated_at"])
        audit.record(
            ctx,
            "partner.kyc_review_started",
            resource_type="partners.partner",
            resource_id=partner_id,
            changes={"to": "under_review"},
        )
    return detail(ctx, partner_id)


@in_scope
def decide(
    ctx: TenantContext,
    partner_id: UUID,
    *,
    decision: Any,
    reason: Any = "",
    flagged_requirements: Any = None,
    experience: Any = None,
    expected_version: Any = None,
    also: Any = None,
) -> dict[str, Any]:
    """Approve, ask for corrections or reject. Each records who, when and why.

    ``also`` (ADR-063): the base permissions the reviewer grants explicitly beside
    the type - White Label alone sells, refers and earns nothing.

    **Approval does not activate (2026-11-10, the owner's brief).** An active
    partner is *KYC approved + an authorized Admin's activation*: two decisions by
    two permissions (``platform.staff_kyc.review`` here, ``platform.staff_partner.manage``
    on the status command, which checks the approval again). Approval records the
    decision and grants the capabilities of what the partner asked to become, but a
    draft partner's grants confer nothing (``resolver.py``), so it can do nothing
    until it is activated. A partner that is already active or suspended keeps its
    status: approving its paperwork changes nothing about whether it may operate.
    """
    from workeasy360.commercial.partners import experience as experiences

    authz.require(ctx, REVIEW)
    target = DECISIONS.get(str(decision or ""))
    if target is None:
        raise PartnerValidationError("Choose a decision.", field="decision")
    why = clean_text(reason, max_length=500, field="reason", required=False)
    if target != "approved" and len(why) < 10:
        raise PartnerValidationError(
            "Say why, in at least 10 characters: the partner reads this.", field="reason"
        )
    flagged = (
        _clean_flags(flagged_requirements) if target == "correction_required" else []
    )
    with transaction.atomic():
        row = _record(partner_id, lock=True)
        _check_version(row, expected_version)
        if row.status not in DECIDABLE:
            raise InvalidStateTransition(
                entity="partners.PartnerKyc", from_status=row.status, to_status=target
            )
        if target == "approved":
            waiting = unscanned_documents(ctx, partner_id)
            if waiting:
                # Fail closed (2026-11-10): no approval rests on a document no
                # scanner has passed. Corrections and rejections stay possible.
                raise PartnerValidationError(
                    "This application cannot be approved yet: "
                    f"{len(waiting)} document(s) have not passed the malware scan.",
                    field="documents",
                    documents=waiting,
                )
        _record_decision(
            ctx,
            row,
            decision=str(decision),
            to_status=target,
            reason=why,
            flagged=flagged,
        )
        now = timezone.now()
        row.status = target
        row.decision_reason = why
        row.decided_at = now
        row.decided_by_id = ctx.user_id
        row.version += 1
        row.save(
            update_fields=[
                "status",
                "decision_reason",
                "decided_at",
                "decided_by",
                "version",
                "updated_at",
            ]
        )
        audit.record(
            ctx,
            f"partner.kyc_{target}",
            resource_type="partners.partner",
            resource_id=partner_id,
            changes={"to": target, "flagged": flagged},
            reason=why or "Application approved.",
        )
        if target == "approved":
            key = str(experience or row.requested_experience or "")
            if key:
                # Recorded now, effective only once an Admin activates the partner.
                experiences.grant_for_approved_application(
                    ctx,
                    partner_id,
                    experience=key,
                    reason="KYC application approved.",
                    also=also or (),
                )
    metrics.increment(OPERATIONS, operation=target, outcome="ok")
    _notify_decision(partner_id, target, why)
    return detail(ctx, partner_id)


def _clean_flags(raw: Any) -> list[str]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list) or len(raw) > 50:
        raise PartnerValidationError(
            "Choose the documents to correct.", field="flagged_requirements"
        )
    m = _models()
    known = set(m.KycRequirement.objects.values_list("key", flat=True))
    flags = [str(item) for item in raw]
    if not set(flags) <= known:
        raise PartnerValidationError("Unknown document.", field="flagged_requirements")
    return flags


def _notify_decision(partner_id: UUID, target: str, reason: str) -> None:
    """Tell the partner's administrators. A failed email never undoes a decision."""
    from workeasy360.commercial.partners.notices import kyc_decided

    try:
        kyc_decided(partner_id, target, reason)
    except Exception:  # pragma: no cover - the decision stands whatever email does
        metrics.increment(OPERATIONS, operation="notify", outcome="failed")


# --- who opened KYC documents, across every application (2026-11-10) -------------------


def access_log(ctx: TenantContext, *, limit: Any = 50, offset: Any = 0) -> dict[str, Any]:
    """Every staff opening of a KYC document, newest first, across all partners:
    who, which partner and requirement, when, from which network. For whoever may
    review applications - the same people who may open the documents."""
    authz.require(ctx, REVIEW)
    m = _models()
    size = max(1, min(_int(limit, 50), 200))
    start = max(0, _int(offset, 0))
    rows = m.PartnerKycDocumentAccess.objects.select_related("document", "partner")
    total = rows.count()
    page = list(rows.order_by("-accessed_at")[start : start + size])
    names = _user_names({r.accessed_by_id for r in page})
    return {
        "results": [
            {
                "accessed_at": r.accessed_at.isoformat(),
                "accessed_by_name": names.get(r.accessed_by_id, ""),
                "partner_id": str(r.partner_id),
                "partner_name": r.partner.display_name,
                "requirement_key": r.document.requirement_key,
                "ip_prefix": r.ip_prefix,
            }
            for r in page
        ],
        "total": total,
        "limit": size,
        "offset": start,
    }


# --- reading a document ---------------------------------------------------------------------


def document_content(
    ctx: TenantContext, partner_id: UUID, document_id: UUID, *, ip_prefix: str = ""
) -> tuple[bytes, str, str]:
    """The bytes of one document, for the partner's administrators or a reviewer.

    Every read by staff is written down before the bytes leave, in the same
    transaction as the read of the row: an unaudited read is not possible here.
    """
    from workeasy360.platform.files import selectors as file_selectors

    m = _models()
    staff = ctx.principal is PrincipalKind.STAFF
    if staff:
        authz.require(ctx, REVIEW)
    else:
        _assert_own_administrator(ctx, partner_id)
    row = m.PartnerKycDocument.objects.filter(
        id=document_id, partner_id=partner_id
    ).first()
    if row is None:
        raise PartnerRecordNotFound("No such document.")
    with acting_scope(ctx), transaction.atomic():
        found = file_selectors.servable_content(row.file_id, max_bytes=DOCUMENT_MAX_BYTES)
        if found is None:
            state = file_selectors.scan_states([row.file_id]).get(row.file_id)
            if state == "pending":
                raise PartnerValidationError(
                    "This document has not passed the malware scan yet, so it cannot "
                    "be opened. It is scanned again automatically.",
                    field="document",
                )
            if state == "infected":
                raise PartnerValidationError(
                    "The malware scan found a threat in this document. Its content was "
                    "deleted; ask the partner to upload it again.",
                    field="document",
                )
            raise PartnerRecordNotFound("That document cannot be opened.")
        if staff:
            m.PartnerKycDocumentAccess.objects.create(
                partner_id=partner_id,
                document_id=row.id,
                accessed_by_id=ctx.user_id,
                ip_prefix=ip_prefix[:64],
            )
            audit.record(
                ctx,
                "partner.kyc_document_opened",
                resource_type="partners.kyc_document",
                resource_id=row.id,
                changes={
                    "partner_id": str(partner_id),
                    "requirement": row.requirement_key,
                },
            )
    data, detected = found
    return data, detected, row.filename


__all__ = [
    "PartnerStatus",
    "application",
    "assert_may_activate",
    "awaiting_review_count",
    "decide",
    "detail",
    "document_content",
    "queue",
    "recent_decisions",
    "requirements",
    "start_review",
    "submit",
    "upload_document",
]

# ``selectors`` is imported for the module's public read of a partner row by callers
# that already use this module; keep the name referenced for static analysis.
_ = selectors


def public_requirements() -> list[dict[str, Any]]:
    """ADR-065: the documents an application asks for, as the Store's Partners page
    lists them - the label, the Admin's help text and whether it is required. No
    identifiers and nothing about any applicant."""
    return [
        {"label": row.label, "help_text": row.help_text, "is_required": row.is_required}
        for row in _active_requirements()
    ]
