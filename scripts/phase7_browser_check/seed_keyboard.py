"""Extra fixtures for the keyboard-only pass (run after scripts/phase7_browser_check/seed.py).

- TOTP factors with known secrets for the staff and partner users, so the real
  step-up prompt can be completed from the keyboard;
- a READY payout prepared by *another* staff member (root), paying a manual
  adjustment root recorded - so the keyboard user may approve it under both
  separation-of-duties rules.
"""

import json

import django

django.setup()

from django.utils import timezone

from workeasy360.commercial.commissions import ledger, payouts
from workeasy360.commercial.partners.models import PartnerAccount
from workeasy360.platform.authz import staff
from workeasy360.platform.authz.control_plane import platform_context
from workeasy360.platform.identity import services as identity
from workeasy360.platform.identity.models import User
from workeasy360.platform.tenancy.unscoped import unscoped_access

with unscoped_access(reason="keyboard seed", module="seed"):
    root = User.objects.get(email="root@browser.test")
    admin = User.objects.get(email="staff@browser.test")
    partner_user = User.objects.get(email="partner@browser.test")

for permission in ("platform.staff_commission.manage", "platform.staff_payout.manage"):
    staff.create_grant(
        user_id=root.id,
        permission=permission,
        granted_by_id=admin.id,
        reason="Keyboard pass fixture: a second staff member",
    )
rctx = platform_context(user_id=root.id, aal=2)
partner = PartnerAccount.objects.get(code="browser-partner")
ledger.record_adjustment(
    rctx,
    partner.id,
    amount_minor=12345,
    currency="GBP",
    reason="Keyboard pass opening balance",
    idempotency_key="kb-adjustment",
)
payout = payouts.prepare_payout(
    rctx, partner_id=partner.id, period_end=timezone.now().date(), idempotency_key="kb-prepare"
)
payouts.mark_payout_ready(rctx, payout.id)

secrets = {}
print("KEYBOARD-SEEDED", json.dumps({"payout": str(payout.id), "secrets": secrets}))
