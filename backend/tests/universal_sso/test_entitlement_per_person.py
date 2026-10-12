"""The entitlement API's optional per-person question (ADR-044 addendum).

A product session outlives the sign-in that opened it. The organization-level
answer covers a trial that ran out or a subscription that ended; a product that
also sends ``subject`` (the ID token's ``sub``) learns, within the same
revalidation window, that this person's account was disabled, their membership
removed or their seat unassigned - the personal half of sign-in's checks.

What must hold:
- without ``subject`` nothing changes;
- an organization-level denial wins and says nothing about the person;
- removed, suspended, disabled, unknown and malformed all read the same
  (``membership_inactive``), so the question cannot enumerate members;
- the failed-password brake is not a removal;
- a platform failure is a 503, never a denial;
- the per-(client, organization, person) bucket stops a loop on one key without
  touching the product's other questions.
"""

from __future__ import annotations

import time
import uuid

import jwt
import pytest

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("frozen_throttle_window")]

VERIFY = "/api/v1/integrations/entitlements/verify"
TOKEN = "/api/v1/integrations/oauth/token"
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


def _auth(registration) -> dict:
    now = int(time.time())
    return {
        "client_assertion_type": ASSERTION_TYPE,
        "client_assertion": jwt.encode(
            {
                "iss": registration["client_id"],
                "sub": registration["client_id"],
                "aud": "http://testserver" + TOKEN,
                "iat": now,
                "exp": now + 60,
                "jti": uuid.uuid4().hex,
            },
            registration["private_key_pem"],
            algorithm="RS256",
            headers={"kid": registration["credential_id"]},
        ),
    }


def _verify(client, registration, org, **fields):
    return client.post(
        VERIFY,
        {"organization_id": org["organization_id"], **fields, **_auth(registration)},
    )


def _set_membership(org, status):
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test changes a membership", module="tests.universal_sso"),
        organization_db_scope(org["org"].id),
    ):
        Membership.unscoped.filter(id=org["membership"].id).update(status=status)


def _set_user_status(user, status):
    from workeasy360.platform.identity.models import User

    User.objects.filter(id=user.id).update(status=status)


def test_without_a_subject_the_answer_is_the_organizations(
    client, signing_key, register_product, entitled_org
):
    registration = register_product("crm")
    org = entitled_org("crm")
    _set_membership(org, "removed")

    body = _verify(client, registration, org).json()

    assert body["decision"] == "allow"
    assert body["reason"] == "entitled"


def test_an_entitled_member_with_a_seat_is_allowed(
    client, signing_key, register_product, entitled_org
):
    registration = register_product("crm")
    org = entitled_org("crm")

    body = _verify(
        client, registration, org, subject=str(org["user"].id), write="true"
    ).json()

    assert body["decision"] == "allow"
    assert body["write_allowed"] is True
    assert set(body) == {
        "decision",
        "reason",
        "product_code",
        "organization_id",
        "write_allowed",
        "checked_at",
        "revalidate_after",
    }


@pytest.mark.parametrize("status", ["removed", "suspended"])
def test_a_member_no_longer_active_is_denied(
    client, signing_key, register_product, entitled_org, status
):
    registration = register_product("crm")
    org = entitled_org("crm")
    _set_membership(org, status)

    body = _verify(client, registration, org, subject=str(org["user"].id)).json()

    assert body == {**body, "decision": "deny", "reason": "membership_inactive"}
    assert body["write_allowed"] is False


@pytest.mark.parametrize("status", ["disabled", "locked"])
def test_an_account_an_administrator_stopped_is_denied(
    client, signing_key, register_product, entitled_org, status
):
    registration = register_product("crm")
    org = entitled_org("crm")
    _set_user_status(org["user"], status)

    body = _verify(client, registration, org, subject=str(org["user"].id)).json()

    assert (body["decision"], body["reason"]) == ("deny", "membership_inactive")


