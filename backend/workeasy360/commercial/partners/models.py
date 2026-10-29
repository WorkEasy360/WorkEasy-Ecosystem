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
    PartnerContactKind,
    PartnerHierarchyStatus,
    PartnerLinkStatus,
    PartnerMemberRole,
    PartnerStatus,
    PayoutFrequency,
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
    #: Which workspace this partner's portal opens first - ``reseller``,
    #: ``white_label`` or ``affiliate`` (``capabilities.EXPERIENCES``), or empty
    #: for "whichever its grants earn first". **A presentation preference and
    #: nothing else** (ADR-047): the Add Partner type card sets it, and no
    #: permission, price, commission or entitlement ever reads it - what a
    #: partner may do comes from its capability grants alone, so setting this to
    #: an experience the grants do not earn opens nothing (``capabilities.
    #: resolve_landing`` intersects it with what the grants allow). It is not a
    #: partner type: it can change at any time and removes no history.
    default_experience = models.CharField(
        max_length=16, blank=True, default="", db_default=""
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
        constraints = [
            # A closed set, held by the database as well as the service: the
            # preference is never free text.
            models.CheckConstraint(
                condition=models.Q(
                    default_experience__in=["", "reseller", "white_label", "affiliate"]
                ),
                name="ck_partner_default_experience_known",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.code} [{self.status}]"

    @property
    def status_enum(self) -> PartnerStatus:
        return PartnerStatus(self.status)


class PartnerProfile(models.Model):
    """What a partner is as a business, beyond what every channel shares.

    One row per partner, created the first time anything here is saved. It holds
    *facts to reach and invoice a company* - a website, a registration and tax
    number, an address, customer-facing support details, how payouts are
    preferred, and staff-only notes - and nothing a decision reads: no
    permission, price, commission or entitlement depends on a column here.

    Kept off ``PartnerAccount`` so that table stays "only what every channel
    shares" (the module docstring): an affiliate that never sets a tax number
    carries no empty columns for one, and a business detail can change without
    touching the version token a status change is serialised on. The partner's
    own name, legal name, currency and lifecycle remain on the account.

    Personal data. Never copied into an event or an audit entry (those record
    *which* fields changed), never shown to another partner, and read by staff
    only.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.OneToOneField(
        PartnerAccount, on_delete=models.PROTECT, related_name="profile"
    )
    business_email = models.EmailField(max_length=254, blank=True, default="")
    website = models.CharField(max_length=300, blank=True, default="")
    phone = models.CharField(max_length=32, blank=True, default="")
    alternative_email = models.EmailField(max_length=254, blank=True, default="")
    registration_number = models.CharField(max_length=64, blank=True, default="")
    tax_number = models.CharField(max_length=64, blank=True, default="")
    #: ISO 3166-1 alpha-2 (``contracts.values.countries``), or empty.
    country = models.CharField(max_length=2, blank=True, default="")
    #: An IANA zone name (``Asia/Kolkata``) validated against the platform's
    #: zone database on write, or empty. Presentation only: it decides how this
    #: partner's own screens write a time, never when anything happens.
    timezone = models.CharField(max_length=64, blank=True, default="", db_default="")
    #: ``{line1, line2, city, region, postal_code}`` - a closed key set.
    address = models.JSONField(default=dict)

    #: Customer-facing support details of the *reseller business*. (A
    #: white-label brand carries its own support details for its own portal.)
    support_email = models.EmailField(max_length=254, blank=True, default="")
    support_phone = models.CharField(max_length=32, blank=True, default="")
    support_url = models.CharField(max_length=300, blank=True, default="")

    #: Payout *preferences*. Currency is the account's ``payout_currency``.
    payout_frequency = models.CharField(
        max_length=16,
        choices=_choices(PayoutFrequency),
        default=PayoutFrequency.MANUAL.value,
    )
    payout_contact_email = models.EmailField(max_length=254, blank=True, default="")

    #: Staff only. Never serialised to a partner, a customer or a public page.
    internal_notes = models.TextField(blank=True, default="")

    updated_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partners_profile"

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"profile of {self.partner_id}"


class PartnerContact(models.Model):
    """A person to reach at a partner, in one business role.

    **Not an account.** A contact is name, title, email and phone *as text* - it
    has no password, no membership, no permission and cannot sign in; the people
    who act for a partner are ``PartnerMember`` rows over the one identity
    table. A role with no row means "same as the primary contact" (the screens
    say so) rather than a copied row that could drift from the original.

    The primary contact's name and email are mirrored onto the account
    (``contact_name`` / ``contact_email``) in the same transaction, because the
    partner lists and the setup checks have always read them there.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.ForeignKey(
        PartnerAccount, on_delete=models.PROTECT, related_name="contacts"
    )
    kind = models.CharField(max_length=16, choices=_choices(PartnerContactKind))
    name = models.CharField(max_length=120, blank=True, default="")
    job_title = models.CharField(max_length=120, blank=True, default="")
    email = models.EmailField(max_length=254, blank=True, default="")
    phone = models.CharField(max_length=32, blank=True, default="")
    updated_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partners_contact"
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "kind"], name="uq_partner_contact_kind"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.kind} contact of {self.partner_id}"


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
        choices=[
            ("active", "Active"),
            ("suspended", "Suspended"),
            ("removed", "Removed"),
        ],
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
    #: When a one-time setup link was last emailed to this person. ``None`` for
    #: someone with no password means "named, not yet invited" - what lets a
    #: partner be set up in draft and its administrator invited at activation.
    invitation_sent_at = models.DateTimeField(null=True, blank=True)
    #: True only for a person attached with ``invite_member(send_link=False)``:
    #: their setup link is sent when the partner is first activated. Someone
    #: attached any other way (an existing account, ``add_member``) is never
    #: emailed by activation.
    invitation_deferred = models.BooleanField(default=False, db_default=False)

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
    #: Whether this partner's customers may start the product's *existing*
    #: platform trial. It only ever narrows: a product with no trial configured
    #: has none whatever this says, and a trial already started is untouched.
    trial_allowed = models.BooleanField(default=True, db_default=True)
    #: Whether the product is shown on the partner's white-label portal. Display
    #: only - it never grants or removes an entitlement, so a customer who holds
    #: the product still launches it.
    portal_visible = models.BooleanField(default=True, db_default=True)
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


