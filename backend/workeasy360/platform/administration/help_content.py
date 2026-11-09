"""Shared help content: FAQs and setup guides (2026-11-09).

One library for everyone; an article says who it is for. Plain text, never HTML: nothing
here can carry a script into the pages that show it. Staff manage it behind
``platform.staff_support.manage``; only what is published reaches a reader.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from workeasy360.contracts.errors import SecurityPolicyValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

VIEW = "platform.staff_support.view"
MANAGE = "platform.staff_support.manage"

KINDS = {"faq", "setup_guide"}
AUDIENCES = {"all", "customer", "partner"}
STATUSES = {"draft", "published", "archived"}


def _model() -> Any:
    from workeasy360.platform.administration.models import HelpArticle

    return HelpArticle


def article_json(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "slug": row.slug,
        "kind": row.kind,
        "audience": row.audience,
        "status": row.status,
        "title": row.title,
        "body": row.body,
        "product_code": row.product_code,
        "display_order": row.display_order,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "updated_at": row.updated_at.isoformat(),
    }


def _clean(body: dict[str, Any]) -> dict[str, Any]:
    title = " ".join(str(body.get("title") or "").split())
    text = str(body.get("body") or "").strip()
    if not title or len(title) > 200:
        raise SecurityPolicyValidationError(
            "Give it a title of at most 200 characters.", field="title"
        )
    if not text or len(text) > 20_000:
        raise SecurityPolicyValidationError(
            "Write the content, at most 20,000 characters.", field="body"
        )
    kind = body.get("kind", "faq")
    audience = body.get("audience", "all")
    if kind not in KINDS:
        raise SecurityPolicyValidationError(
            "Choose what kind of content this is.", field="kind"
        )
    if audience not in AUDIENCES:
        raise SecurityPolicyValidationError("Choose who this is for.", field="audience")
    order = body.get("display_order", 0)
    if isinstance(order, bool) or not isinstance(order, int) or not 0 <= order <= 9999:
        raise SecurityPolicyValidationError(
            "Order must be 0 to 9999.", field="display_order"
        )
    return {
        "title": title,
        "body": text,
        "kind": kind,
        "audience": audience,
        "display_order": order,
        "product_code": str(body.get("product_code") or "").strip()[:64],
    }


def articles(
    ctx: TenantContext, *, kind: Any = None, status: Any = None, search: Any = None
) -> list[dict[str, Any]]:
    authz.require(ctx, VIEW)
    rows = _model().objects.order_by("display_order", "title")
    if kind:
        rows = rows.filter(kind=kind)
    if status:
        rows = rows.filter(status=status)
    if search:
        term = str(search).strip()[:80]
        rows = rows.filter(Q(title__icontains=term) | Q(body__icontains=term))
    return [article_json(row) for row in rows[:200]]


def _slug(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "article"
    taken = set(
        _model().objects.filter(slug__startswith=base).values_list("slug", flat=True)
    )
    slug, n = base, 1
    while slug in taken:
        n += 1
        slug = f"{base}-{n}"
    return slug


@in_scope
def create(ctx: TenantContext, body: dict[str, Any]) -> dict[str, Any]:
    authz.require(ctx, MANAGE)
    fields = _clean(body)
    with transaction.atomic():
        row = _model().objects.create(
            slug=_slug(fields["title"]), status="draft", **fields
        )
        audit.record(
            ctx,
            "support.help_created",
            resource_type="administration.help_article",
            resource_id=row.id,
            changes={"kind": row.kind, "audience": row.audience},
        )
    return article_json(row)


@in_scope
def update(ctx: TenantContext, article_id: UUID, body: dict[str, Any]) -> dict[str, Any]:
    authz.require(ctx, MANAGE)
    fields = _clean(body)
    with transaction.atomic():
        row = _model().objects.select_for_update().filter(id=article_id).first()
        if row is None:
            raise SecurityPolicyValidationError("No such article.", field="article")
        for name, value in fields.items():
            setattr(row, name, value)
        row.save()
        audit.record(
            ctx,
            "support.help_updated",
            resource_type="administration.help_article",
            resource_id=row.id,
            changes={"kind": row.kind, "audience": row.audience},
        )
    return article_json(row)


@in_scope
def set_status(ctx: TenantContext, article_id: UUID, status: Any) -> dict[str, Any]:
    """Publish, take back to draft, or archive. Archiving is the only way out: an
    article is never deleted, because a guide may have been linked to."""
    authz.require(ctx, MANAGE)
    if status not in STATUSES:
        raise SecurityPolicyValidationError("Unknown status.", field="status")
    with transaction.atomic():
        row = _model().objects.select_for_update().filter(id=article_id).first()
        if row is None:
            raise SecurityPolicyValidationError("No such article.", field="article")
        row.status = status
        if status == "published" and row.published_at is None:
            row.published_at = timezone.now()
        row.save(update_fields=["status", "published_at", "updated_at"])
        audit.record(
            ctx,
            "support.help_status",
            resource_type="administration.help_article",
            resource_id=row.id,
            changes={"status": status},
        )
    return article_json(row)


def published(*, audience: Any = None, kind: Any = None) -> list[dict[str, Any]]:
    """What a reader sees: published articles for them and for everyone."""
    rows = _model().objects.filter(status="published").order_by("display_order", "title")
    if audience in ("customer", "partner"):
        rows = rows.filter(audience__in=("all", audience))
    else:
        rows = rows.filter(audience="all")
    if kind in KINDS:
        rows = rows.filter(kind=kind)
    return [
        {
            "slug": row.slug,
            "kind": row.kind,
            "title": row.title,
            "body": row.body,
            "product_code": row.product_code,
        }
        for row in rows[:200]
    ]
