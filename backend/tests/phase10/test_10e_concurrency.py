"""Phase 10e concurrency: one link, several browsers, real commits.

A rollback-wrapped test gives every thread a different, invisible view of the
data and proves nothing, so each test here commits for real (as in 10b).
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from django.db import connections

from tests.phase10.conftest import PASSWORD, deliver_account_mail, post, token_from
from workeasy360.commercial.partners import customers
from workeasy360.commercial.partners.models import PartnerOwnerInvite

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.concurrency]

ACCEPT = "/api/v1/public/owner-invitations/accept"
WORKERS = 6


def _race(jobs):
    """Run every job at the same instant; return ``(results, errors)``."""
    results: list = []
    errors: list = []
    lock = threading.Lock()
    barrier = threading.Barrier(len(jobs))

    def wrapped(job):
        try:
            barrier.wait(timeout=30)
            outcome = job()
            with lock:
                results.append(outcome)
        except Exception as exc:
            with lock:
                errors.append(exc)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        for future in as_completed([pool.submit(wrapped, job) for job in jobs]):
            future.result()
    return results, errors


@pytest.fixture
def provisioned(channel):
    address = f"race-{uuid.uuid4().hex[:8]}@customer-example.com"
    result = customers.provision_customer(
        channel["actor"](),
        organization_name="Race Ltd",
        owner_email=address,
        idempotency_key=uuid.uuid4().hex,
    )
    sent = [m for m in deliver_account_mail() if address in m.to]
    assert len(sent) == 1
    return {"email": address, "org": result.organization_id, "token": token_from(sent[0])}


def _claim(client, token, index):
    return lambda: post(
        client,
        ACCEPT,
        {
            "token": token,
            "full_name": f"Browser {index}",
            "password": f"A distinct passphrase number {index} 8842!",
        },
    )


def test_six_browsers_presenting_one_link_make_exactly_one_owner(provisioned, anonymous):
    clients = [anonymous(ip=f"10.30.0.{i + 1}") for i in range(WORKERS)]
    results, errors = _race(
        [_claim(c, provisioned["token"], i) for i, c in enumerate(clients)]
    )
    assert not errors, errors
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200] + [404] * (WORKERS - 1), statuses
    assert {r.json()["code"] for r in results if r.status_code == 404} == {
        "invitation_invalid"
    }

    from workeasy360.platform.identity.models import User

    row = PartnerOwnerInvite.objects.get(organization_id=provisioned["org"])
    assert row.status == "accepted" and row.token_hash == ""
    user = User.objects.get(email=provisioned["email"])
    winners = [
        i
        for i in range(WORKERS)
        if user.check_password(f"A distinct passphrase number {i} 8842!")
    ]
    assert len(winners) == 1, (
        "one password, the winner's - never a blend or a later overwrite"
    )
    assert user.full_name == f"Browser {winners[0]}"

    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.organizations.resolver import is_active_owner
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    assert is_active_owner(user_id=user.id, organization_id=provisioned["org"])
    with (
        unscoped_access(reason="Test counts the owner memberships", module="tests"),
        organization_db_scope(provisioned["org"]),
    ):
        assert (
            Membership.unscoped.filter(
                organization_id=provisioned["org"], user_id=user.id
            ).count()
            == 1
        )


def test_a_claim_racing_a_withdrawal_ends_in_one_consistent_state(
    provisioned, anonymous, channel, http_login
):
    channel["user"].set_password(PASSWORD)
    channel["user"].save(update_fields=["password"])
    partner = http_login(channel["user"])
    url = (
        f"/api/v1/partner/{channel['partner'].id}/customers/{provisioned['org']}"
        "/owner-invitation/revoke"
    )
    claimant = anonymous(ip="10.31.0.1")
    results, errors = _race(
        [_claim(claimant, provisioned["token"], 1), lambda: post(partner, url)]
    )
    assert not errors, errors
    claim_result, revoke_result = sorted(
        results, key=lambda r: r.request["PATH_INFO"] != ACCEPT
    )
    row = PartnerOwnerInvite.objects.get(organization_id=provisioned["org"])
    if claim_result.status_code == 200:
        # The claim took the row first; a withdrawal after it is refused.
        assert row.status == "accepted" and revoke_result.status_code == 400
    else:
        assert claim_result.status_code == 404
        assert row.status == "revoked" and revoke_result.status_code == 200
    assert row.token_hash == ""


def test_two_resends_at_once_leave_exactly_one_working_link(
    provisioned, anonymous, channel, http_login
):
    channel["user"].set_password(PASSWORD)
    channel["user"].save(update_fields=["password"])
    url = (
        f"/api/v1/partner/{channel['partner'].id}/customers/{provisioned['org']}"
        "/owner-invitation/resend"
    )
    clients = [http_login(channel["user"]) for _ in range(2)]
    results, errors = _race([lambda c=c: post(c, url) for c in clients])
    assert not errors, errors
    assert [r.status_code for r in results] == [200, 200]
    row = PartnerOwnerInvite.objects.get(organization_id=provisioned["org"])
    assert row.send_count == 3, (
        "the provisioning send and both resends, each counted once"
    )

    tokens = {
        token_from(m) for m in deliver_account_mail() if provisioned["email"] in m.to
    }
    assert len(tokens) == 3
    previews = [
        post(
            anonymous(ip=f"10.32.0.{n + 1}"),
            "/api/v1/public/owner-invitations/preview",
            {"token": token},
        ).status_code
        for n, token in enumerate(sorted(tokens))
    ]
    assert sorted(previews) == [200, 404, 404], "only the last link sent still opens it"
