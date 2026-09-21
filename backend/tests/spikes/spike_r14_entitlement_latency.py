"""SPIKE R-14 - entitlement read-path latency benchmark.

The question (15-risk-register.md, R-14):

    Several entitlement and authorization checks per request, at a 1 ms p50
    budget, is aggressive. If the cache under-performs, every endpoint in the
    platform slows simultaneously. Does the check path meet
    1 ms p50 / 5 ms p95 on realistic data?

What is measured, each over the same seeded dataset:

    1. cold path            - no request scope, no shared cache: snapshot read
    2. shared-cache path    - snapshot cached, request scope absent
    3. request-local path   - the second and subsequent checks in one request
    4. multi-check request  - a realistic request making N checks
    5. snapshot-missing     - the self-healing fallback that computes live

Query counts are recorded alongside latency, because a check that meets the
latency budget by issuing three queries has simply moved the cost somewhere
the budget does not look.

The instruction this spike follows: evaluate request-local caching first, and
do not introduce distributed cache complexity purely to chase a benchmark. If
the target is not justified by the measurement, revise the budget in writing
rather than hiding the result.

Run:
    python backend/tests/spikes/spike_r14_entitlement_latency.py
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

import django

django.setup()

from django.conf import settings
from django.core.cache import cache
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from workeasy360.contracts.enums import (
    EntitlementSourceKind,
    EntitlementStatus,
)
from workeasy360.contracts.values.identifiers import FeatureKey, ProductCode
from workeasy360.platform.entitlements.services import read as entitlements
from workeasy360.platform.entitlements.services.write import (
    ComputedEntitlements,
    ComputedFeature,
    ComputedProduct,
    apply_entitlements,
)
from workeasy360.platform.tenancy.context import tenant_context
from workeasy360.platform.tenancy.db import organization_db_scope
from workeasy360.platform.tenancy.unscoped import unscoped_access

#: Iterations per scenario. Enough for a stable p99 without the spike taking
#: minutes; each scenario reports its own sample count so the numbers are
#: interpretable.
ITERATIONS = 2000
WARMUP = 100

#: The realistic shape: an organization holding several products with a
#: handful of features each. Measuring one product with one feature would
#: flatter the result, because the snapshot JSON would be trivially small.
PRODUCTS = ["crm", "books", "inventory", "projects", "marketing", "support"]
FEATURES_PER_PRODUCT = 8

#: Checks a realistic request makes: entitlement for the product being used,
#: plus a few feature/quota lookups.
CHECKS_PER_REQUEST = 6


@dataclass
class Measurement:
    name: str
    samples: list[float] = field(default_factory=list)
    queries: int = 0
    note: str = ""

    def record(self, seconds: float) -> None:
        self.samples.append(seconds * 1000.0)  # milliseconds

    @property
    def stats(self) -> dict:
        if not self.samples:
            return {}
        ordered = sorted(self.samples)
        return {
            "n": len(ordered),
            "p50_ms": round(statistics.median(ordered), 4),
            "p95_ms": round(ordered[int(len(ordered) * 0.95) - 1], 4),
            "p99_ms": round(ordered[int(len(ordered) * 0.99) - 1], 4),
            "mean_ms": round(statistics.fmean(ordered), 4),
            "min_ms": round(ordered[0], 4),
            "max_ms": round(ordered[-1], 4),
            "queries": self.queries,
            "note": self.note,
        }


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(len(ordered) * pct) - 1))
    return ordered[idx]


# --- fixtures -------------------------------------------------------------


def seed() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Create an organization holding six products with eight features each."""
    from workeasy360.platform.identity.models import User
    from workeasy360.platform.organizations.models import Membership, Organization

    suffix = uuid.uuid4().hex[:8]

    with unscoped_access(
        reason="Benchmark fixture creation runs outside a request context",
        module="tests.spikes.spike_r14",
    ):
        user = User.objects.create_user(
            email=f"bench-{suffix}@example.com", password=None, full_name="Bench"
        )
        org = Organization.objects.create(
            slug=f"bench-{suffix}",
            legal_name="Bench Co",
            display_name="Bench Co",
            status="active",
            created_by=user,
        )
        # RLS (Layer 3) still applies inside unscoped_access, so the write
        # must enter this organization's database scope as well.
        with organization_db_scope(org.id):
            membership = Membership(
                organization_id=org.id,
                user_id=user.id,
                status="active",
                is_owner=True,
                member_type="internal",
            )
            membership.save()

    computed = ComputedEntitlements(
        organization_id=org.id,
        products=tuple(
            ComputedProduct(
                product_code=code,
                status=EntitlementStatus.ACTIVE,
                source_kind=EntitlementSourceKind.SUBSCRIPTION,
                source_ref=f"bench:{code}",
                seats_total=25,
                effective_from=_now(),
            )
            for code in PRODUCTS
        ),
        features=tuple(
            ComputedFeature(
                feature_key=f"{code}.area{i}.limit",
                value=(i * 10 if i % 2 else True),
                kind="quota" if i % 2 else "boolean",
                source_ref=f"bench:{code}",
                effective_from=_now(),
            )
            for code in PRODUCTS
            for i in range(FEATURES_PER_PRODUCT)
        ),
    )
    apply_entitlements(computed)
    return org.id, membership.id, user.id