def test_the_failed_password_brake_is_not_a_removal(
    client, signing_key, register_product, entitled_org
):
    """Ten wrong passwords for someone's address must not end their product
    sessions: that brake is about the password, not the person."""
    from datetime import timedelta

    from django.utils import timezone

    from workeasy360.platform.identity.models import User

    registration = register_product("crm")
    org = entitled_org("crm")
    User.objects.filter(id=org["user"].id).update(
        locked_until=timezone.now() + timedelta(minutes=15)
    )
    assert User.objects.get(id=org["user"].id).is_locked_out

    body = _verify(client, registration, org, subject=str(org["user"].id)).json()

    assert body["decision"] == "allow"


@pytest.mark.parametrize("subject", ["not-a-uuid", "0" * 64, None])
def test_unknown_and_malformed_subjects_read_as_not_a_member(
    client, signing_key, register_product, entitled_org, subject
):
    registration = register_product("crm")
    org = entitled_org("crm")

    body = _verify(client, registration, org, subject=subject or str(uuid.uuid4())).json()

    assert (body["decision"], body["reason"]) == ("deny", "membership_inactive")


def test_a_person_from_another_organization_is_not_a_member_here(
    client, signing_key, register_product, entitled_org
):
    registration = register_product("crm")
    org = entitled_org("crm")
    other = entitled_org("crm")

    body = _verify(client, registration, org, subject=str(other["user"].id)).json()

    assert (body["decision"], body["reason"]) == ("deny", "membership_inactive")


def test_an_unseated_member_of_a_per_seat_product_is_denied(
    client, signing_key, register_product, entitled_org
):
    registration = register_product("crm")
    org = entitled_org("crm", seat=False)

    body = _verify(client, registration, org, subject=str(org["user"].id)).json()

    assert (body["decision"], body["reason"]) == ("deny", "no_seat")


def test_a_released_seat_is_denied_at_the_next_question(
    client, signing_key, register_product, entitled_org
):
    from workeasy360.platform.entitlements.services.seats import release_seat
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    registration = register_product("crm")
    org = entitled_org("crm")
    assert (
        _verify(client, registration, org, subject=str(org["user"].id)).json()["decision"]
        == "allow"
    )

    with tenant_context(org["ctx"]), tenant_db_scope(org["ctx"]):
        release_seat(org["org"].id, pool_key="crm", membership_id=org["membership"].id)

    body = _verify(client, registration, org, subject=str(org["user"].id)).json()
    assert (body["decision"], body["reason"]) == ("deny", "no_seat")


def test_an_organization_level_denial_wins_and_says_nothing_about_the_person(
    client, signing_key, register_product, entitled_org, organization_factory
):
    registration = register_product("crm")
    unentitled, _membership, user = organization_factory()

    body = _verify(
        client,
        registration,
        {"organization_id": str(unentitled.id)},
        subject=str(user.id),
    ).json()

    assert (body["decision"], body["reason"]) == ("deny", "not_entitled")


def test_a_platform_failure_while_asking_about_the_person_is_a_503(
    client, signing_key, register_product, entitled_org, monkeypatch
):
    from workeasy360.platform.ecosystem import authorization

    registration = register_product("crm")
    org = entitled_org("crm")

    def boom(**_kwargs):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(authorization, "person_access_reason", boom)

    response = _verify(client, registration, org, subject=str(org["user"].id))

    assert response.status_code == 503
    assert response.json()["error"] == "temporarily_unavailable"


def test_one_person_looping_is_cut_off_without_touching_the_others(
    client, signing_key, register_product, entitled_org, settings
):
    settings.ECOSYSTEM_RATE_LIMITS = {
        **settings.ECOSYSTEM_RATE_LIMITS,
        "verify": (100, 60),
        "verify_org": (2, 60),
    }
    registration = register_product("crm")
    org = entitled_org("crm")
    looping = str(org["user"].id)

    seen = [
        _verify(client, registration, org, subject=looping).status_code for _ in range(3)
    ]
    assert seen == [200, 200, 429]

    # Another person in the same organization, and the organization-level
    # question, each have their own count.
    assert (
        _verify(client, registration, org, subject=str(uuid.uuid4())).status_code == 200
    )
    assert _verify(client, registration, org).status_code == 200


