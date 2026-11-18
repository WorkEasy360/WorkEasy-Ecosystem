"""Back-channel logout (ADR-069) during the move to WorkEasy360 Accounts (ADR-067).

A logout token is minted by a worker, where no request chose an issuer. Without
care it would always name the canonical issuer, and during stages b and c a
product still configured with the other sign-in name would refuse it - and keep
its session after the person signed out everywhere.

The rule under test: **a logout token names the issuer the product is
configured with** (the one it last redeemed a code under), and **never a name
outside the platform's closed list**. Each token is verified the way a product
verifies one: signature by ``kid`` from the key set, ``iss`` equal to that
product's own configured issuer, ``aud`` its client id.
"""

from __future__ import annotations

from urllib.parse import parse_qs

import jwt
import pytest

from tests.accounts_portal.conftest import ACCOUNTS, AUTH, Browser, sign_in_to_product
from tests.factories.manifests import PRODUCT_ORIGIN, manifest_for

pytestmark = pytest.mark.django_db

BACKCHANNEL_URI = f"{PRODUCT_ORIGIN}/auth/backchannel-logout"


@pytest.fixture
def crm(signing_key, register_product, entitled_org):
    """A product that takes back-channel logout, and an entitled person."""
    registration = register_product(
        "crm",
        manifest=manifest_for(
            "crm",
            manifest_version="2026-11-15",
            sign_in_mode="sso_only",
            backchannel_logout_uri=BACKCHANNEL_URI,
        ),
    )
    org = entitled_org("crm")
    return {"registration": registration, "org": org, "user": org["user"]}


@pytest.fixture
def product_answers(monkeypatch):
    """The product's HTTP answer at the egress boundary; captures what was sent."""
    from workeasy360.integration.backchannel import delivery
    from workeasy360.integration.egress.client import EgressResponse

    sent: list = []

    def fake_send(request, **kwargs):
        sent.append(request)
        return EgressResponse(
            status=200, headers={}, body=b"", truncated=False, duration_s=0.01
        )

    monkeypatch.setattr(delivery, "send", fake_send)
    return sent


def _sign_in(product, crm, *, issuer: str) -> dict:
    browser = Browser()
    browser.sign_in(ACCOUNTS, crm["user"])
    rp = product(crm["registration"], issuer=issuer)
    return sign_in_to_product(rp, browser, crm["org"]["organization_id"])


def _sign_out_everywhere(crm) -> None:
    from django.db import transaction

    from workeasy360.contracts.ports.sign_ins import REASON_SIGNED_OUT_EVERYWHERE
    from workeasy360.platform.ecosystem import backchannel

    with transaction.atomic():
        queued = backchannel.record_sign_ins_ended(
            user_id=crm["user"].pk,
            sign_in_ids=(),
            everywhere=True,
            reason=REASON_SIGNED_OUT_EVERYWHERE,
        )
    assert queued == 1


def _delivered_token(sent) -> str:
    from workeasy360.integration.backchannel import delivery

    assert delivery.deliver_due()["delivered"] == 1
    (request,) = sent
    assert request.url == BACKCHANNEL_URI
    return parse_qs(request.body.decode())["logout_token"][0]


def _verify_as_product(token: str, *, client_id: str, configured_issuer: str) -> dict:
    """Exactly what a relying party does with a logout token: the key from the
    key set by ``kid``, ``iss`` equal to its own configured issuer, ``aud``."""
    from workeasy360.platform.ecosystem import keys

    header = jwt.get_unverified_header(token)
    assert header["typ"] == "logout+jwt"
    key = next(k for k in keys.jwks()["keys"] if k["kid"] == header["kid"])
    return jwt.decode(
        token,
        jwt.PyJWK(key).key,
        algorithms=[key["alg"]],
        audience=client_id,
        issuer=configured_issuer,
        options={"require": ["iss", "aud", "iat", "exp", "jti", "events"]},
    )