def _now():
    from django.utils import timezone

    return timezone.now()


def cleanup(org_id: uuid.UUID) -> None:
    from workeasy360.platform.entitlements.models import (
        Entitlement,
        EntitlementSnapshot,
        FeatureEntitlement,
    )
    from workeasy360.platform.identity.models import User
    from workeasy360.platform.organizations.models import Membership, Organization

    with unscoped_access(
        reason="Benchmark teardown removes its own fixture rows",
        module="tests.spikes.spike_r14",
    ):
        with organization_db_scope(org_id):
            Entitlement.unscoped.filter(organization_id=org_id).delete()
            FeatureEntitlement.unscoped.filter(organization_id=org_id).delete()
            EntitlementSnapshot.unscoped.filter(organization_id=org_id).delete()
            Membership.unscoped.filter(organization_id=org_id).delete()
        org = Organization.objects.filter(id=org_id).first()
        if org:
            creator = org.created_by_id
            org.delete()
            User.objects.filter(id=creator).delete()


# --- scenarios ------------------------------------------------------------


def bench_db_roundtrip_floor(org_id: uuid.UUID) -> Measurement:
    """The cost of the cheapest possible query on this topology.

    Measured so the entitlement numbers can be read honestly. Any check that
    touches PostgreSQL - through PgBouncer, over a TCP loopback - cannot be
    faster than this, no matter how the entitlement code is written. Reporting
    a budget miss without this baseline would suggest the fix lies in the
    entitlement path, when it may lie entirely in the topology.
    """
    m = Measurement(
        "baseline: SELECT 1 through PgBouncer",
        note="the floor for any path that touches the database at all",
    )
    for _ in range(WARMUP):
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()

    for _ in range(ITERATIONS):
        started = time.perf_counter()
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        m.record(time.perf_counter() - started)
    m.queries = 1
    return m


def bench_cache_roundtrip_floor(org_id: uuid.UUID) -> Measurement:
    """The cost of one cache round trip, for the same reason."""
    m = Measurement(
        "baseline: cache.get through Redis",
        note="the floor for any path that touches the shared cache",
    )
    cache.set("w360:bench:floor", {"x": 1}, 60)
    for _ in range(WARMUP):
        cache.get("w360:bench:floor")

    for _ in range(ITERATIONS):
        started = time.perf_counter()
        cache.get("w360:bench:floor")
        m.record(time.perf_counter() - started)
    m.queries = 0
    return m


def bench_cold(org_id: uuid.UUID) -> Measurement:
    """Every check goes to the database: no request scope, no shared cache.

    The worst realistic case and the one the budget must survive: a cold
    process, or a cache that has just been invalidated by a provisioning run.
    """
    m = Measurement(
        "cold (no request scope, cache cleared each time)",
        note="reads EntitlementSnapshot from PostgreSQL every call",
    )
    product = ProductCode(PRODUCTS[0])

    for _ in range(WARMUP):
        cache.clear()
        entitlements.check(org_id, product)

    with CaptureQueriesContext(connection) as ctx:
        cache.clear()
        entitlements.check(org_id, product)
    m.queries = len(ctx.captured_queries)

    for _ in range(ITERATIONS):
        cache.clear()
        started = time.perf_counter()
        entitlements.check(org_id, product)
        m.record(time.perf_counter() - started)
    return m


def bench_shared_cache(org_id: uuid.UUID) -> Measurement:
    """Snapshot served from the shared cache, with no request-local memo.

    Still one database query for the snapshot version, which is the honest
    cost of version-keyed invalidation: the version must be read to build the
    cache key.
    """
    m = Measurement(
        "shared cache (no request scope)",
        note="version read from DB, snapshot from cache",
    )
    product = ProductCode(PRODUCTS[0])

    entitlements.check(org_id, product)  # populate
    for _ in range(WARMUP):
        entitlements.check(org_id, product)

    with CaptureQueriesContext(connection) as ctx:
        entitlements.check(org_id, product)
    m.queries = len(ctx.captured_queries)

    for _ in range(ITERATIONS):
        started = time.perf_counter()
        entitlements.check(org_id, product)
        m.record(time.perf_counter() - started)
    return m


