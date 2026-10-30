"""History made by the COMMITTED code (ADR-054), before ADR-055.

Run by ``prepare.py`` with ``PYTHONPATH`` pointing at a ``git archive HEAD``
export. It replays the ecosystem history seed and adds what ADR-054 makes and
ADR-055 must leave alone: an affiliate's promotion grant for Easy CRM, a
product link under it with a captured visit, and a second grant that was
revoked. Scratch databases only.
"""

import runpy
from pathlib import Path

runpy.run_path(
    str(
        Path(__file__).resolve().parents[1]
        / "ecosystem_browser_check"
        / "seed_history.py"
    ),
    run_name="__main__",
)

from django.utils import timezone  # noqa: E402

from workeasy360.commercial.catalog.models import Product  # noqa: E402
from workeasy360.commercial.commissions import referrals  # noqa: E402
from workeasy360.commercial.commissions.models import ReferralCode  # noqa: E402
from workeasy360.commercial.partners.models import (  # noqa: E402
    PartnerAccount,
    PartnerPromotionGrant,
)

aff = PartnerAccount.objects.get(code="hist-affiliate")
general = ReferralCode.objects.get(partner=aff)
now = timezone.now()
PartnerPromotionGrant.objects.create(
    partner=aff,
    product=Product.objects.get(code="easy_crm"),
    granted_by_id=general.created_by_id,
    granted_at=now,
)
PartnerPromotionGrant.objects.create(
    partner=aff,
    product=Product.objects.get(code="easybook"),
    granted_by_id=general.created_by_id,
    granted_at=now,
    status="revoked",
    revoked_at=now,
    revoked_by_id=general.created_by_id,
)
link = ReferralCode.objects.create(
    partner=aff,
    code="HISTCRM2",
    label="CRM launch",
    product_code="easy_crm",
    link_kind="product",
    created_by_id=general.created_by_id,
)
referrals.capture_referral(link.code)
print("HISTORY_PROMOTION", link.code)
