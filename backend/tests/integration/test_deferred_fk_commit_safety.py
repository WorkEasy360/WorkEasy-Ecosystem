"""Proves the deferred composite-FK backstop is real, at a real COMMIT
(Phase 5, Tasks 33-36).

The Phase 4 audit found and fixed one instance of this bug class - seat
assignment trusted a client-supplied ``membership_id`` and would have 500d
at commit for a cross-organization one, invisible to a normal
``pytest.mark.django_db`` test because that mode never commits
(``platform.launcher.services.assign_seat`` and
``organizations.selectors.active_membership_exists`` are the fix and its own
explanation). What Phase 4 did **not** leave behind is a test that commits
for real and shows the database constraint - the actual backstop the
application-level guard exists in front of - genuinely firing. This module
is that test, plus the same proof for Phase 5's own new write path
(``platform.authz.services.assign_system_role``, used by organization
creation) and a registry-driven inventory of every other composite tenant
FK, so the next one of these bugs is a deliberate, reviewed decision rather
than a rediscovery.

``transaction=True`` throughout (Task 34): outside an explicit
``atomic()`` block the connection is in autocommit mode, so a bare
``.save()`` is its own single-statement transaction and the ``DEFERRABLE
INITIALLY DEFERRED`` constraint's check fires at that statement's implicit
commit - synchronously, from the ``.save()`` call itself. A test wrapped in
pytest-django's default rollback-only transaction would never reach that
commit at all, which is the exact blindness this file exists to close.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError

pytestmark = [pytest.mark.django_db(transaction=True)]


def test_a_cross_organization_role_assignment_is_refused_at_commit(
    organization_factory,
):
    """The mechanism ``assign_seat``'s guard (and Task 30's owner-role
    bootstrap) both rely on: ``authz_role_assignment.membership_id`` is a
    composite, deferred foreign key into ``(organizations_membership.id,
    organization_id)`` (``tenancy/migrations/0001_rls_policies.py``,
    ``COMPOSITE_FKS``). A row naming the right organization but the *wrong*
    organization's membership has no matching target and must fail - at
    commit, not at INSERT, because the constraint is deferred.
    """
    from workeasy360.platform.authz.models import Role, RoleAssignment
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    org_a, _membership_a, _user_a = organization_factory()
    _org_b, membership_b, _user_b = organization_factory()

    owner_role = Role.objects.get(key="owner", organization__isnull=True, is_system=True)

    with unscoped_access(
        reason="Commit-phase test constructs a tenant row directly, bypassing "
        "the application guard on purpose to prove the database backstop",
        module="tests.integration.test_deferred_fk_commit_safety",
    ):
        bad_assignment = RoleAssignment(
            organization_id=org_a.id,  # org A
            membership_id=membership_b.id,  # but org B's membership
            role=owner_role,
        )
        # The exception surfaces when organization_db_scope's own internal
        # transaction.atomic() commits (its __exit__), not from .save()
        # itself: PostgreSQL evaluates a DEFERRABLE INITIALLY DEFERRED
        # constraint at COMMIT, and organization_db_scope opens its own
        # atomic block to give SET LOCAL a transaction to be local *to* -
        # so the whole scoped block, not the bare save() call, is where
        # this test must expect the raise.
        with pytest.raises(IntegrityError):
            with organization_db_scope(org_a.id):
                bad_assignment.save()


def test_a_same_organization_role_assignment_commits_cleanly(organization_factory):
    """The negative case: this is not merely "every RoleAssignment insert
    fails" - a correctly-paired (membership, organization) commits fine,
    which is what makes the test above meaningful rather than trivial."""
    from workeasy360.platform.authz.models import Role, RoleAssignment
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    org, membership, _user = organization_factory()
    admin_role = Role.objects.get(key="admin", organization__isnull=True, is_system=True)

    with (
        unscoped_access(
            reason="Commit-phase test constructs a tenant row directly, "
            "mirroring platform.authz.services.assign_system_role",
            module="tests.integration.test_deferred_fk_commit_safety",
        ),
        organization_db_scope(org.id),
    ):
        RoleAssignment(
            organization_id=org.id, membership_id=membership.id, role=admin_role
        ).save()

        # The absence of IntegrityError above is the primary assertion; this
        # confirms the row is genuinely there, not merely that nothing
        # raised. unscoped + inside org's own scope, matching the write.
        assert RoleAssignment.unscoped.filter(
            organization_id=org.id, membership_id=membership.id, role=admin_role
        ).exists()


def test_organization_creation_never_produces_a_cross_organization_role_assignment(
    user_factory,
):
    """Phase 5's own new write path (Task 33's brief: audit *every*
    client-controlled tenant FK write this phase adds). ``create_organization``
    builds the owner's ``RoleAssignment`` from a membership it just created
    in the same transaction, never from client input - this commits for
    real, with no guard needed, because there is no client-supplied id
    anywhere in the path for a cross-organization value to occur in.
    """
    from workeasy360.platform.onboarding.services import create_organization

    user = user_factory()
    result = create_organization(user_id=user.id, name="Commit Safety Co")
    assert result.created is True  # the transaction committed successfully


def test_seat_assignment_to_another_organizations_membership_still_fails_at_commit(
    organization_factory,
):
    """Task 33's other named example, re-verified here at a real commit
    rather than only through the HTTP-level guard test
    (test_a_seat_cannot_be_assigned_to_another_organizations_member, which
    proves the *application* refuses it - this proves the *database* would
    have too).
    """
    from workeasy360.platform.entitlements.models import SeatAssignment, SeatGrant
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    org_a, _membership_a, _user_a = organization_factory()
    _org_b, membership_b, _user_b = organization_factory()

    with unscoped_access(
        reason="Commit-phase test constructs a tenant row directly, "
        "bypassing assign_seat's guard on purpose",
        module="tests.integration.test_deferred_fk_commit_safety",
    ):
        # Committed cleanly first, in its own scope, so the grant genuinely
        # exists before the bad assignment references it.
        with organization_db_scope(org_a.id):
            grant = SeatGrant(
                organization_id=org_a.id, pool_key="crm", seats_total=5, source_ref="test"
            )
            grant.save()

        bad = SeatAssignment(
            organization_id=org_a.id,
            seat_grant=grant,
            membership_id=membership_b.id,
        )
        # See the RoleAssignment test above for why the raise is expected
        # from the scope's exit (commit), not from save() itself.
        with pytest.raises(IntegrityError):
            with organization_db_scope(org_a.id):
                bad.save()


# --- Task 35: inventory, derived from the live schema ----------------------


def _composite_tenant_fks() -> set[tuple[str, str]]:
    """(table, column) for every composite ``(column, organization_id)``
    foreign key that actually exists in the database.

    Read from PostgreSQL's own catalog rather than from a migration's
    ``COMPOSITE_FKS`` list: that list is split across several migrations,
    and reading one of them is how five composite FKs went uninventoried
    until the Phase 5 completion audit (invitation.invited_by_id,
    role_assignment.granted_by_id, seat_assignment.assigned_by_id,
    subscriptions_item.parent_item_id/subscription_id). The catalog cannot
    be incomplete in that way.
    """
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT c.conrelid::regclass::text, lead_col.attname
            FROM pg_constraint c
            JOIN pg_attribute lead_col
              ON lead_col.attrelid = c.conrelid AND lead_col.attnum = c.conkey[1]
            JOIN pg_attribute org_col
              ON org_col.attrelid = c.conrelid AND org_col.attnum = c.conkey[2]
            WHERE c.contype = 'f'
              AND array_length(c.conkey, 1) = 2
              AND org_col.attname = 'organization_id'
            """
        )
        return {(table, column) for table, column in cur.fetchall()}