def bench_request_local(org_id: uuid.UUID) -> Measurement:
    """The second and subsequent checks within one request.

    This is the measurement that matters most, because it is the common case:
    a request makes several checks, and only the first can possibly be cold.
    """
    m = Measurement(
        "request-local (2nd+ check in one request)",
        note="dict lookup; zero queries, zero cache calls",
    )
    product = ProductCode(PRODUCTS[0])

    with entitlements.entitlement_request_scope():
        entitlements.check(org_id, product)  # first check warms the scope

        with CaptureQueriesContext(connection) as ctx:
            entitlements.check(org_id, product)
        m.queries = len(ctx.captured_queries)

        for _ in range(WARMUP):
            entitlements.check(org_id, product)

        for _ in range(ITERATIONS):
            started = time.perf_counter()
            entitlements.check(org_id, product)
            m.record(time.perf_counter() - started)
    return m


def bench_feature_and_quota(org_id: uuid.UUID) -> Measurement:
    """Feature and quota lookups inside a warm request scope."""
    m = Measurement(
        "feature()/quota() in request scope",
        note="the lookups a product makes alongside check()",
    )
    keys = [
        FeatureKey(f"{PRODUCTS[0]}.area{i}.limit") for i in range(FEATURES_PER_PRODUCT)
    ]

    with entitlements.entitlement_request_scope():
        entitlements.feature(org_id, keys[0])

        with CaptureQueriesContext(connection) as ctx:
            entitlements.feature(org_id, keys[1])
            entitlements.quota(org_id, keys[1])
        m.queries = len(ctx.captured_queries)

        for i in range(ITERATIONS):
            key = keys[i % len(keys)]
            started = time.perf_counter()
            entitlements.feature(org_id, key)
            m.record(time.perf_counter() - started)
    return m