class PartnerPromotionGrant(models.Model):
    """A product a partner may **promote** with referral links (ADR-054).

    Separate on purpose from ``PartnerProductGrant`` (may *sell*) and from any
    customer entitlement (may *use*): an affiliate given a product here can
    make product, pricing, feature and update links for it, and nothing else.
    Without an active row the partner cannot make a new link for the product.

    Revoking stops new links at once. What the partner's existing links do
    afterwards is the programme's ``revoked_links`` policy; referrals already
    captured and commission already earned are never touched. Rows are never
    deleted - a grant given again is a new row - so the history of who could
    promote what, and when, stays readable.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.ForeignKey(
        PartnerAccount, on_delete=models.PROTECT, related_name="promotion_grants"
    )
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="+"
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
        db_table = "partners_promotion_grant"
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "product"],
                condition=models.Q(status="active"),
                name="uq_partner_promotion_grant_active",
            ),
            models.CheckConstraint(
                condition=models.Q(status="active", revoked_at__isnull=True)
                | models.Q(status="revoked", revoked_at__isnull=False),
                name="ck_promotion_grant_status_matches_revocation",
            ),
        ]
        indexes = [
            models.Index(
                fields=["partner", "status", "product"], name="ix_promotiongrant_partner"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.partner_id} may promote {self.product_id}"


class PartnerHierarchyLink(models.Model):
    """One partner (the parent) delegating to another (the sub-partner).

    **Architecture-neutral on purpose.** This is the one table the sub-partner
    relationship needs: a parent ``PartnerAccount`` points at a child
    ``PartnerAccount``. There is no agency, sub-agency or sub-partner model - a
    sub-partner is an ordinary partner account that happens to have a live row
    here as ``child`` - and the depth rule lives in ``hierarchy.py`` (today one
    level), not in the schema, so a deeper chain is a policy change and never a
    migration.

    **Never inferred and never a permission.** The row grants the *parent* the
    right to see the child's status and counts and to suspend it; it grants the
    child nothing and the parent nothing of the child's customers, profile,
    payout details or secrets. Every access decision still comes from the
    child's own capability grants.

    **Invariants held by the database**, not only the service:

    - a partner is never its own parent (``ck_hierarchy_not_self``);
    - a partner has at most one *live* parent (``uq_one_live_parent_per_child``),
      so two parents can never both believe they own a sub-partner;
    - an ended row is stamped (``ck_hierarchy_end_stamped``).

    Cycles are refused by the service under a lock on the child.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parent = models.ForeignKey(
        PartnerAccount, on_delete=models.PROTECT, related_name="child_links"
    )
    child = models.ForeignKey(
        PartnerAccount, on_delete=models.PROTECT, related_name="parent_links"
    )
    status = models.CharField(
        max_length=16,
        choices=_choices(PartnerHierarchyStatus),
        default=PartnerHierarchyStatus.ACTIVE.value,
    )
    created_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_by = models.ForeignKey(
        "identity.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    end_reason = models.CharField(max_length=64, blank=True, default="")
    version = models.IntegerField(default=0)

    class Meta:
        db_table = "partners_hierarchy_link"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(parent=models.F("child")),
                name="ck_hierarchy_not_self",
            ),
            models.UniqueConstraint(
                fields=["child"],
                condition=~models.Q(status="ended"),
                name="uq_one_live_parent_per_child",
            ),
            models.CheckConstraint(
                condition=(~models.Q(status="ended") & models.Q(ended_at__isnull=True))
                | models.Q(status="ended", ended_at__isnull=False),
                name="ck_hierarchy_end_stamped",
            ),
        ]
        indexes = [
            models.Index(
                fields=["parent", "status", "-created_at", "-id"],
                name="ix_hierarchy_parent",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.parent_id} -> {self.child_id} [{self.status}]"


class PartnerProductSetting(models.Model):
    """How one partner presents and offers a product it was granted.

    **Presentation and a sale switch - never an entitlement.** A row exists only
    once a partner has changed something; no row means the defaults (offered,
    visible, catalog order). It is read in exactly two places: the partner's
    own sell path (``enabled_for_sale`` can only *narrow* what the grant
    allows - a partner cannot enable a product it holds no ``PartnerProductGrant``
    for, because the grant is checked first and separately) and the display of
    its customers' portal (order and visibility). Nothing here reaches
    authorization, entitlement, pricing or tenant resolution, and turning a
    product off for new sales ends nobody's subscription.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.ForeignKey(
        PartnerAccount, on_delete=models.PROTECT, related_name="product_settings"
    )
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="+"
    )
    enabled_for_sale = models.BooleanField(default=True, db_default=True)
    #: Lower sorts first; ties fall back to the catalog's own order.
    display_order = models.PositiveSmallIntegerField(default=0, db_default=0)
    show_in_explore = models.BooleanField(default=True, db_default=True)
    show_in_my_products = models.BooleanField(default=True, db_default=True)
    updated_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partners_product_setting"
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "product"], name="uq_partner_product_setting"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"settings of {self.partner_id} for {self.product_id}"


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
    #: Bounded plain text and flags the customer portal shows (a short name, a
    #: welcome message, a footer, support details) and the sender identity of
    #: emails sent in the partner's name. A closed key set validated on write
    #: (``branding.PORTAL_FIELDS``), like ``theme``: no markup, no URLs to
    #: fetch, nothing an authorization decision reads.
    portal = models.JSONField(default=dict, db_default={})

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


class PartnerApiKey(models.Model):
    """A scoped credential a partner's own systems present to the partner API.

    **Only a hash is stored.** The secret is 256 random bits shown once, at
    creation or rotation; what is kept is its SHA-256 (enough for a
    high-entropy random value, and what the platform already does for every
    other bearer secret) and a short public ``key_id`` that names the row. The
    secret is never returned again, never logged and never audited.

    A key is bound to the **partner** (its data is the only data it can reach),
    to a closed set of **scopes** from ``api_keys.SCOPES`` (never free text),
    and to the **member who created it**: it works only while that member is
    still an active member of the partner, so removing or pausing a person
    ends their keys with their access. It always expires.

    Rotation never edits a secret in place: the old row is revoked and a new
    row (``replaces`` -> the old one) carries the new secret.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.ForeignKey(
        PartnerAccount, on_delete=models.PROTECT, related_name="api_keys"
    )
    name = models.CharField(max_length=80)
    #: The public part of the credential (``w360pk_<key_id>_<secret>``).
    key_id = models.CharField(max_length=16, unique=True)
    secret_hash = models.CharField(max_length=64)
    scopes = models.JSONField(default=list)
    status = models.CharField(
        max_length=16,
        choices=[("active", "Active"), ("revoked", "Revoked")],
        default="active",
    )
    created_by = models.ForeignKey(
        PartnerMember, on_delete=models.PROTECT, related_name="api_keys"
    )
    created_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        "identity.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    revoke_reason = models.CharField(max_length=32, blank=True, default="")
    replaces = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        db_table = "partners_api_key"
        indexes = [
            models.Index(fields=["partner", "-created_at"], name="ix_apikey_partner"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status="active", revoked_at__isnull=True)
                | models.Q(status="revoked", revoked_at__isnull=False),
                name="ck_apikey_revoked_stamped",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"api key {self.key_id} of {self.partner_id} [{self.status}]"


class PartnerTranslation(models.Model):
    """A partner-owned presentation string for one locale.

    The key must be in ``translations.KEYS`` (a closed list of labels and
    messages a partner's customers see) and the locale in
    ``translations.LOCALES``; the value is one bounded line of plain text, never
    markup or template code. Nothing here is read by authorization,
    entitlement, validation or pricing. A missing row falls back to the
    platform's wording, then to English (``translations.resolve``).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.ForeignKey(
        PartnerAccount, on_delete=models.PROTECT, related_name="translations"
    )
    locale = models.CharField(max_length=8)
    key = models.CharField(max_length=64)
    value = models.CharField(max_length=500)
    updated_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="+"
    )
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "partners_translation"
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "locale", "key"], name="uq_partner_translation"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.partner_id} {self.locale} {self.key}"


class PartnerNotificationState(models.Model):
    """A member's "mark all read" mark: everything in their Notification Center
    at or before ``read_until`` counts as read. Read-state only - the
    notifications themselves are entries of the one audit log and are not
    copied anywhere."""

    member = models.OneToOneField(
        PartnerMember, primary_key=True, on_delete=models.CASCADE, related_name="+"
    )
    read_until = models.DateTimeField()

    class Meta:
        db_table = "partners_notification_state"

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.member_id} read until {self.read_until}"


class PartnerNotificationRead(models.Model):
    """One member has read one notification.

    A partner's notifications are entries of the one audit log (nothing is
    copied), so this row holds only the pair "member, event" - the read-state a
    person keeps for themselves. ``PartnerNotificationState`` covers "mark all
    read".
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    member = models.ForeignKey(
        PartnerMember, on_delete=models.CASCADE, related_name="notification_reads"
    )
    event_id = models.UUIDField()
    read_at = models.DateTimeField()

    class Meta:
        db_table = "partners_notification_read"
        constraints = [
            models.UniqueConstraint(
                fields=["member", "event_id"], name="uq_partner_notification_read"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.member_id} read {self.event_id}"


class PartnerUpgradeRequest(models.Model):
    """A partner's request to move to a larger experience (ADR-049).

    **The request is a workflow record and nothing more.** It never holds a
    capability, a price or a permission: approving it asks the one experience
    transition service (``experience.transition_experience``) to grant the
    approved bundle, exactly as staff do by hand. So the same user, the same
    partner account and the same capability grants carry the partner from
    Affiliate to Reseller or White-label; only the landing preference and the
    grants change, and nothing the partner earned is touched.

    ``status`` is the lifecycle the Super Admin console and the partner both
    read: ``requested`` (the partner started it), ``profile_required`` (staff
    asked for more), ``pending_approval`` (submitted), then the three ends
    ``approved``, ``rejected`` and ``cancelled``. A partner has at most one
    open request (a partial unique index), so two clicks, two tabs or two
    administrators cannot raise two.

    ``proposal`` holds only what has no home elsewhere yet - the products asked
    for, the pricing preference, the commercial notes, and the brand and domain
    a White-label partner proposes - in a closed key set the service validates.
    Business details go to ``PartnerProfile`` through the profile command; they
    are not duplicated here. Business data, never a secret, and never copied
    into an audit entry (those record which sections changed).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.ForeignKey(
        PartnerAccount, on_delete=models.PROTECT, related_name="upgrade_requests"
    )
    #: The workspace the partner opened first when it asked (``""`` for none).
    from_experience = models.CharField(max_length=16, blank=True, default="")
    to_experience = models.CharField(max_length=16)
    status = models.CharField(max_length=20, default="requested")
    proposal = models.JSONField(default=dict)

    requested_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="+"
    )
    requested_at = models.DateTimeField()
    submitted_at = models.DateTimeField(null=True, blank=True)
    #: What staff wrote when they asked for more, shown to the partner.
    info_request_note = models.CharField(max_length=500, blank=True, default="")

    decided_by = models.ForeignKey(
        "identity.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=500, blank=True, default="")
    #: What approving it changed, kept on the row so the console can show it
    #: without reading the audit log. The grants themselves are the authority.
    capabilities_added = models.JSONField(default=list)
    capabilities_removed = models.JSONField(default=list)
    products_granted = models.JSONField(default=list)
    #: Staff's pointer to the commercial terms agreed (a contract or invoice
    #: number). A reference only: no amount is stored or charged here.
    commercial_reference = models.CharField(max_length=120, blank=True, default="")
    correlation_id = models.UUIDField(null=True, blank=True)
    #: ADR-053: the programme's set-up fee for the target, fixed when the
    #: request opened (a later fee change does not move it). ``None`` is "no
    #: fee". When there is one, approval needs the payment's reference, and
    #: ``fee_settled_at`` records when staff confirmed it - money is collected
    #: outside the platform (an offline invoice), never charged here.
    fee_amount_minor = models.BigIntegerField(null=True, blank=True)
    fee_currency = models.CharField(max_length=3, blank=True, default="", db_default="")
    fee_settled_at = models.DateTimeField(null=True, blank=True)

    #: Optimistic token: two administrators editing one draft must not silently
    #: overwrite each other.
    version = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partners_upgrade_request"
        indexes = [
            models.Index(
                fields=["partner", "-requested_at"], name="ix_upgrade_partner_time"
            ),
            models.Index(fields=["status", "-requested_at"], name="ix_upgrade_status"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["partner"],
                condition=models.Q(
                    status__in=["requested", "profile_required", "pending_approval"]
                ),
                name="uq_partner_one_open_upgrade",
            ),
            models.CheckConstraint(
                condition=models.Q(to_experience__in=["reseller", "white_label"]),
                name="ck_upgrade_target_known",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    from_experience__in=["", "reseller", "white_label", "affiliate"]
                ),
                name="ck_upgrade_source_known",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        "requested",
                        "profile_required",
                        "pending_approval",
                        "approved",
                        "rejected",
                        "cancelled",
                    ]
                ),
                name="ck_upgrade_status_known",
            ),
            # A decision always names who made it and when.
            models.CheckConstraint(
                condition=~models.Q(status__in=["approved", "rejected"])
                | (
                    models.Q(decided_by__isnull=False)
                    & models.Q(decided_at__isnull=False)
                ),
                name="ck_upgrade_decision_has_actor",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.partner_id}: {self.from_experience or '-'} -> {self.to_experience} [{self.status}]"


class PartnerProgramFee(models.Model):
    """The set-up fee for becoming a Reseller or a White-label partner
    (ADR-053, the owner's "setup fee if configured"). One row per target
    experience, set by Super Admin; no row, or a zero amount, is "no fee".

    A request snapshots the fee when it opens, so changing it here never moves
    a request already in progress. Settlement is offline: staff record the
    payment's reference when approving.
    """

    experience = models.CharField(max_length=16, unique=True)
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3, choices=_choices(CurrencyCode))
    updated_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partners_program_fee"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(experience__in=["reseller", "white_label"]),
                name="ck_program_fee_experience",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_minor__gte=0),
                name="ck_program_fee_not_negative",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.experience}: {self.amount_minor} {self.currency}"