def test_every_composite_tenant_fk_is_inventoried_and_reviewed():
    """Task 35: every composite tenant FK in the live schema is classified.

    Every FK in this schema is ``DEFERRABLE INITIALLY DEFERRED`` (Django's
    PostgreSQL default, plus the tenancy migrations), so a bad value is only
    refused at COMMIT - a 500 after the view built its success response.
    Each composite FK must therefore be one of:

    - **application-validated**: a client-supplied id is checked - existence
      *and* same organization - before the write, and refused with a 4xx;
    - **not client-controlled**: every value written comes from the
      server (``ctx``, a row the same transaction created, a server-side
      lookup), never from client input;
    - **not written**: no production code path writes the column at all.

    The full per-column audit, including the non-composite FKs, is in
    docs/phase-5/phase-5-status.md. An entry reaching none of these lists
    fails the test: a new composite FK is a deliberate decision, not a gap.
    """
    import pathlib

    application_validated = {
        # --- Phase 8 extension ---
        ("payments_attempt", "invoice_id"): (
            "commercial/payments/checkout.start_payment locks the invoice through "
            "the tenant-scoped manager -> PaymentRecordNotFound (404) before any "
            "write; staff.mark_invoice_paid locks it inside the named "
            "organization's scope -> 404."
        ),
        ("payments_refund", "attempt_id"): (
            "commercial/payments/refunds.request_refund locks the attempt inside "
            "the named organization's scope (settlement.lock_attempt) -> 404 "
            "before any write."
        ),
        ("entitlements_seat_assignment", "membership_id"): (
            "platform/launcher/services.py assign_seat: "
            "organizations.selectors.active_membership_exists (tenant-scoped "
            "manager: existence and same organization) -> MembershipRequired"
        ),
        # --- Phase 6 ---
        ("webhooks_subscription", "connection_id"): (
            "integration/webhooks/services.set_subscriptions loads the "
            "connection through connectors.selectors.runtime_connection "
            "(tenant-scoped manager) -> IntegrationNotFound (404) before any "
            "write."
        ),
        ("webhooks_delivery", "connection_id"): (
            "Fan-out: connections matched by the organization's own "
            "subscriptions and filtered by active_connection_ids (scoped). "
            "Ping/replay: loaded through the scoped manager -> 404. "
            "Automation: re-read through runtime_connection at execution; an "
            "unavailable connection fails the action instead of writing."
        ),
        ("webhooks_delivery", "replay_of_id"): (
            "integration/webhooks/services.replay locks the original through "
            "the scoped manager -> IntegrationNotFound (404)."
        ),
    }
    not_client_controlled = {
        # --- Phase 9 ---
        ("payments_order", "subscription_id"): (
            "commercial/payments/orders._target: always the organization's own "
            "open subscription, read by subscriptions.selectors.commercial_state "
            "in its scope. A client's activate_subscription_id is only compared "
            "with it (mismatch -> OrderNotAllowed 409), never written. "
            "orders.fulfil: the subscription system.add_paid_line just opened "
            "or locked in the same organization's scope."
        ),
        ("payments_invoice", "order_id"): (
            "The order created in the same transaction (orders._place_payable)."
        ),
        # --- Phase 8 extension ---
        ("organizations_member_sign_in", "membership_id"): (
            "platform/administration/access.record_sign_in: ctx.membership_id - "
            "the member whose request the tenancy middleware just resolved."
        ),
        ("payments_invoice", "subscription_id"): (
            "commercial/payments/invoicing._issue: the subscription read by "
            "subscriptions.selectors.commercial_state inside the same "
            "organization's scope - never client input."
        ),
        ("payments_invoice_line", "invoice_id"): (
            "The invoice created in the same transaction (invoicing._issue)."
        ),
        ("authz_role_assignment", "membership_id"): (
            "authz.services.assign_system_role, from the Membership row "
            "create_organization just created in the same transaction."
        ),
        ("entitlements_seat_assignment", "seat_grant_id"): (
            "Resolved server-side from pool_key, never accepted from the "
            "client - platform.entitlements.services.seats."
        ),
        ("entitlements_seat_assignment", "assigned_by_id"): (
            "ctx.membership_id - the authenticated actor (launcher.services)."
        ),
        ("files_file", "uploaded_by_id"): (
            "platform.files.services sets uploaded_by_id=ctx.membership_id. "
            "register_upload has no HTTP route and no production caller yet."
        ),
        ("subscriptions_item", "subscription_id"): (
            "The subscription is opened or locked by the service through the "
            "tenant-scoped manager (commercial.subscriptions.services "
            "_open_subscription/_lock -> CommercialRecordNotFound)."
        ),
        ("subscriptions_item", "parent_item_id"): (
            "The bundle line created earlier in the same transaction."
        ),
        ("notifications_notification", "recipient_membership_id"): (
            "notifications.services.notify has no production caller. LATENT: "
            "it does not validate recipient_membership_id, so the first "
            "caller must pass only a resolved membership (debt register)."
        ),
        ("notifications_delivery", "notification_id"): (
            "Created from the Notification the same transaction wrote."
        ),
        # --- Phase 6 ---
        ("connectors_secret", "connection_id"): (
            "The Connection created, or locked through the scoped manager, in "
            "the same transaction (connectors.services)."
        ),
        ("webhooks_delivery", "event_id"): (
            "The RoutedEvent the platform recorded itself - at ingestion, "
            "for a ping, or the original's event on a replay."
        ),
        ("webhooks_attempt", "delivery_id"): (
            "The delivery the pipeline claimed under its row lock."
        ),
        ("automation_rule", "current_version_id"): (
            "The version the same transaction created (automation.services)."
        ),
        ("automation_rule_version", "rule_id"): (
            "The rule created, or locked through the scoped manager, in the "
            "same transaction."
        ),
        ("automation_execution", "rule_id"): (
            "Rules matched by the organization-scoped trigger query, or the "
            "schedule rule locked in the same transaction."
        ),
        ("automation_execution", "rule_version_id"): (
            "That rule's current_version, read in the same query."
        ),
        ("automation_execution", "trigger_event_id"): (
            "The RoutedEvent being routed, or the schedule-fired event the "
            "same transaction recorded."
        ),
        # --- Phase 8 ---
        ("ai_usage_line", "execution_id"): (
            "ai.budget._finish: the execution locked (select_for_update) and "
            "fenced on its in-flight status in the same transaction."
        ),
        ("ai_execution_content", "execution_id"): (
            "ai.budget.reserve (async job input: the execution the same "
            "transaction created) and ai.budget._finish (the locked execution)."
        ),
        ("rag_document", "source_id"): (
            "rag.ingestion._source: the calling product's source, resolved or "
            "created server-side by (product_code, source_key) in the same "
            "transaction; no request field names a source id."
        ),
        ("rag_document_version", "document_id"): (
            "rag.ingestion._record: the document locked or created in the "
            "same transaction."
        ),
        ("rag_chunk", "document_id"): (
            "rag.indexing._activate: the document locked in the activation "
            "transaction under the attempt fence."
        ),
        ("rag_chunk", "version_id"): (
            "rag.indexing._activate: the version claimed and locked in the "
            "same transaction."
        ),
    }
    not_written = {
        ("authz_role_assignment", "granted_by_id"): "Never set (NULL).",
        ("organizations_invitation", "invited_by_id"): (
            "Invitation is modelled but never created (no invitation flow)."
        ),
        ("notifications_preference", "membership_id"): "No production writes.",
    }

    declared = _composite_tenant_fks()
    reviewed = set(application_validated) | set(not_client_controlled) | set(not_written)

    unreviewed = declared - reviewed
    assert not unreviewed, (
        "Composite tenant FK(s) in the schema without a Task 35 "
        f"classification: {sorted(unreviewed)}. Classify each in "
        f"{pathlib.Path(__file__).name}."
    )
    # A classification naming a column that no longer exists would mean this
    # test had silently stopped checking it.
    stale = reviewed - declared
    assert not stale, f"Classified FK(s) no longer in the schema: {sorted(stale)}"


