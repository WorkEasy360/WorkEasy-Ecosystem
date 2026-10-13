"""Sign in once across portal hosts (design section 8), and what the SSO
lifecycle logs - and never logs (design section 9)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from tests.universal_sso.conftest import (
    AUTHORIZE,
    CHOOSE,
    authorize_query,
    query_of,
    resume_token_of,
)

pytestmark = pytest.mark.django_db


def _session(user, sign_in_id):
    from workeasy360.platform.identity.sessions import SessionStore

    store = SessionStore()
    store["_auth_user_id"] = str(user.id)
    store["w360_sign_in_id"] = sign_in_id
    store.create()
    return store


def test_one_sessions_slide_keeps_the_rest_of_its_sign_in_alive(settings, user_factory):
    """Working on one portal host keeps the identity provider's session on
    the sign-in host from lapsing - so a product launch needs no password."""
    from workeasy360.platform.identity.models import UserSession
    from workeasy360.platform.identity.sessions import SessionStore

    settings.PORTALS_ENABLED = True
    user = user_factory()
    portal = _session(user, "a" * 32)
    central = _session(user, "a" * 32)
    other_sign_in = _session(user, "b" * 32)
    soon = timezone.now() + timedelta(minutes=1)
    UserSession.objects.filter(
        session_key__in=[central.session_key, other_sign_in.session_key]
    ).update(expire_date=soon)
    expired = _session(user, "a" * 32)
    UserSession.objects.filter(session_key=expired.session_key).update(
        expire_date=timezone.now() - timedelta(seconds=1)
    )

    working = SessionStore(session_key=portal.session_key)
    working["touched"] = True
    working.save()

    rows = {r.session_key: r.expire_date for r in UserSession.objects.filter(user=user)}
    assert rows[central.session_key] == rows[portal.session_key]  # slid with it
    assert rows[other_sign_in.session_key] == soon  # another sign-in: untouched
    assert rows[expired.session_key] < timezone.now()  # never revived


def test_single_host_mode_spends_no_query_on_it(settings, user_factory):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from workeasy360.platform.identity.sessions import SessionStore

    settings.PORTALS_ENABLED = False
    user = user_factory()
    store = _session(user, "c" * 32)
    loaded = SessionStore(session_key=store.session_key)
    loaded.load()
    loaded["touched"] = True
    with CaptureQueriesContext(connection) as captured:
        loaded.save()
    # The session's own UPDATE sets sign_in_id; a sibling slide would filter on it.
    sibling_updates = [
        q["sql"]
        for q in captured.captured_queries
        if q["sql"].lstrip().upper().startswith("UPDATE")
        and '"sign_in_id"' in q["sql"].split(" WHERE ", 1)[-1]
    ]
    assert sibling_updates == []


def test_the_sso_lifecycle_is_observable_and_leaks_nothing(
    client, signing_key, sso_product, entitled_org, signed_in, product_client, caplog
):
    crm = sso_product("crm", mode="sso_optional")
    org = entitled_org("crm")
    signed_in(org["user"], sign_in_preference="ask")
    with caplog.at_level("DEBUG"):
        verifier, query = authorize_query(
            crm,
            org["organization_id"],
            state="state-SECRET-123",
            nonce="nonce-SECRET-456",
        )
        token = resume_token_of(client.get(AUTHORIZE, query))
        client.post(
            CHOOSE,
            {"resume": token, "choice": "workeasy360"},
            content_type="application/json",
        )
        granted = client.get(f"/api/v1/integrations/oauth/resume?resume={token}")
        code = query_of(granted)["code"][0]
        tokens = product_client(crm).exchange_code(code=code, code_verifier=verifier)
        # A failure, for its category.
        _v, bad = authorize_query(crm, "00000000-0000-0000-0000-000000000000")
        client.get(AUTHORIZE, bad)
    text = caplog.text
    for event in (
        "ecosystem.sso.started",
        "ecosystem.sso.chooser_shown",
        "ecosystem.sso.choice",
        "ecosystem.sso.succeeded",
        "ecosystem.sso.failed",
    ):
        assert event in text, event
    assert "membership_inactive" in text  # the failure category
    assert "crm" in text and org["organization_id"] in text
    for secret in (
        code,
        token,
        verifier,
        "state-SECRET-123",
        "nonce-SECRET-456",
        tokens["id_token"],
        tokens["access_token"],
        crm["private_key_pem"].splitlines()[1],
    ):
        assert secret not in text
