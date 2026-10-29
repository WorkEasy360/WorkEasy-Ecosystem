"""History made by the COMMITTED code (44e2364, ADR-053), before ADR-054.

Run by ``prepare.py`` with ``PYTHONPATH`` pointing at a ``git archive HEAD``
export. It replays the ecosystem history seed (partners, a general referral
code with captured visits, a reseller sale and its ledger row, a rule) and adds
what ADR-053 could make and ADR-054's data step must carry over: an
affiliate's **product** link with a captured visit. Scratch databases only.
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

from workeasy360.commercial.commissions import referrals  # noqa: E402
from workeasy360.commercial.commissions.models import ReferralCode  # noqa: E402
from workeasy360.commercial.partners.models import PartnerAccount  # noqa: E402

aff = PartnerAccount.objects.get(code="hist-affiliate")
general = ReferralCode.objects.get(partner=aff)
# An ADR-053 product link: a code naming a product, made before promotion grants.
product_link = ReferralCode.objects.create(
    partner=aff,
    code="HISTCRM1",
    label="CRM launch",
    product_code="easy_crm",
    created_by_id=general.created_by_id,
)
referrals.capture_referral(product_link.code)
print("HISTORY_PRODUCT_LINK", product_link.code)
