"""Partner-channel data (Phase 7, ADR-028).

**Classification of every table here: partner-scoped control plane.** None is
a ``TenantScopedModel`` and none has a customer-tenant RLS policy, deliberately
and for one reason: these rows are not owned by a customer. A partner account,
its capability grants, its members, its price book and its branding belong to
the partner, and the relationship rows relate a partner to a customer without
being *the customer's data*. Attaching ``organization_id = current_setting``
RLS to them would be wrong twice over - a partner's portfolio spans many
organizations, so its reads could never run under a single tenant variable,
and the customer must not be able to edit who administers it.

Isolation is therefore **application-enforced and registry-checked** rather
than RLS-enforced (Task 98):

- every read reaches these tables only through ``selectors.py``, and every
  selector that serves a partner takes the ``partner_id`` from a
  server-verified ``PartnerMember`` (never from the request);
- no customer-facing endpoint reads them;
- ``test_control_plane_registry`` pins the exact list of control-plane tables
  and fails when one is added without being classified and given an
  isolation test.

**PartnerAccount carries only what every channel shares.** Channel-specific
data - a referral code, a brand, a domain - lives in its own table, so an
affiliate carries none of a white-label partner's columns and a fourth channel
is a new table rather than a wider row.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db import models

from workeasy360.contracts.enums import (
    DomainStatus,
    GrantStatus,
    PartnerLinkStatus,
    PartnerMemberRole,
    PartnerStatus,
)
from workeasy360.contracts.values.money import CurrencyCode


def _choices(enum_cls: Any) -> list[tuple[str, str]]:
    return [(member.value, member.name.replace("_", " ").title()) for member in enum_cls]


class PartnerAccount(models.Model):
    """One partner: shared identity and lifecycle, nothing channel-specific."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    #: Stable machine identifier. Never reused, never a security boundary.
    code = models.SlugField(max_length=48, unique=True)
    display_name = models.CharField(max_length=120)
    legal_name = models.CharField(max_length=200, blank=True, default="")

    #: Business-contact metadata. Bounded, and never copied into events or
    #: the audit trail.
    contact_name = models.CharField(max_length=120, blank=True, default="")
    contact_email = models.EmailField(max_length=254, blank=True, default="")

    #: The one currency this partner is paid in. Commission is never converted:
    #: a sale in another currency accrues nothing and is reported as
    #: unsupported (Task 92).
    payout_currency = models.CharField(max_length=3, choices=_choices(CurrencyCode))

    status = models.CharField(
        max_length=16,
        choices=_choices(PartnerStatus),
        default=PartnerStatus.DRAFT.value,
    )
    #: Optimistic concurrency token. Two staff members editing one partner
    #: must not silently overwrite each other.
    version = models.IntegerField(default=0)

    created_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    terminated_at = models.DateTimeField(null=True, blank=True)
    status_reason = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        db_table = "partners_account"
        indexes = [
            models.Index(fields=["status", "created_at"], name="ix_partner_status"),
            # The staff partner list pages newest-first with a keyset cursor.
            models.Index(fields=["-created_at", "-id"], name="ix_partner_created_id"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.code} [{self.status}]"

    @property
    def status_enum(self) -> PartnerStatus:
        return PartnerStatus(self.status)


class PartnerCapabilityGrant(models.Model):
    """One capability held by one partner.

    Composable by construction: a partner is a *set of these rows*, so a
    reseller that also wants branding is one more row, not a new partner type
    and not a second account. ``capability`` is validated against the registry
    in ``capabilities.py`` - it is never free text.

    Never deleted: a revocation stamps ``revoked_at``, so what a partner was
    allowed to do on a given day remains answerable (commission eligibility
    depends on it).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.ForeignKey(
        PartnerAccount, on_delete=models.PROTECT, related_name="capability_grants"
    )
    capability = models.CharField(max_length=48)
    status = models.CharField(
        max_length=16,
        choices=_choices(GrantStatus),
        default=GrantStatus.ACTIVE.value,
    )
    granted_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="+"
    )
    granted_at = models.DateTimeField()
    #: A grant may be time-boxed. Enforced wherever permissions are resolved.
    expires_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=200, blank=True, default="")
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        "identity.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    revoke_reason = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        db_table = "partners_capability_grant"
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "capability"],
                condition=models.Q(status="active"),
                name="uq_partner_capability_active",
            ),
            models.CheckConstraint(
                condition=models.Q(status="active", revoked_at__isnull=True)
                | models.Q(status="revoked", revoked_at__isnull=False),
                name="ck_capability_grant_status_matches_revocation",
            ),
        ]
        indexes = [
            models.Index(fields=["partner", "status"], name="ix_capgrant_partner_status"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.partner_id}:{self.capability} [{self.status}]"


class PartnerMember(models.Model):
    """A Workeasy360 identity's membership of a partner.

    **Not a customer-organization membership.** ``organizations.Membership``
    says "this person belongs to a customer tenant"; this says "this person
    works for a partner". They are different relationships with different
    authority, and merging them would let customer-org membership imply
    partner authority (Task 20-21). There is one identity table; there are two
    association tables.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.ForeignKey(
        PartnerAccount, on_delete=models.PROTECT, related_name="members"
    )
    user = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")
    role = models.CharField(
        max_length=16,
        choices=_choices(PartnerMemberRole),
        default=PartnerMemberRole.VIEWER.value,
    )
    status = models.CharField(
        max_length=16,
        choices=[("active", "Active"), ("removed", "Removed")],
        default="active",
    )
    added_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="+"
    )
    added_at = models.DateTimeField()
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(
        "identity.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        db_table = "partners_member"
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "user"],
                condition=models.Q(status="active"),
                name="uq_partner_member_active",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "status"], name="ix_partnermember_user"),
            models.Index(fields=["partner", "status"], name="ix_partnermember_partner"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.user_id} @ {self.partner_id} [{self.role}]"

    @property
    def role_enum(self) -> PartnerMemberRole:
        return PartnerMemberRole(self.role)


