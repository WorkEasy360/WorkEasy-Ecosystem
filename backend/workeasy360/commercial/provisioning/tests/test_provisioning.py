"""Entitlement provisioning: the translation of commercial state into access.

The properties asserted here are the ones the whole design rests on.
``compute_entitlements`` must be a deterministic function of durable state,
``apply_entitlements`` must be idempotent, and commercial status must map to
entitlement status **by construction** rather than by a hand-maintained table
that will drift.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from workeasy360.commercial.provisioning import services as provisioning
from workeasy360.commercial.subscriptions import services
from workeasy360.commercial.subscriptions.models import Subscription
from workeasy360.contracts.enums import (
    EntitlementSourceKind,
    EntitlementStatus,
    SubscriptionStatus,
)
from workeasy360.platform.entitlements.services import read as entitlements

pytestmark = pytest.mark.django_db


def key() -> str:
    return uuid.uuid4().hex


@pytest.fixture
def subscribed(catalog, customer, billing_account, in_tenant):
    """A customer on CRM Professional, provisioned."""
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()
    return {"ctx": ctx, "item": item, "subscription": subscription}


# --- registry-derived invariants ------------------------------------------


@pytest.mark.parametrize("status", list(SubscriptionStatus))
def test_every_subscription_status_maps_to_an_entitlement_status(status):
    """Derived from the live enum rather than a maintained list.

    A subscription status added tomorrow is covered the moment it exists. A
    hand-written table would be missing it, and the failure mode of a missing
    mapping is a customer with no access at all.
    """
    mapped = status.entitlement_status
    assert isinstance(mapped, EntitlementStatus)


@pytest.mark.parametrize("status", list(SubscriptionStatus))
def test_commercial_status_produces_the_expected_entitlement_status(
    status, subscribed, in_tenant
):
    """The consistency invariant, for every status the machine can reach.

    Written against the live enum so it cannot silently stop covering a
    status; the mapping itself is asserted as a *behaviour* of provisioning
    rather than as a property of the enum.
    """
    ctx = subscribed["ctx"]
    subscription = subscribed["subscription"]

    with in_tenant(ctx):
        Subscription.objects.filter(id=subscription.id).update(status=status.value)

    provisioning.reconcile(ctx.organization_id)

    with in_tenant(ctx):
        decision = entitlements.check(ctx.organization_id, "crm")

    if status is SubscriptionStatus.EXPIRED:
        assert not decision.allowed
        return

    assert decision.status is status.entitlement_status
    assert decision.allowed is status.entitlement_status.allows_read
    write = None
    with in_tenant(ctx):
        write = entitlements.check(ctx.organization_id, "crm", write=True)
    assert write.allowed is status.entitlement_status.allows_write


def test_suspended_is_read_only_not_locked_out(subscribed, in_tenant):
    ctx = subscribed["ctx"]
    with in_tenant(ctx):
        Subscription.objects.filter(id=subscribed["subscription"].id).update(
            status=SubscriptionStatus.SUSPENDED.value
        )
    provisioning.reconcile(ctx.organization_id)

    with in_tenant(ctx):
        assert entitlements.check(ctx.organization_id, "crm").allowed
        assert not entitlements.check(ctx.organization_id, "crm", write=True).allowed
        # The data is still readable, which is what keeps this on the right
        # side of data-protection obligations.
        assert "crm" in [str(p) for p in entitlements.products(ctx.organization_id)]


def test_past_due_and_grace_restrict_nothing(subscribed, in_tenant):
    """A failed card is usually an expired card. Escalation is by notification
    intensity, not by capability removal, until grace elapses."""
    ctx = subscribed["ctx"]
    for status in (SubscriptionStatus.PAST_DUE, SubscriptionStatus.GRACE):
        with in_tenant(ctx):
            Subscription.objects.filter(id=subscribed["subscription"].id).update(
                status=status.value
            )
        provisioning.reconcile(ctx.organization_id)
        with in_tenant(ctx):
            decision = entitlements.check(ctx.organization_id, "crm", write=True)
        assert decision.allowed, f"{status.value} wrongly restricted writes"


# --- determinism and idempotency -----------------------------------------


def test_computation_is_deterministic(subscribed):
    ctx = subscribed["ctx"]
    at = timezone.now()
    first = provisioning.compute_entitlements(ctx.organization_id, at)
    second = provisioning.compute_entitlements(ctx.organization_id, at)

    def shape(computed):
        return (
            sorted(
                (p.product_code, p.status, p.source_ref, p.seats_total)
                for p in computed.products
            ),
            sorted(
                (f.feature_key, str(f.value), f.source_ref) for f in computed.features
            ),
            sorted((s.pool_key, s.seats_total, s.source_ref) for s in computed.seats),
        )

    assert shape(first) == shape(second)


def test_reconciling_twice_produces_the_same_rows(subscribed, in_tenant):
    """Idempotence is what makes a duplicated or replayed trigger harmless."""
    from workeasy360.platform.entitlements.selectors import stored_snapshot

    ctx = subscribed["ctx"]
    provisioning.reconcile(ctx.organization_id)
    first = stored_snapshot(ctx.organization_id)
    provisioning.reconcile(ctx.organization_id)
    second = stored_snapshot(ctx.organization_id)

    assert first is not None and second is not None
    assert first.products == second.products
    assert first.features == second.features
    # The version still advances, which is deliberate: it invalidates caches
    # without a diff having to decide whether to.
    assert second.version > first.version


def test_entitlements_rebuild_from_nothing(subscribed, in_tenant):
    """The disaster-recovery property.

    If the entitlement tables were lost entirely, one reconciliation pass
    rebuilds them from subscriptions. Asserted by actually deleting them.
    """
    from workeasy360.platform.entitlements.models import (
        Entitlement,
        EntitlementSnapshot,
    )
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    ctx = subscribed["ctx"]
    with (
        unscoped_access(
            reason="Test destroys the entitlement read model to prove recovery",
            module="tests.provisioning",
        ),
        organization_db_scope(ctx.organization_id),
    ):
        Entitlement.unscoped.filter(organization_id=ctx.organization_id).delete()
        EntitlementSnapshot.unscoped.filter(organization_id=ctx.organization_id).delete()

    with in_tenant(ctx):
        assert not entitlements.check(ctx.organization_id, "crm").allowed

    provisioning.reconcile(ctx.organization_id)

    with in_tenant(ctx):
        assert entitlements.check(ctx.organization_id, "crm").allowed
        assert entitlements.quota(ctx.organization_id, "crm.pipelines.max") == 25


def test_drift_is_detected_repaired_and_signalled(subscribed, in_tenant):
    """Drift is a bug and is never silently fixed.

    Measured against the **snapshot**, because that is the row every read
    resolves from - it is what the customer effectively has. The underlying
    ``Entitlement`` rows are rewritten by the same pass regardless, so a
    corruption in either is repaired; only a difference in the effective
    answer is worth waking somebody for.
    """
    from workeasy360.integration.events.models import OutboxEvent
    from workeasy360.platform.entitlements.models import (
        Entitlement,
        EntitlementSnapshot,
    )
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    ctx = subscribed["ctx"]
    provisioning.reconcile(ctx.organization_id)

    with (
        unscoped_access(
            reason="Test corrupts the entitlement read model to exercise repair",
            module="tests.provisioning",
        ),
        organization_db_scope(ctx.organization_id),
    ):
        EntitlementSnapshot.unscoped.filter(organization_id=ctx.organization_id).update(
            products={"crm": {"status": "expired", "seats": 1, "write": False}},
            features={},
        )
        Entitlement.unscoped.filter(
            organization_id=ctx.organization_id, product_code="crm"
        ).update(status=EntitlementStatus.EXPIRED.value, seats_total=1)

    report = provisioning.reconcile_and_report_drift(ctx.organization_id)

    assert report.has_drift
    assert "crm" in report.products_changed
    assert "crm.pipelines.max" in report.features_changed
    assert OutboxEvent.objects.filter(
        event_type="entitlements.drift.repaired",
        organization_id=ctx.organization_id,
    ).exists()

    with in_tenant(ctx):
        assert entitlements.check(ctx.organization_id, "crm").allowed
        assert entitlements.quota(ctx.organization_id, "crm.pipelines.max") == 25

    with (
        unscoped_access(
            reason="Test verifies the underlying rows were repaired too",
            module="tests.provisioning",
        ),
        organization_db_scope(ctx.organization_id),
    ):
        row = Entitlement.unscoped.get(
            organization_id=ctx.organization_id, product_code="crm"
        )
    assert row.status == EntitlementStatus.ACTIVE.value
    assert row.seats_total == 10


def test_no_drift_produces_no_signal(subscribed):
    """A detector that cries wolf is one people stop believing."""
    from workeasy360.integration.events.models import OutboxEvent

    ctx = subscribed["ctx"]
    provisioning.reconcile(ctx.organization_id)
    report = provisioning.reconcile_and_report_drift(ctx.organization_id)

    assert not report.has_drift
    assert not OutboxEvent.objects.filter(
        event_type="entitlements.drift.repaired"
    ).exists()


# --- sources --------------------------------------------------------------


def test_a_support_grant_survives_reconciliation(subscribed, in_tenant):
    """Grants are an *input* to the computation, not an override applied
    afterwards, so "support fixes it, the nightly job undoes it" cannot
    happen."""
    from workeasy360.platform.entitlements.models import EntitlementGrant

    ctx = subscribed["ctx"]
    with in_tenant(ctx):
        grant = EntitlementGrant(
            organization_id=ctx.organization_id,
            product_code="books",
            reason="Goodwill after an outage",
            granted_by_id=ctx.user_id,
            expires_at=timezone.now() + timedelta(days=30),
        )
        grant.save()

    provisioning.reconcile(ctx.organization_id)
    provisioning.reconcile(ctx.organization_id)

    with in_tenant(ctx):
        assert entitlements.check(ctx.organization_id, "books").allowed


def test_overlapping_sources_resolve_most_generously(
    catalog, customer, billing_account, in_tenant
):
    """A bundle and a support grant for the same product must not reduce
    access. Adding a grant can never take something away."""
    from workeasy360.platform.entitlements.models import EntitlementGrant

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        grant = EntitlementGrant(
            organization_id=ctx.organization_id,
            feature_key="crm.pipelines.max",
            value=100,
            reason="Migration head-room",
            granted_by_id=ctx.user_id,
            expires_at=timezone.now() + timedelta(days=7),
        )
        grant.save()

    provisioning.reconcile(ctx.organization_id)

    with in_tenant(ctx):
        # Starter grants 3; the grant raises it to 100. Most generous wins.
        assert entitlements.quota(ctx.organization_id, "crm.pipelines.max") == 100


def test_bundle_items_are_recorded_as_a_bundle_source(
    catalog, customer, billing_account, in_tenant
):
    from workeasy360.platform.entitlements.models import Entitlement
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_bundle(
            ctx, bundle_version_id=catalog["bundle_version"].id, idempotency_key=key()
        )

    with (
        unscoped_access(
            reason="Test inspects the entitlement rows provisioning wrote",
            module="tests.provisioning",
        ),
        organization_db_scope(ctx.organization_id),
    ):
        kinds = set(
            Entitlement.unscoped.filter(organization_id=ctx.organization_id).values_list(
                "source_kind", flat=True
            )
        )
    assert kinds == {EntitlementSourceKind.BUNDLE.value}


def test_each_item_is_its_own_entitlement_source(
    catalog, customer, billing_account, in_tenant
):
    """Per item, not per subscription.

    An organization holding CRM from two sources genuinely has two, and
    collapsing them would make most-generous-wins unable to see the second.
    """
    from workeasy360.platform.entitlements.models import Entitlement
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_bundle(
            ctx, bundle_version_id=catalog["bundle_version"].id, idempotency_key=key()
        )

    with (
        unscoped_access(
            reason="Test inspects entitlement source references",
            module="tests.provisioning",
        ),
        organization_db_scope(ctx.organization_id),
    ):
        refs = set(
            Entitlement.unscoped.filter(organization_id=ctx.organization_id).values_list(
                "source_ref", flat=True
            )
        )
    assert len(refs) == 2
    assert all(ref.startswith("subscriptions.item:") for ref in refs)


# --- single writer --------------------------------------------------------


def _imported_modules(path):
    """Modules a file actually imports, by AST.

    Not a text search. A docstring that *names* the writer is documentation,
    and a check that cannot tell the two apart trains people to work around
    it rather than to obey it.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
        elif isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
    return found


