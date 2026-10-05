"""Seed a scratch database for the Phase 8 real-browser verification: a staff
member holding every staff permission (so Super Admin -> AI is reachable)
and an organization owner (so Settings -> AI and Analytics are). Run against
a scratch database only - see README.md."""

import django

django.setup()

from django.utils import timezone

from workeasy360.platform.authz import staff
from workeasy360.platform.authz.permissions import STAFF_PERMISSIONS
from workeasy360.platform.identity.models import User
from workeasy360.platform.onboarding.services import create_organization
from workeasy360.platform.tenancy.unscoped import unscoped_access

PASSWORD = "Browser-Test-Password-1!"


def user(email, **extra):
    with unscoped_access(reason="phase 8 browser seed", module="seed"):
        return User.objects.create_user(
            email=email, password=PASSWORD, status="active", email_verified_at=timezone.now(), **extra
        )


root = user("root@browser.test", is_staff=True)
admin = user("staff@browser.test", is_staff=True)
for permission in STAFF_PERMISSIONS:
    staff.create_grant(
        user_id=admin.id, permission=str(permission.key), granted_by_id=root.id, reason="Browser verification fixture"
    )
owner = user("owner@browser.test")
organization = create_organization(user_id=owner.id, name="Browser Organization", idempotency_key="phase8-browser")
print("SEEDED", organization.slug, organization.organization_id)

# A month of metering, so the usage tables render rows rather than empty
# states. Written directly: the browser check exercises the screens, not the
# gateway (which the backend suites cover end to end).
from workeasy360.intelligence.ai.budget import period_start  # noqa: E402
from workeasy360.intelligence.ai.models import AIBudgetPeriod, AIExecution  # noqa: E402
from workeasy360.platform.tenancy.db import organization_db_scope  # noqa: E402

now = timezone.now()
with unscoped_access(reason="phase 8 browser seed", module="seed"), organization_db_scope(organization.organization_id):
    for scope, spent, requests in (("", 41_250, 3), ("example", 41_250, 3)):
        AIBudgetPeriod.unscoped.create(
            organization_id=organization.organization_id,
            period_start=period_start(now),
            scope=scope,
            spent_micros=spent,
            requests=requests,
            input_tokens=5_400,
            output_tokens=1_300,
        )
    for outcome, status in (("ok", "succeeded"), ("ok", "succeeded"), ("ai_invalid_output", "failed")):
        AIExecution.unscoped.create(
            organization_id=organization.organization_id,
            product_code="example",
            client_id="browser-example",
            operation="invoke",
            status=status,
            outcome=outcome,
            currency="USD",
            model_key="anthropic.claude-opus-5",
            provider_key="anthropic",
            input_tokens=1_800,
            output_tokens=430,
            cost_micros=13_750,
            lease_expires_at=now,
            completed_at=now,
            skill_key="product.example.summary",
            skill_version=1,
        )
print("METERED", organization.organization_id)