def test_a_looping_key_does_not_spend_the_products_budget(
    client, signing_key, register_product, entitled_org, settings
):
    """The per-key bucket is asked first: what it refuses never reaches the
    product's own budget, which its other customers' questions need."""
    settings.ECOSYSTEM_RATE_LIMITS = {
        **settings.ECOSYSTEM_RATE_LIMITS,
        "verify": (4, 60),
        "verify_org": (2, 60),
    }
    registration = register_product("crm")
    org = entitled_org("crm")
    looping = str(org["user"].id)

    seen = [
        _verify(client, registration, org, subject=looping).status_code for _ in range(10)
    ]
    assert seen == [200, 200] + [429] * 8

    # Two of the product's four remain for everyone else.
    assert _verify(client, registration, org).status_code == 200
    assert (
        _verify(client, registration, org, subject=str(uuid.uuid4())).status_code == 200
    )


def test_spellings_of_one_subject_share_one_count(
    client, signing_key, register_product, entitled_org, settings
):
    settings.ECOSYSTEM_RATE_LIMITS = {
        **settings.ECOSYSTEM_RATE_LIMITS,
        "verify": (100, 60),
        "verify_org": (2, 60),
    }
    registration = register_product("crm")
    org = entitled_org("crm")
    canonical = str(org["user"].id)
    spellings = [canonical, canonical.upper(), "{" + canonical + "}"]

    seen = [_verify(client, registration, org, subject=s).status_code for s in spellings]

    assert seen == [200, 200, 429]


def test_malformed_subjects_share_one_count(
    client, signing_key, register_product, entitled_org, settings
):
    settings.ECOSYSTEM_RATE_LIMITS = {
        **settings.ECOSYSTEM_RATE_LIMITS,
        "verify": (100, 60),
        "verify_org": (2, 60),
    }
    registration = register_product("crm")
    org = entitled_org("crm")

    seen = [
        _verify(client, registration, org, subject=f"not-a-uuid-{n}").status_code
        for n in range(3)
    ]

    assert seen == [200, 200, 429]


def test_every_denied_person_costs_the_same_reads(
    client, signing_key, register_product, entitled_org, settings
):
    """Unknown, malformed, from another organization and removed all run the
    same queries, so timing says no more than the answer does."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    settings.ECOSYSTEM_RATE_LIMITS = {
        **settings.ECOSYSTEM_RATE_LIMITS,
        "verify": (1000, 60),
        "verify_org": (1000, 60),
    }
    registration = register_product("crm")
    org = entitled_org("crm")
    other = entitled_org("crm")
    removed = entitled_org("crm")
    _set_membership(removed, "removed")
    # Warm whatever is cached per process (discovery, the product directory).
    _verify(client, registration, org, subject=str(uuid.uuid4()))

    def reads(target, subject) -> int:
        with CaptureQueriesContext(connection) as captured:
            body = _verify(client, registration, target, subject=subject).json()
        assert (body["decision"], body["reason"]) == ("deny", "membership_inactive")
        return len(captured)

    counts = {
        "unknown": reads(org, str(uuid.uuid4())),
        "malformed": reads(org, "not-a-uuid"),
        "other organization": reads(org, str(other["user"].id)),
        "removed": reads(removed, str(removed["user"].id)),
    }

    assert len(set(counts.values())) == 1, counts


def test_the_default_budget_matches_the_capacity_model(settings):
    """docs/universal-sso/capacity.md: 1,200/min per product client, 6/min per
    (client, organization, person). Changing either means re-reading that
    model, not just this number."""
    from django.conf import settings as live

    assert live.ECOSYSTEM_RATE_LIMITS["verify"][1] == 60
    assert live.ECOSYSTEM_RATE_LIMITS["verify_org"][1] == 60
    import os

    if "W360_RL_VERIFY" not in os.environ:
        assert live.ECOSYSTEM_RATE_LIMITS["verify"][0] == 1200
    if "W360_RL_VERIFY_ORG" not in os.environ:
        assert live.ECOSYSTEM_RATE_LIMITS["verify_org"][0] == 6