def test_provisioning_is_the_only_entitlement_writer():
    """Asserted structurally, over the whole source tree.

    The import contract covers the modules it names; this covers everything,
    including a module added tomorrow that nobody thought to add to it.
    """
    import pathlib

    package = pathlib.Path(__file__).resolve().parents[4] / "workeasy360"
    offenders = []
    for path in package.rglob("*.py"):
        rel = path.relative_to(package).as_posix()
        if rel.startswith(("platform/entitlements/", "commercial/provisioning/")):
            continue
        if "/tests/" in rel or rel.startswith("tests/"):
            continue
        for module in _imported_modules(path):
            if module.startswith("workeasy360.platform.entitlements.services.write"):
                offenders.append(rel)
    assert not offenders, f"Modules importing the entitlement writer: {offenders}"


def test_products_import_nothing_commercial():
    """A product asking what plan an organization is on is a design error.

    Products reach entitlements through the contracts port and know nothing
    else. Checked on imports rather than on text, so a comment explaining the
    rule does not itself violate it.
    """
    import pathlib

    package = pathlib.Path(__file__).resolve().parents[4] / "workeasy360"
    offenders = []
    for path in (package / "products").rglob("*.py"):
        for module in _imported_modules(path):
            if module.startswith(
                ("workeasy360.commercial", "workeasy360.platform.entitlements")
            ):
                offenders.append(f"{path.name}: {module}")
    assert not offenders, offenders