@pytest.mark.parametrize(
    ("stage_name", "configured"),
    [("b", AUTH), ("b", ACCOUNTS), ("c", AUTH), ("c", ACCOUNTS)],
)
def test_a_logout_token_names_the_issuer_the_product_is_configured_with(
    stage, crm, product, product_answers, stage_name, configured
):
    stage(stage_name)
    signed_in = _sign_in(product, crm, issuer=configured)
    assert signed_in["claims"]["iss"] == configured

    _sign_out_everywhere(crm)
    token = _delivered_token(product_answers)

    claims = _verify_as_product(
        token,
        client_id=crm["registration"]["client_id"],
        configured_issuer=configured,
    )
    assert claims["sub"] == str(crm["user"].pk)
    # And the product configured with the other name would refuse it: the
    # window never makes a token acceptable to both.
    other = ACCOUNTS if configured == AUTH else AUTH
    with pytest.raises(jwt.InvalidIssuerError):
        _verify_as_product(
            token,
            client_id=crm["registration"]["client_id"],
            configured_issuer=other,
        )


def test_a_product_that_moves_is_told_under_its_new_issuer(
    stage, crm, product, product_answers
):
    """Its product sessions from before the move are still ended: the product
    checks a logout token against what it is configured with *now*."""
    from workeasy360.platform.ecosystem.models import ProductIntegration

    stage("b")
    _sign_in(product, crm, issuer=AUTH)
    _sign_in(product, crm, issuer=ACCOUNTS)  # the product's release moved it
    integration = ProductIntegration.objects.get(product_code="crm")
    assert integration.last_token_issuer == ACCOUNTS

    _sign_out_everywhere(crm)
    claims = _verify_as_product(
        _delivered_token(product_answers),
        client_id=crm["registration"]["client_id"],
        configured_issuer=ACCOUNTS,
    )
    assert claims["iss"] == ACCOUNTS


def test_a_retired_issuer_is_never_named(stage, crm, product, product_answers):
    """At stage d the previous name is no longer an issuer: a product left on
    it already fails closed at discovery, and its logout token names the
    canonical issuer - never one the platform no longer is."""
    stage("c")
    _sign_in(product, crm, issuer=AUTH)
    stage("d")

    _sign_out_everywhere(crm)
    claims = _verify_as_product(
        _delivered_token(product_answers),
        client_id=crm["registration"]["client_id"],
        configured_issuer=ACCOUNTS,
    )
    assert claims["iss"] == ACCOUNTS


def test_the_recorded_issuer_is_written_only_when_it_changes(stage, crm, product):
    from workeasy360.platform.ecosystem.models import ProductIntegration

    stage("b")
    _sign_in(product, crm, issuer=ACCOUNTS)
    before = ProductIntegration.objects.get(product_code="crm")
    assert before.last_token_issuer == ACCOUNTS
    _sign_in(product, crm, issuer=ACCOUNTS)
    after = ProductIntegration.objects.get(product_code="crm")
    # A recorded issuer is not a registration change.
    assert after.updated_at == before.updated_at


@pytest.mark.parametrize(
    "recorded",
    ["", "https://evil.example", "https://accounts.workeasy360.com.evil.example", AUTH],
)
def test_logout_token_issuer_is_always_one_of_the_closed_list(stage, recorded):
    from workeasy360.platform.ecosystem import tokens

    stage("d")  # canonical accounts., nothing else configured
    assert tokens.logout_token_issuer(recorded) == ACCOUNTS


@pytest.mark.parametrize("stage_name", ["off", "e"])
def test_with_one_issuer_every_logout_token_names_it(stage, stage_name):
    from workeasy360.platform.ecosystem import tokens

    stage(stage_name)
    expected = AUTH if stage_name == "off" else ACCOUNTS
    assert tokens.logout_token_issuer(AUTH) == expected
    assert tokens.logout_token_issuer(ACCOUNTS) == expected
    assert tokens.logout_token_issuer("") == expected