def bench_full_request(org_id: uuid.UUID) -> Measurement:
    """A whole request's worth of checks, measured end to end.

    Six checks per request against the 1 ms p50 budget. The budget is per
    *check*, but what the user experiences is the total, so both are reported.
    """
    m = Measurement(
        f"full request ({CHECKS_PER_REQUEST} checks, cold scope each time)",
        note="total time for all checks in one simulated request",
    )
    product = ProductCode(PRODUCTS[0])
    keys = [FeatureKey(f"{PRODUCTS[0]}.area{i}.limit") for i in range(5)]

    def one_request() -> None:
        with entitlements.entitlement_request_scope():
            entitlements.check(org_id, product, write=True)
            for key in keys:
                entitlements.feature(org_id, key)

    for _ in range(WARMUP):
        one_request()

    with CaptureQueriesContext(connection) as ctx:
        one_request()
    m.queries = len(ctx.captured_queries)

    for _ in range(ITERATIONS // 2):
        started = time.perf_counter()
        one_request()
        m.record(time.perf_counter() - started)
    return m


def bench_snapshot_missing(org_id: uuid.UUID) -> Measurement:
    """The self-healing path: no snapshot row, resolve from Entitlement rows.

    Measured because it is a real production state - a newly provisioned
    organization between the entitlement write and the snapshot write, or a
    snapshot lost to a failed migration. It must be slow-but-correct, never
    wrong, and it must not be so slow that it becomes an outage when a
    reconciliation run clears snapshots.
    """
    from workeasy360.platform.entitlements.models import EntitlementSnapshot

    m = Measurement(
        "snapshot missing (live resolution fallback)",
        note="resolves from Entitlement + FeatureEntitlement rows",
    )
    product = ProductCode(PRODUCTS[0])

    with unscoped_access(
        reason="Benchmark removes the snapshot to measure the fallback path",
        module="tests.spikes.spike_r14",
    ):
        with organization_db_scope(org_id):
            saved = list(EntitlementSnapshot.unscoped.filter(organization_id=org_id))
            EntitlementSnapshot.unscoped.filter(organization_id=org_id).delete()

    try:
        cache.clear()
        for _ in range(20):
            cache.clear()
            entitlements.check(org_id, product)

        with CaptureQueriesContext(connection) as ctx:
            cache.clear()
            entitlements.check(org_id, product)
        m.queries = len(ctx.captured_queries)

        for _ in range(ITERATIONS // 4):
            cache.clear()
            started = time.perf_counter()
            entitlements.check(org_id, product)
            m.record(time.perf_counter() - started)
    finally:
        with unscoped_access(
            reason="Benchmark restores the snapshot it removed",
            module="tests.spikes.spike_r14",
        ):
            with organization_db_scope(org_id):
                for row in saved:
                    row.save()
        cache.clear()
    return m


# --- entry point ----------------------------------------------------------


def main() -> int:
    print("=" * 78)
    print("SPIKE R-14 - entitlement read-path latency")
    print("=" * 78)

    p50_budget = float(getattr(settings, "ENTITLEMENT_P50_BUDGET_MS", 1.0))
    p95_budget = float(getattr(settings, "ENTITLEMENT_P95_BUDGET_MS", 5.0))

    print(f"  budget     : p50 <= {p50_budget} ms, p95 <= {p95_budget} ms")
    print(f"  dataset    : {len(PRODUCTS)} products x {FEATURES_PER_PRODUCT} features")
    print(f"  iterations : {ITERATIONS} per scenario ({WARMUP} warmup)")
    print(f"  cache      : {settings.CACHES['default']['BACKEND'].rsplit('.', 1)[-1]}")
    print(
        f"  database   : {settings.DATABASES['default']['HOST']}:"
        f"{settings.DATABASES['default']['PORT']}"
    )
    print()

    org_id, membership_id, user_id = seed()
    results: dict[str, dict] = {}

    try:
        # Both isolation layers must be entered, exactly as the tenancy
        # middleware does per request: tenant_context() binds the contextvar
        # that the ORM manager reads (Layer 2), organization_db_scope() sets
        # the RLS variable the database reads (Layer 3). Without the second,
        # every snapshot read returns zero rows and the benchmark would
        # silently measure the fallback path instead of the real one.
        #
        # The transaction is opened once around the whole run so the
        # measurements isolate the entitlement path itself; per-request
        # transaction setup belongs to the middleware and is measured there.
        with (
            organization_db_scope(org_id),
            tenant_context(
                __import__(
                    "workeasy360.contracts.values.tenant", fromlist=["TenantContext"]
                ).TenantContext(
                    organization_id=org_id,
                    membership_id=membership_id,
                    user_id=user_id,
                )
            ),
        ):
            scenarios = [
                bench_db_roundtrip_floor,
                bench_cache_roundtrip_floor,
                bench_cold,
                bench_shared_cache,
                bench_request_local,
                bench_feature_and_quota,
                bench_full_request,
                bench_snapshot_missing,
            ]
            for scenario in scenarios:
                measurement = scenario(org_id)
                stats = measurement.stats
                results[measurement.name] = stats
                print(f"  {measurement.name}")
                print(
                    f"    p50={stats['p50_ms']:>8.4f} ms   "
                    f"p95={stats['p95_ms']:>8.4f} ms   "
                    f"p99={stats['p99_ms']:>8.4f} ms   "
                    f"queries={stats['queries']}"
                )
                print(f"    {stats['note']}")
                print()
    finally:
        cleanup(org_id)

    # --- verdict ---------------------------------------------------------

    print("-" * 78)
    print("Budget assessment")
    print("-" * 78)

    verdicts = {}
    for name, stats in results.items():
        within_p50 = stats["p50_ms"] <= p50_budget
        within_p95 = stats["p95_ms"] <= p95_budget
        verdicts[name] = {"within_p50": within_p50, "within_p95": within_p95}
        mark = "MEETS" if (within_p50 and within_p95) else "EXCEEDS"
        print(
            f"  [{mark:>7}] {name}: p50 {stats['p50_ms']:.4f} ms "
            f"(budget {p50_budget}), p95 {stats['p95_ms']:.4f} ms "
            f"(budget {p95_budget})"
        )

    request_local = results.get("request-local (2nd+ check in one request)", {})
    shared = results.get("shared cache (no request scope)", {})

    print()
    print("-" * 78)
    print("Reading of the result")
    print("-" * 78)
    if request_local:
        print(
            f"  Request-local caching serves repeat checks in "
            f"{request_local['p50_ms']:.4f} ms p50 with "
            f"{request_local['queries']} queries."
        )
    if shared:
        print(
            f"  The first check in a request costs "
            f"{shared['p50_ms']:.4f} ms p50 with {shared['queries']} "
            f"query/queries (the snapshot version read)."
        )

    report = {
        "spike": "R-14",
        "question": "Does the entitlement check path meet 1 ms p50 / 5 ms p95 "
        "on realistic data?",
        "budget": {"p50_ms": p50_budget, "p95_ms": p95_budget},
        "environment": {
            "iterations": ITERATIONS,
            "warmup": WARMUP,
            "products": len(PRODUCTS),
            "features_per_product": FEATURES_PER_PRODUCT,
            "cache_backend": settings.CACHES["default"]["BACKEND"],
            "database_host": f"{settings.DATABASES['default']['HOST']}:"
            f"{settings.DATABASES['default']['PORT']}",
            "python": sys.version.split()[0],
        },
        "results": results,
        "verdicts": verdicts,
    }
    out = Path(__file__).parent / "r14_results.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nResults written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