# --- effective timing -----------------------------------------------------


def test_access_is_computed_at_a_moment_not_from_a_flag(
    catalog, customer, billing_account, in_tenant
):
    """One place applies the clock.

    Products never check a date; provisioning evaluates every durable
    timestamp at a single ``at``, which is also what lets a historical
    recomputation be correct.
    """
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()
        services.cancel_subscription(
            ctx, subscription_id=subscription.id, idempotency_key=key()
        )

    before = provisioning.compute_entitlements(
        ctx.organization_id, subscription.current_period_end - timedelta(minutes=1)
    )
    after = provisioning.compute_entitlements(
        ctx.organization_id, subscription.current_period_end + timedelta(minutes=1)
    )

    assert [p.product_code for p in before.products] == ["crm"]
    assert after.products == ()


def test_a_reconciliation_pass_applies_time_based_changes(
    catalog, customer, billing_account, in_tenant
):
    """No command is needed - only a clock. This is what makes the scheduled
    pass the mechanism for scheduled downgrades and expiries."""
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()
        services.change_plan(
            ctx,
            subscription_item_id=item.id,
            to_plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )

    with in_tenant(ctx):
        assert entitlements.quota(ctx.organization_id, "crm.pipelines.max") == 25

    provisioning.reconcile(
        ctx.organization_id, at=subscription.current_period_end + timedelta(seconds=1)
    )

    with in_tenant(ctx):
        assert entitlements.quota(ctx.organization_id, "crm.pipelines.max") == 3


