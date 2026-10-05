"""Load-test data for the Phase 12 staging rehearsal (runs INSIDE the api container).

    docker compose ... exec -T -e LOAD_USERS=500 -e LOAD_PASSWORD=... api \
        python manage.py shell < scripts/phase12_staging/seed_load.py

Dedicated test data only (Step 25): LOAD_USERS customers, each the owner of
its own organization holding an easy_crm trial and a seat, created through the
same onboarding, trial and seat services the API calls; and one reseller with
LOAD_PARTNER_CUSTOMERS provisioned customers for the partner customer list.
Prints one JSON line per account for the k6 script.
"""

import json
import os
import uuid

from workeasy360.commercial.partners import customers
from workeasy360.commercial.partners import services as partners
from workeasy360.commercial.partners.access import partner_actor
from workeasy360.commercial.subscriptions import trials
from workeasy360.contracts.enums import OrganizationStatus, PartnerStatus
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz.control_plane import acting_scope, platform_context
from workeasy360.platform.entitlements.services.seats import assign_seat
from workeasy360.platform.identity import lifecycle
from workeasy360.platform.identity.models import User
from workeasy360.platform.onboarding import services as onboarding
from workeasy360.platform.organizations.models import Organization
from workeasy360.platform.organizations.resolver import resolve_by_organization_id
from workeasy360.platform.tenancy.context import tenant_context
from workeasy360.platform.tenancy.db import tenant_db_scope
from workeasy360.platform.tenancy.unscoped import unscoped_access

N = int(os.environ.get("LOAD_USERS", "500"))
PARTNER_CUSTOMERS = int(os.environ.get("LOAD_PARTNER_CUSTOMERS", "60"))
PASSWORD = os.environ["LOAD_PASSWORD"]
RUN = uuid.uuid4().hex[:6]


def person(email, name):
    with unscoped_access(reason="phase 12 load seed", module="seed"):
        user = User.objects.create_user(email=email, password=None)
        lifecycle.complete_with_password(user, PASSWORD, full_name=name, activate=True)
    return user


for i in range(N):
    email = f"load{i:04d}-{RUN}@load-customers.net"
    user = person(email, f"Load {i}")
    created = onboarding.create_organization(user_id=user.id, name=f"Load Org {i} {RUN}")
    org_id = getattr(created, "organization_id", None) or getattr(created, "id")
    res = resolve_by_organization_id(user_id=user.id, organization_id=org_id)
    ctx = TenantContext(
        organization_id=res.organization_id,
        membership_id=res.membership_id,
        user_id=user.id,
        organization_status=OrganizationStatus.ACTIVE,
    )
    with tenant_context(ctx), tenant_db_scope(ctx):
        trials.start_product_trial(
            ctx, product_code="easy_crm", plan_code="easy_crm_team", idempotency_key=uuid.uuid4().hex
        )
    with tenant_context(ctx), tenant_db_scope(ctx):
        assign_seat(res.organization_id, pool_key="easy_crm", membership_id=res.membership_id)
    slug = Organization.objects.get(id=res.organization_id).slug
    print("LOADUSER " + json.dumps({"email": email, "slug": slug}))

admin = User.objects.get(email="superadmin@staging.workeasy360.com")
sctx = platform_context(user_id=admin.id, aal=2)
with acting_scope(sctx):
    partner = partners.create_partner(
        sctx,
        code=f"load-reseller-{RUN}",
        display_name=f"Load Reseller {RUN}",
        legal_name="Load Reseller Ltd",
        contact_name="Lee Load",
        contact_email=f"reseller-{RUN}@load-partners.net",
        payout_currency="GBP",
    )
    for capability in ("customer.provision", "customer.manage", "subscription.sell", "commission.view"):
        partners.grant_capability(sctx, partner.id, capability=capability, reason="Phase 12 load-test reseller setup")
    partners.grant_product(sctx, partner.id, product_code="easy_crm")
    admin_user = person(f"reseller-{RUN}@load-partners.net", "Lee Load")
    partners.add_member(sctx, partner.id, email=admin_user.email, role="admin")
    partners.set_partner_status(sctx, partner.id, target=PartnerStatus.ACTIVE, reason="Phase 12 load-test reseller setup")
actor = partner_actor(user_id=admin_user.id, partner_id=partner.id, aal=2)
for i in range(PARTNER_CUSTOMERS):
    customers.provision_customer(
        actor,
        organization_name=f"Partner Customer {i} {RUN}",
        owner_email=f"pc{i:03d}-{RUN}@load-customers.net",
        idempotency_key=uuid.uuid4().hex,
    )
print("LOADPARTNER " + json.dumps({"email": admin_user.email, "partner_id": str(partner.id)}))
print("LOADSTAFF " + json.dumps({"email": admin.email}))
print("LOAD_SEED_DONE")
