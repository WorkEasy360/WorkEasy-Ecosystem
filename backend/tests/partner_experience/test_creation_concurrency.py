"""Racing creators through Add Partner (ADR-047, ADR-048).

Two people adding the same address at once get one partner and one account;
several adding the same partner name at once each get a partner with its own
code. Prices and commission are no longer part of creation: they are set in
Complete Your Profile through the platform's own commands, whose rules
(a partner price is a price-book row; a commission rule is never backdated) are
tested with those commands.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from django.db import connections

from tests.partner_experience.conftest import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    make_body,
)
from workeasy360.commercial.partners.models import PartnerAccount
from workeasy360.contracts.errors import Workeasy360Error
from workeasy360.platform.identity.models import User

# --- racing creators ---------------------------------------------------------------------------------------


def run_together(worker, count=6):
    results: list = []
    unexpected: list = []
    lock = threading.Lock()
    barrier = threading.Barrier(count)

    def wrapped(index: int) -> None:
        try:
            barrier.wait(timeout=30)
            outcome = worker(index)
            with lock:
                results.append(outcome)
        except Exception as exc:
            with lock:
                unexpected.append(exc)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=count) as pool:
        for future in as_completed([pool.submit(wrapped, i) for i in range(count)]):
            future.result()
    return results, unexpected


@pytest.fixture
def staff_ctx(staff_factory):
    _user, ctx = staff_factory(
        "platform.staff_partner.manage",
        "platform.staff_partner.view",
        "platform.staff_pricing.manage",
        "platform.staff_commission.manage",
    )
    return ctx


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
def test_six_creators_naming_one_address_make_one_partner_and_one_account(
    catalog, staff_ctx
):
    from workeasy360.commercial.controlplane import partner_onboarding

    def worker(i):
        try:
            partner_onboarding.create(
                staff_ctx, make_body("reseller", name=f"Race Mail {i}")
            )
            return "created"
        except Workeasy360Error:
            return "refused"

    results, unexpected = run_together(worker)
    assert unexpected == []
    assert results.count("created") == 1 and results.count("refused") == 5
    assert User.objects.filter(email=ADMIN_EMAIL).count() == 1
    assert PartnerAccount.objects.filter(code__startswith="race-mail-").count() == 1
    # The one account has the one password that was typed.
    assert User.objects.get(email=ADMIN_EMAIL).check_password(ADMIN_PASSWORD)


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
def test_six_creators_naming_one_partner_get_six_partners_with_distinct_codes(
    catalog, staff_ctx
):
    from workeasy360.commercial.controlplane import partner_onboarding

    def worker(i):
        try:
            partner_onboarding.create(
                staff_ctx,
                make_body(
                    "reseller", name="Race Code", email=f"admin{i}@race-code.example.com"
                ),
            )
            return "created"
        except Workeasy360Error:
            return "refused"

    results, unexpected = run_together(worker)
    assert unexpected == []
    # The code is derived from the name under a lock, so six people adding the
    # same name at once get six partners, each with its own code - never a
    # collision that leaves an account behind.
    assert results.count("created") == 6
    codes = set(
        PartnerAccount.objects.filter(code__startswith="race-code").values_list(
            "code", flat=True
        )
    )
    assert codes == {"race-code", *(f"race-code-{n}" for n in range(2, 7))}
    assert User.objects.filter(email__endswith="@race-code.example.com").count() == 6


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
def test_a_name_and_a_suffixed_name_racing_never_share_a_code(catalog, staff_ctx):
    """ "Acme" (whose base is taken, so it settles on ``acme-2``) and "Acme 2"
    (whose base is ``acme-2``) lock the same key for that code."""
    from workeasy360.commercial.controlplane import partner_onboarding

    partner_onboarding.create(
        staff_ctx, make_body("reseller", name="Acme", email="first@race-acme.example.com")
    )

    def worker(i):
        name = "Acme" if i % 2 == 0 else "Acme 2"
        try:
            partner_onboarding.create(
                staff_ctx,
                make_body("reseller", name=name, email=f"admin{i}@race-acme.example.com"),
            )
            return "created"
        except Workeasy360Error:
            return "refused"

    results, unexpected = run_together(worker, count=4)
    assert unexpected == []
    assert results.count("created") == 4
    codes = list(
        PartnerAccount.objects.filter(code__startswith="acme").values_list(
            "code", flat=True
        )
    )
    assert len(codes) == len(set(codes)) == 5