def test_a_pending_change_does_not_grant_two_products(
    catalog, customer, billing_account, in_tenant
):
    """While a period-end change is pending, exactly one line is effective.

    Lives here rather than in the subscriptions suite because it asserts a
    property of the *computation*: two lines exist for one product, and the
    effective-dating filter must pick exactly one.
    """
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        services.change_plan(
            ctx,
            subscription_item_id=item.id,
            to_plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )

    computed = provisioning.compute_entitlements(ctx.organization_id)
    crm = [p for p in computed.products if p.product_code == "crm"]
    assert len(crm) == 1, "a pending change granted the product twice"
    assert crm[0].seats_total == 10, "the pending downgrade applied early"


def test_reconcile_all_visits_every_organization(subscribed):
    from workeasy360.commercial.provisioning.tasks import reconcile_all

    result = reconcile_all()
    assert result["visited"] >= 1
    assert result["failed"] == 0


def test_a_failing_organization_does_not_stop_the_pass(subscribed, monkeypatch):
    """One corrupt tenant must not stop reconciliation for everyone else."""
    from workeasy360.commercial.provisioning import services as module
    from workeasy360.commercial.provisioning.tasks import reconcile_all

    calls = {"n": 0}
    original = module.reconcile_and_report_drift

    def explode(organization_id, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("corrupt tenant")
        return original(organization_id, **kwargs)

    monkeypatch.setattr(module, "reconcile_and_report_drift", explode)
    result = reconcile_all()
    assert result["failed"] == 1