class PartnerOrganizationLink(models.Model):
    """The explicit relationship between a partner and a customer.

    **Never inferred.** Not from ``Organization.created_by``, not from who
    provisioned the subscription, not from a shared user. A partner acts on a
    customer if and only if an ``active`` row exists here (Task 27), and the
    customer belongs to itself throughout: ending this row removes the
    partner's access and touches nothing of the customer's (Task 28).

    **At most one live link per organization** - "live" meaning ``active`` or
    ``suspended`` - so two partners can never both believe they own an
    account. A partial unique index enforces it; a check-then-insert would
    not.

    The ``organization`` foreign key targets the frozen allowlist
    (``Organization``) and is ``PROTECT``: a customer cannot be purged while a
    partner relationship - and the commercial history hanging from it - still
    refers to it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.ForeignKey(
        PartnerAccount, on_delete=models.PROTECT, related_name="links"
    )
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT, related_name="+"
    )
    status = models.CharField(
        max_length=16,
        choices=_choices(PartnerLinkStatus),
        default=PartnerLinkStatus.ACTIVE.value,
    )
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=64, blank=True, default="")
    created_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="+"
    )
    ended_by = models.ForeignKey(
        "identity.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    version = models.IntegerField(default=0)

    class Meta:
        db_table = "partners_organization_link"
        constraints = [
            models.UniqueConstraint(
                fields=["organization"],
                condition=~models.Q(status="ended"),
                name="uq_one_live_partner_link_per_org",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status="ended")
                    & models.Q(ended_at__isnull=True, ended_by__isnull=True)
                )
                | models.Q(status="ended", ended_at__isnull=False),
                name="ck_partner_link_end_stamped",
            ),
        ]
        indexes = [
            # The portfolio page: one partner's customers, newest first.
            models.Index(
                fields=["partner", "status", "-started_at", "-id"],
                name="ix_partnerlink_portfolio",
            ),
            models.Index(fields=["organization", "status"], name="ix_partnerlink_org"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.partner_id} -> {self.organization_id} [{self.status}]"

    @property
    def status_enum(self) -> PartnerLinkStatus:
        return PartnerLinkStatus(self.status)


class PartnerProductGrant(models.Model):
    """A product (or one plan of it) a partner is allowed to sell.

    A reseller does not sell everything by default: without an active row here
    a partner cannot sell a product to anyone (Task 23). ``plan`` null means
    every plan of the product; set, it narrows the grant to that one plan.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.ForeignKey(
        PartnerAccount, on_delete=models.PROTECT, related_name="product_grants"
    )
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="+"
    )
    plan = models.ForeignKey(
        "catalog.Plan",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    status = models.CharField(
        max_length=16,
        choices=_choices(GrantStatus),
        default=GrantStatus.ACTIVE.value,
    )
    granted_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="+"
    )
    granted_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        "identity.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        db_table = "partners_product_grant"
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "product"],
                condition=models.Q(status="active", plan__isnull=True),
                name="uq_partner_product_grant_active",
            ),
            models.UniqueConstraint(
                fields=["partner", "product", "plan"],
                condition=models.Q(status="active", plan__isnull=False),
                name="uq_partner_plan_grant_active",
            ),
            models.CheckConstraint(
                condition=models.Q(status="active", revoked_at__isnull=True)
                | models.Q(status="revoked", revoked_at__isnull=False),
                name="ck_product_grant_status_matches_revocation",
            ),
        ]
        indexes = [
            models.Index(
                fields=["partner", "status", "product"], name="ix_productgrant_partner"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.partner_id} may sell {self.product_id}"


class PartnerBrand(models.Model):
    """A white-label partner's presentation metadata.

    **Presentation only, and bounded.** Every field is a short validated
    string or a fixed set of hex colour tokens; there is no free HTML, no
    free CSS and no arbitrary remote image URL (Tasks 30-31, 82-83). The logo
    is a reference to the shared ``files`` platform, whose own controls (type,
    size, scanning, servability) apply - this module builds no storage.

    Branding never carries authority. Nothing here is read by authorization,
    entitlement or tenant resolution.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.OneToOneField(
        PartnerAccount, on_delete=models.PROTECT, related_name="brand"
    )
    brand_name = models.CharField(max_length=80)
    logo_file = models.ForeignKey(
        "files.File",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    accent_color = models.CharField(max_length=7, blank=True, default="")
    #: ``{token: "#rrggbb"}`` over a fixed token set, validated on write.
    theme = models.JSONField(default=dict)
    support_email = models.EmailField(max_length=254, blank=True, default="")
    support_url = models.CharField(max_length=300, blank=True, default="")

    version = models.IntegerField(default=0)
    updated_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partners_brand"

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"brand of {self.partner_id}"


class PartnerDomain(models.Model):
    """A custom hostname a white-label partner asked to serve branding on.

    Lifecycle ``requested -> verification_pending -> verified -> active``, plus
    ``failed`` and ``disabled``. **A domain is never active until the platform
    has itself observed the verification token in the domain's DNS** - a
    partner cannot claim a hostname by asserting it (Task 33).

    Uniqueness is on the *proven* states only. Two partners may both have a
    pending request for the same name (each holds its own token, so neither
    blocks the other and an attacker cannot squat a victim's domain by asking
    first), but only one can reach ``verified``/``active``; the partial unique
    index decides a race in the database.

    A domain changes presentation and nothing else. It is never an identity
    signal: tenant resolution, sessions and authorization are unaffected by
    which host a request arrived on.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.ForeignKey(
        PartnerAccount, on_delete=models.PROTECT, related_name="domains"
    )
    #: Lower-case, IDNA (punycode) ASCII, no trailing dot.
    hostname = models.CharField(max_length=253)
    status = models.CharField(
        max_length=24,
        choices=_choices(DomainStatus),
        default=DomainStatus.REQUESTED.value,
    )
    #: The value the partner must publish in DNS. Public by nature - it is
    #: proof of control, not a credential - and unique per request.
    verification_token = models.CharField(max_length=64)
    verified_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    disabled_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    check_count = models.PositiveIntegerField(default=0)
    #: Consecutive re-verifications that found the proof missing.
    missed_checks = models.PositiveSmallIntegerField(default=0)
    #: A short machine code, never provider output.
    failure_code = models.CharField(max_length=48, blank=True, default="")
    version = models.IntegerField(default=0)
    requested_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partners_domain"
        constraints = [
            # One partner cannot hold the same hostname twice in a live state.
            models.UniqueConstraint(
                fields=["partner", "hostname"],
                condition=~models.Q(status__in=["failed", "disabled"]),
                name="uq_partner_domain_live",
            ),
            # The claim itself: at most one *proven* owner per hostname.
            models.UniqueConstraint(
                fields=["hostname"],
                condition=models.Q(status__in=["verified", "active"]),
                name="uq_domain_hostname_proven",
            ),
            models.CheckConstraint(
                condition=~models.Q(status__in=["verified", "active"])
                | models.Q(verified_at__isnull=False),
                name="ck_domain_proven_has_verification_time",
            ),
        ]
        indexes = [
            # Host -> branding lookup: only ever asked of active domains.
            models.Index(
                fields=["hostname"],
                condition=models.Q(status="active"),
                name="ix_domain_active_host",
            ),
            models.Index(fields=["partner", "status"], name="ix_domain_partner"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.hostname} [{self.status}]"

    @property
    def status_enum(self) -> DomainStatus:
        return DomainStatus(self.status)


class PartnerOwnerInvite(models.Model):
    """The pending offer to be the owner of a partner-provisioned customer.

    A partner *names* an owner; it does not make anyone one. The organization
    is created with an ``invited`` owner membership and this row is what lets
    the named person find the offer when they sign in (a tenant-scoped
    ``Membership`` cannot be listed across organizations, and this table is
    read only by the invited user's own verified id). Nothing is granted until
    they accept - see ``platform.onboarding.services.activate_pending_owner``.

    **The activation link (Phase 10e, closing D-020).** A person a partner names
    has no password, so nothing could ever sign them in to accept. The row now
    also carries the *hash* of a one-time link, emailed to them, that proves
    they hold the mailbox: ``token_hash`` is the SHA-256 of a token that is
    unguessable (256 bits, prefixed with the organization it is for), single
    use (accepting or revoking clears it), expiring (``expires_at``) and bound
    to this user and this organization. The token itself is never stored, never
    logged and never returned by any API - it lives in the sealed notification
    and the URL fragment only. Sending again replaces it, so the previous link
    stops working.

    Control-plane, like the rest of the module: the customer organization
    does not own it and a partner never reads another person's invitations.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT, related_name="+"
    )
    user = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")
    partner = models.ForeignKey(
        PartnerAccount, on_delete=models.PROTECT, related_name="owner_invites"
    )
    status = models.CharField(
        max_length=16,
        choices=[
            ("pending", "Pending"),
            ("accepted", "Accepted"),
            ("expired", "Expired"),
            ("declined", "Declined"),
            ("revoked", "Revoked"),
        ],
        default="pending",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    #: SHA-256 (hex) of the emailed link's token; empty when there is no
    #: redeemable link (a legacy row, or one that was used or withdrawn).
    token_hash = models.CharField(max_length=64, blank=True, default="")
    #: How many times a link has been sent, and when last - facts for the
    #: person resending, never anything that could redeem one.
    send_count = models.PositiveSmallIntegerField(default=0)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "partners_owner_invite"
        constraints = [
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(status="pending"),
                name="uq_one_pending_owner_invite_per_org",
            ),
            # A link's hash names at most one invitation, so a lookup by hash
            # can never be ambiguous.
            models.UniqueConstraint(
                fields=["token_hash"],
                condition=~models.Q(token_hash=""),
                name="uq_owner_invite_token_hash",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "status"], name="ix_ownerinvite_user"),
            # The customer list reads the newest invitation of each organization.
            models.Index(
                fields=["organization", "-created_at"], name="ix_ownerinvite_org_latest"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"owner invite for {self.organization_id} [{self.status}]"


class PartnerLogoUpload(models.Model):
    """Which partner uploaded which logo file.

    Logos are held by the shared ``files`` platform, under the platform
    organization. A file id alone says nothing about *whose* it is, and two
    partners' logos share that organization - so without this row a partner
    could attach another partner's uploaded file by guessing its id. The
    branding service accepts a logo only when this row ties it to the caller.
    """

    file = models.OneToOneField(
        "files.File", primary_key=True, on_delete=models.PROTECT, related_name="+"
    )
    partner = models.ForeignKey(
        PartnerAccount, on_delete=models.PROTECT, related_name="logo_uploads"
    )
    uploaded_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "partners_logo_upload"
        indexes = [models.Index(fields=["partner"], name="ix_logoupload_partner")]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"logo {self.file_id} of {self.partner_id}"