# --- the one DB-ONLY path the audit found ----------------------------------


@pytest.mark.parametrize("target", ["plan_version_id", "bundle_version_id"])
def test_publishing_a_price_for_an_unknown_target_is_refused_before_commit(
    target, catalog, platform_admin, in_tenant
):
    """``pricing_plan_price.plan_version_id``/``bundle_version_id`` were
    written straight from the request body (POST .../commercial/prices,
    platform staff only) without being looked up, so an unknown id passed
    the INSERT, the view built a 201, and the deferred FK failed at COMMIT -
    a 500. Now refused with a CatalogValidationError (400) before any
    write, and nothing is committed.
    """
    import uuid

    from workeasy360.commercial.pricing import services as pricing
    from workeasy360.commercial.pricing.models import PlanPrice
    from workeasy360.contracts.enums import BillingInterval
    from workeasy360.contracts.errors import CatalogValidationError

    ctx = platform_admin["ctx"]
    before = PlanPrice.objects.count()
    with pytest.raises(CatalogValidationError) as refused, in_tenant(ctx):
        pricing.publish_price(
            ctx,
            price_book_id=catalog["price_book"].id,
            interval=BillingInterval.MONTHLY,
            amount_minor=1000,
            **{target: uuid.uuid4()},
        )
    assert refused.value.http_status == 400
    assert PlanPrice.objects.count() == before
