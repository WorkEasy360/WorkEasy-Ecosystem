"""Phase 12 (G-24 follow-up): a suspended organization is *read-only*, not
unentitled, on every product-facing write path.

Since G-24, ``verify(write=True)`` answers a suspended organization "deny /
suspended". The callers that turn a decision into a protocol answer must keep
telling the product "not accepting new work" rather than "not entitled".
(Event ingestion is pinned by ``tests/security/test_phase6_security.py``.)
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def _suspend(org) -> None:
    from workeasy360.platform.organizations.models import Organization
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with unscoped_access(reason="test suspends an organization", module="tests"):
        Organization.objects.filter(id=org["org"].id).update(status="suspended")


def test_verify_tells_a_product_a_suspended_organization_is_read_only(ai_org):
    from workeasy360.platform.ecosystem import verification

    org = ai_org()
    _suspend(org)

    read = verification.verify(organization_id=org["org"].id, product_code="example")
    assert (read.decision, read.write_allowed) == (verification.ALLOW, False)
    write = verification.verify(
        organization_id=org["org"].id, product_code="example", write=True
    )
    assert (write.decision, write.reason) == (verification.DENY, "suspended")


def test_ai_for_a_suspended_organization_is_disabled_not_unentitled(ai_org):
    from workeasy360.contracts.errors import AIDisabled
    from workeasy360.intelligence.ai import policy

    org = ai_org()
    assert policy.authorize(org["org"].id, "example")
    _suspend(org)

    with pytest.raises(AIDisabled):
        policy.authorize(org["org"].id, "example")
