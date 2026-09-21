"""Entitlements: deny by default, most-generous-wins, and safe failure."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from workeasy360.contracts.enums import (
    EntitlementReason,
    EntitlementSourceKind,
    EntitlementStatus,
)
from workeasy360.contracts.errors import EntitlementDenied
from workeasy360.contracts.ports.entitlements import UNLIMITED
from workeasy360.platform.entitlements.services import read as entitlements

pytestmark = pytest.mark.django_db


# --- deny by default ------------------------------------------------------


def test_organization_with_no_entitlements_is_denied(two_tenants, in_tenant):
    """Absence of information never *grants*.

    An org with no entitlement row is denied, because the default is
    ``not_entitled`` (06-entitlements.md section 4.4).
    """
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        decision = entitlements.check(a["org"].id, "crm")

    assert not decision.allowed
    assert decision.reason_code is EntitlementReason.NOT_ENTITLED
    assert not decision.write_allowed


def test_require_raises_with_a_machine_readable_reason(two_tenants, in_tenant):
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        with pytest.raises(EntitlementDenied) as exc:
            entitlements.require(a["org"].id, "crm")

    assert exc.value.http_status == 402
    assert exc.value.context["reason_code"] == "not_entitled"


def test_granted_product_is_allowed(two_tenants, in_tenant, entitled):
    a = two_tenants["a"]
    entitled(a["org"].id, "crm")

    with in_tenant(a["ctx"]):
        decision = entitlements.check(a["org"].id, "crm")

    assert decision.allowed
    assert decision.write_allowed
    assert decision.reason_code is EntitlementReason.OK


def test_entitlement_does_not_leak_across_tenants(two_tenants, in_tenant, entitled):
    a, b = two_tenants["a"], two_tenants["b"]
    entitled(a["org"].id, "crm")

    with in_tenant(a["ctx"]):
        assert entitlements.check(a["org"].id, "crm").allowed
    with in_tenant(b["ctx"]):
        assert not entitlements.check(b["org"].id, "crm").allowed


def test_granting_one_product_does_not_grant_another(two_tenants, in_tenant, entitled):
    a = two_tenants["a"]
    entitled(a["org"].id, "crm")

    with in_tenant(a["ctx"]):
        assert entitlements.check(a["org"].id, "crm").allowed
        assert not entitlements.check(a["org"].id, "books").allowed


# --- status semantics -----------------------------------------------------


@pytest.mark.parametrize(
    "status,read_ok,write_ok",
    [
        (EntitlementStatus.TRIAL, True, True),
        (EntitlementStatus.ACTIVE, True, True),
        # past_due and grace restrict nothing: a failed card is usually an
        # expired card, and degrading on first failure costs more in churn
        # than it recovers (06-entitlements.md section 4.2).
        (EntitlementStatus.PAST_DUE, True, True),
        (EntitlementStatus.GRACE, True, True),
        (EntitlementStatus.CANCELLED, True, True),
        # suspended is read-only, not lockout.
        (EntitlementStatus.SUSPENDED, True, False),
        (EntitlementStatus.EXPIRED, False, False),
    ],
)
def test_status_read_write_matrix(
    two_tenants, in_tenant, entitled, status, read_ok, write_ok
):
    a = two_tenants["a"]
    entitled(a["org"].id, "crm", status=status)

    with in_tenant(a["ctx"]):
        read = entitlements.check(a["org"].id, "crm")
        write = entitlements.check(a["org"].id, "crm", write=True)

    assert read.allowed is read_ok
    assert write.allowed is write_ok


def test_suspended_entitlement_reports_a_distinguishable_reason(
    two_tenants, in_tenant, entitled
):
    """The UI must render three different things for "never bought",
    "bought and lapsed" and "no seat". A bare False forces it to guess."""
    a = two_tenants["a"]
    entitled(a["org"].id, "crm", status=EntitlementStatus.SUSPENDED)

    with in_tenant(a["ctx"]):
        decision = entitlements.check(a["org"].id, "crm", write=True)

    assert not decision.allowed
    assert decision.reason_code is EntitlementReason.READ_ONLY
    assert decision.reason_code is not EntitlementReason.NOT_ENTITLED
    assert decision.upgrade_hint


# --- features and quotas --------------------------------------------------


def test_boolean_feature(two_tenants, in_tenant, entitled):
    a = two_tenants["a"]
    entitled(a["org"].id, "crm", features={"crm.ai.summaries": True})

    with in_tenant(a["ctx"]):
        assert entitlements.feature(a["org"].id, "crm.ai.summaries") is True
        assert entitlements.feature(a["org"].id, "crm.ai.absent") is None


def test_quota_feature(two_tenants, in_tenant, entitled):
    a = two_tenants["a"]
    entitled(a["org"].id, "crm", features={"crm.pipelines.max": 50})

    with in_tenant(a["ctx"]):
        assert entitlements.quota(a["org"].id, "crm.pipelines.max") == 50
        assert entitlements.within_quota(a["org"].id, "crm.pipelines.max", used=49)
        assert not entitlements.within_quota(a["org"].id, "crm.pipelines.max", used=50)


def test_unlimited_quota_beats_any_number(two_tenants, in_tenant, entitled):
    """-1 rather than None, so "unlimited" participates in max() during
    most-generous-wins resolution without a special case."""
    a = two_tenants["a"]
    entitled(a["org"].id, "crm", features={"crm.pipelines.max": UNLIMITED})

    with in_tenant(a["ctx"]):
        assert entitlements.quota(a["org"].id, "crm.pipelines.max") == UNLIMITED
        assert entitlements.within_quota(a["org"].id, "crm.pipelines.max", used=10**9)


def test_missing_quota_returns_the_caller_default(two_tenants, in_tenant):
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        assert entitlements.quota(a["org"].id, "crm.pipelines.max", default=3) == 3


def test_most_generous_wins_across_sources(two_tenants, in_tenant):
    """Adding a grant can never *reduce* access.

    "Sum quotas" double-counts when a bundle and a standalone plan both
    include CRM; "latest wins" lets a support grant silently downgrade a
    paying customer. Most-generous is the only rule without those failures.
    """
    from django.utils import timezone

    from workeasy360.platform.entitlements.services.write import (
        ComputedEntitlements,
        ComputedFeature,
        ComputedProduct,
        apply_entitlements,
    )

    a = two_tenants["a"]
    now = timezone.now()

    apply_entitlements(
        ComputedEntitlements(
            organization_id=a["org"].id,
            products=(
                ComputedProduct(
                    product_code="crm",
                    status=EntitlementStatus.SUSPENDED,
                    source_kind=EntitlementSourceKind.SUBSCRIPTION,
                    source_ref="sub:1",
                    seats_total=5,
                    effective_from=now,
                ),
                ComputedProduct(
                    product_code="crm",
                    status=EntitlementStatus.ACTIVE,
                    source_kind=EntitlementSourceKind.BUNDLE,
                    source_ref="bundle:1",
                    seats_total=10,
                    effective_from=now,
                ),
            ),
            features=(
                ComputedFeature(
                    feature_key="crm.pipelines.max",
                    value=5,
                    kind="quota",
                    source_ref="sub:1",
                    effective_from=now,
                ),
                ComputedFeature(
                    feature_key="crm.pipelines.max",
                    value=50,
                    kind="quota",
                    source_ref="bundle:1",
                    effective_from=now,
                ),
            ),
        )
    )

    with in_tenant(a["ctx"]):
        decision = entitlements.check(a["org"].id, "crm", write=True)
        assert decision.allowed, "the more generous ACTIVE source must win"
        assert decision.status is EntitlementStatus.ACTIVE
        # Seats sum: two sources of 5 and 10 genuinely provide 15.
        assert entitlements.quota(a["org"].id, "crm.pipelines.max") == 50


# --- seats ----------------------------------------------------------------


def test_seat_is_separate_from_entitlement(two_tenants, in_tenant, entitled):
    """An org may hold Books with every seat assigned; user N+1 is in an
    entitled org with no seat. That distinction is what makes per-seat
    pricing enforceable."""
    a = two_tenants["a"]
    entitled(a["org"].id, "crm", seats=5)

    with in_tenant(a["ctx"]):
        assert entitlements.check(a["org"].id, "crm").allowed
        assert not entitlements.has_seat(a["org"].id, a["membership"].id, "crm")


# --- caching and query counts --------------------------------------------


def test_repeat_checks_in_one_request_issue_no_further_queries(
    two_tenants, in_tenant, entitled
):
    """The N+1 guard for the hottest path in the platform.

    A check that meets its latency budget by issuing a query per call has
    moved the cost somewhere the budget does not look.
    """
    a = two_tenants["a"]
    entitled(a["org"].id, "crm", features={"crm.pipelines.max": 50})

    with in_tenant(a["ctx"]), entitlements.entitlement_request_scope():
        entitlements.check(a["org"].id, "crm")  # warms the request scope

        with CaptureQueriesContext(connection) as ctx:
            for _ in range(25):
                entitlements.check(a["org"].id, "crm")
                entitlements.feature(a["org"].id, "crm.pipelines.max")
                entitlements.quota(a["org"].id, "crm.pipelines.max")

    assert len(ctx.captured_queries) == 0, (
        f"{len(ctx.captured_queries)} queries for 75 cached checks"
    )


def test_request_scope_reports_hits(two_tenants, in_tenant, entitled):
    a = two_tenants["a"]
    entitled(a["org"].id, "crm")

    with in_tenant(a["ctx"]), entitlements.entitlement_request_scope():
        for _ in range(5):
            entitlements.check(a["org"].id, "crm")
        hits, misses = entitlements.request_cache_stats()

    assert misses == 1, "only the first check should miss"
    assert hits == 4


def test_check_is_correct_when_cache_is_unavailable(
    two_tenants, in_tenant, entitled, monkeypatch
):
    """Redis down means read the snapshot from PostgreSQL.

    Slower, still correct - and never default-allow.
    """
    from django.core import cache as cache_module

    a = two_tenants["a"]
    entitled(a["org"].id, "crm")

    def explode(*args, **kwargs):
        raise ConnectionError("redis is down")

    monkeypatch.setattr(cache_module.cache, "get", explode)
    monkeypatch.setattr(cache_module.cache, "set", explode)

    with in_tenant(a["ctx"]):
        assert entitlements.check(a["org"].id, "crm").allowed
        assert not entitlements.check(a["org"].id, "books").allowed


def test_missing_snapshot_self_heals_from_entitlement_rows(
    two_tenants, in_tenant, entitled
):
    """A newly provisioned org between the entitlement write and the snapshot
    write must still get a correct answer."""
    from workeasy360.platform.entitlements.models import EntitlementSnapshot
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    a = two_tenants["a"]
    entitled(a["org"].id, "crm", features={"crm.pipelines.max": 7})

    with in_tenant(a["ctx"]):
        with unscoped_access(
            reason="Test removes the snapshot to exercise the fallback",
            module="tests.entitlements",
        ):
            EntitlementSnapshot.unscoped.filter(organization_id=a["org"].id).delete()

        decision = entitlements.check(a["org"].id, "crm")
        assert decision.allowed
        assert entitlements.quota(a["org"].id, "crm.pipelines.max") == 7


def test_unknown_status_denies_rather_than_guessing(two_tenants, in_tenant, entitled):
    """An unrecognised status is the one case that must not default-allow."""
    from workeasy360.platform.entitlements.models import EntitlementSnapshot
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    a = two_tenants["a"]
    entitled(a["org"].id, "crm")

    with in_tenant(a["ctx"]):
        with unscoped_access(
            reason="Test corrupts a snapshot status to assert fail-closed",
            module="tests.entitlements",
        ):
            snapshot = EntitlementSnapshot.unscoped.get(organization_id=a["org"].id)
            snapshot.products["crm"]["status"] = "not_a_real_status"
            snapshot.save()

        decision = entitlements.check(a["org"].id, "crm")

    assert not decision.allowed
    assert decision.reason_code is EntitlementReason.UNKNOWN


# --- the single-writer guarantee ------------------------------------------


def test_only_provisioning_imports_the_write_service():
    """A10, asserted in code as well as by import-linter.

    One writer means one place to audit, one place to test, one place to fix,
    and drift with exactly one possible source.
    """
    import ast
    import pathlib

    # parents[3] is the workeasy360/ package root, so paths below are
    # module-relative ("commercial/provisioning/services.py").
    root = pathlib.Path(__file__).resolve().parents[3]
    offenders: list[str] = []

    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if "/tests/" in rel or rel.startswith("tests/") or "migrations" in rel:
            continue
        if rel.startswith("commercial/provisioning/"):
            continue
        if rel.startswith("platform/entitlements/"):
            continue

        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue

        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
            elif isinstance(node, ast.Import):
                module = ",".join(alias.name for alias in node.names)
            if "entitlements.services.write" in module:
                offenders.append(rel)

    assert not offenders, (
        "Only commercial/provisioning may write entitlements; found imports "
        f"in: {sorted(set(offenders))}"
    )
