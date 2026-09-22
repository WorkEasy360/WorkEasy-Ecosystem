"""The correctness alarms, asserted end to end.

11-observability.md section 6 names three signals that must stay at zero and
page on any non-zero value: a tenant query without context, a cross-tenant
access, and an unscoped bypass. Naming them in the metric registry is not
emitting them, and five declared constants had no call site at all -
including the one ``tenancy/unscoped.py`` told the reader it incremented.

They live here rather than in ``platform/observability/tests`` because each is
emitted by a *higher* layer - tenancy, which observability may not import.
The alarm is cross-cutting by construction: observability owns the registry,
and the layers above it own the events.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def test_unscoped_access_is_counted():
    """The signal an anomalous volume of tenant-scoping bypasses trips."""
    from workeasy360.platform.observability.metrics import UNSCOPED_QUERY, metrics
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with unscoped_access(reason="Observability test counts a bypass", module="probe"):
        pass

    assert metrics.value(UNSCOPED_QUERY, scope_module="probe") == 1


def test_a_missing_tenant_context_is_counted(two_tenants):
    """``tenant_context_missing_total`` must stay at zero in a healthy system,
    which is only a useful statement if something can make it non-zero."""
    from workeasy360.contracts.errors import MissingTenantContext
    from workeasy360.platform.observability.metrics import (
        TENANT_CONTEXT_MISSING,
        metrics,
    )
    from workeasy360.platform.organizations.models import Membership

    with pytest.raises(MissingTenantContext):
        list(Membership.objects.all())

    assert metrics.value(TENANT_CONTEXT_MISSING, model="Membership") == 1


def test_a_suspension_write_block_is_counted(
    db, organization_factory, tenant_ctx_factory, in_tenant
):
    """R-10 refusals are a commercial signal, not only a log line."""
    from workeasy360.contracts.enums import OrganizationStatus
    from workeasy360.contracts.errors import OrganizationNotWritable
    from workeasy360.platform.observability.metrics import (
        WRITE_BLOCKED_SUSPENDED,
        metrics,
    )
    from workeasy360.platform.organizations.models import Membership

    org, membership, user = organization_factory(status=OrganizationStatus.SUSPENDED)
    ctx = tenant_ctx_factory(org, membership, user)

    with in_tenant(ctx), pytest.raises(OrganizationNotWritable):
        Membership.objects.filter(id=membership.id).update(member_type="external")

    assert (
        metrics.value(
            WRITE_BLOCKED_SUSPENDED,
            model="Membership",
            organization_status=OrganizationStatus.SUSPENDED.value,
        )
        == 1
    )
