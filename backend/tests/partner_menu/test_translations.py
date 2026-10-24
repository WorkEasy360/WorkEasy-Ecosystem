"""The Translation Manager (ADR-046): partner-owned presentation strings only.

What is pinned: a closed list of keys and locales, plain text that can never be
markup or template code, a fixed fall-back chain, nothing that touches security
wording, and no way to read or write another partner's words.
"""

from __future__ import annotations

import json

import pytest
from django.core.cache import cache

from tests.partner_menu.conftest import api, partner_audit, send
from workeasy360.commercial.partners import translations

pytestmark = pytest.mark.django_db

URL = "translations"


@pytest.fixture(autouse=True)
def _fresh_throttle():
    cache.clear()


def put(client, pid, locale, strings):
    return send(client, "put", api(pid, URL), {"locale": locale, "strings": strings})


def stored(pid):
    from workeasy360.commercial.partners.models import PartnerTranslation

    return {
        (t.locale, t.key): t.value
        for t in PartnerTranslation.objects.filter(partner_id=pid)
    }


# --- the registry ------------------------------------------------------------------------


def test_only_customer_facing_labels_and_messages_can_be_translated():
    banned = (
        "security",
        "error",
        "legal",
        "auth",
        "permission",
        "validation",
        "password",
        "login",
        "mfa",
        "warning",
        "terms",
        "privacy",
    )
    for key, spec in translations.KEYS.items():
        assert key == spec.key
        assert not any(word in key.lower() for word in banned), key
        assert key.split(".")[0] in {"portal", "email"}, key
    assert translations.DEFAULT_LOCALE in translations.LOCALES
    # The platform layer always covers the English fall-back for every key.
    assert set(translations.PLATFORM["en"]) == set(translations.KEYS)


def test_the_overview_lists_every_key_with_where_it_comes_from(seller, owner_client):
    pid = seller["partner"].id
    body = owner_client.get(api(pid, URL)).json()
    assert body["locale"] == "en"
    assert {s["key"] for s in body["strings"]} == set(translations.KEYS)
    assert {s["source"] for s in body["strings"]} == {"english"}
    assert body["can_manage"] is True
    assert body["fallback"][-1] == "English"
    assert {c["code"] for c in body["locales"]} == set(translations.LOCALES)


# --- the fall-back chain ----------------------------------------------------------------------


def test_the_chain_is_partner_then_platform_then_english(seller, owner_client):
    pid = seller["partner"].id
    key = "portal.welcome_title"
    assert (
        translations.resolve(pid, "fr", key) == "Welcome"
    )  # no French anywhere: English
    assert put(owner_client, pid, "fr", {key: "Bienvenue"}).status_code == 200
    assert translations.resolve(pid, "fr", key) == "Bienvenue"  # the partner's own
    assert (
        translations.resolve(pid, "en", key) == "Welcome"
    )  # another language is unaffected
    assert put(owner_client, pid, "fr", {key: ""}).status_code == 200  # withdrawn
    assert translations.resolve(pid, "fr", key) == "Welcome"
    assert stored(pid) == {}
    # A locale nobody supports reads English rather than failing.
    assert translations.resolve(pid, "xx", key) == "Welcome"


def test_a_platform_translation_beats_english_and_loses_to_the_partner(
    seller, monkeypatch
):
    pid = seller["partner"].id
    key = "portal.support.heading"
    monkeypatch.setitem(translations.PLATFORM, "de", {key: "Brauchen Sie Hilfe?"})
    assert translations.resolve(pid, "de", key) == "Brauchen Sie Hilfe?"
    assert translations.resolve(pid, "fr", key) == "Need help?"


def test_what_the_partner_worded_is_published_by_locale(seller, owner_client):
    pid = seller["partner"].id
    put(owner_client, pid, "hi", {"portal.nav.dashboard": "डैशबोर्ड"})
    put(owner_client, pid, "fr", {"portal.nav.dashboard": "Tableau de bord"})
    assert translations.public_map(pid) == {
        "hi": {"portal.nav.dashboard": "डैशबोर्ड"},
        "fr": {"portal.nav.dashboard": "Tableau de bord"},
    }


# --- what a value may be --------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "Hello >",
        "{{ user.password }}",
        "{% load x %}",
        "${jndi:ldap://x}",
        "Hello %s",
        "back\\slash",
        "`rm -rf`",
        "line one\nline two",
        "x" * 500,
    ],
)
def test_a_value_is_one_line_of_plain_words_and_never_code(seller, owner_client, value):
    pid = seller["partner"].id
    response = put(owner_client, pid, "en", {"portal.welcome_title": value})
    assert response.status_code == 400, value
    assert stored(pid) == {}


@pytest.mark.parametrize(
    "body",
    [
        {"locale": "klingon", "strings": {"portal.welcome_title": "x"}},
        {"locale": 5, "strings": {"portal.welcome_title": "x"}},
        {"locale": "en", "strings": {"security.mfa_prompt": "x"}},
        {"locale": "en", "strings": {"portal.welcome_title": 5}},
        {"locale": "en", "strings": {}},
        {"locale": "en", "strings": ["portal.welcome_title"]},
        {"locale": "en"},
    ],
)
def test_keys_and_locales_come_from_closed_lists(seller, owner_client, body):
    response = send(owner_client, "put", api(seller["partner"].id, URL), body)
    assert response.status_code == 400
    assert stored(seller["partner"].id) == {}


def test_a_bad_value_changes_nothing_at_all(seller, owner_client):
    pid = seller["partner"].id
    response = put(
        owner_client,
        pid,
        "en",
        {"portal.welcome_title": "Fine", "portal.footer": "<b>no</b>"},
    )
    assert response.status_code == 400
    assert stored(pid) == {}


def test_html_entities_are_stored_as_text_and_never_decoded(seller, owner_client):
    pid = seller["partner"].id
    assert (
        put(owner_client, pid, "en", {"portal.welcome_title": "&lt;b&gt; Hi"}).status_code
        == 200
    )
    assert stored(pid)[("en", "portal.welcome_title")] == "&lt;b&gt; Hi"


# --- who may, and whose words -----------------------------------------------------------------


@pytest.mark.parametrize("role", ["sales", "finance", "support", "viewer"])
def test_a_role_without_the_brand_permission_can_read_but_not_write(
    seller, member_client, role
):
    client, _user = member_client(seller, role)
    pid = seller["partner"].id
    assert put(client, pid, "en", {"portal.welcome_title": "Mine"}).status_code == 403
    assert stored(pid) == {}


def test_marketing_may_word_the_brand(seller, member_client):
    client, _user = member_client(seller, "marketing")
    assert (
        put(
            client, seller["partner"].id, "en", {"portal.welcome_title": "Hello"}
        ).status_code
        == 200
    )


def test_a_partner_never_reads_or_writes_another_partners_words(
    seller, owner_client, partner_factory
):
    other = partner_factory("branding.manage")
    pid = seller["partner"].id
    put(owner_client, pid, "en", {"portal.welcome_title": "Acme welcome"})
    assert translations.public_map(other["partner"].id) == {}
    assert (
        translations.resolve(other["partner"].id, "en", "portal.welcome_title")
        == "Welcome"
    )
    # The path carries the partner; a member of one cannot name the other.
    assert owner_client.get(api(other["partner"].id, URL)).status_code == 404
    assert (
        put(
            owner_client,
            other["partner"].id,
            "en",
            {"portal.welcome_title": "Taken over"},
        ).status_code
        == 404
    )
    assert stored(other["partner"].id) == {}


def test_a_suspended_partner_cannot_change_its_words(seller, owner_client):
    from workeasy360.commercial.partners import services
    from workeasy360.contracts.enums import PartnerStatus

    pid = seller["partner"].id
    services.set_partner_status(
        seller["staff_ctx"],
        pid,
        target=PartnerStatus.SUSPENDED,
        reason="Suspended for the test",
    )
    assert put(owner_client, pid, "en", {"portal.welcome_title": "x"}).status_code in (
        403,
        404,
    )


def test_the_audit_names_the_keys_and_never_the_words(seller, owner_client):
    pid = seller["partner"].id
    put(owner_client, pid, "fr", {"portal.welcome_title": "Un mot secret"})
    trail = json.dumps(partner_audit(pid, "action", "changes"), default=str)
    assert "partner.translations_changed" in trail
    assert "portal.welcome_title" in trail
    assert "Un mot secret" not in trail


def test_changes_are_rate_limited(seller, owner_client, settings):
    settings.CONTROL_PLANE_RATE_LIMITS = {
        **settings.CONTROL_PLANE_RATE_LIMITS,
        "partner_translation_user": (2, 60),
    }
    pid = seller["partner"].id
    codes = [
        put(owner_client, pid, "en", {"portal.welcome_title": f"v{n}"}).status_code
        for n in range(4)
    ]
    assert codes[:2] == [200, 200] and codes[-1] == 429


def test_a_hostile_stored_value_is_returned_as_json_text_only(seller, owner_client):
    """Even if a hostile string were in the table (a bad import, a direct write),
    the API returns it as data with a JSON content type - never as a page."""
    from django.utils import timezone

    from workeasy360.commercial.partners.models import PartnerTranslation

    pid = seller["partner"].id
    PartnerTranslation.objects.create(
        partner_id=pid,
        locale="en",
        key="portal.footer",
        value="<script>alert(1)</script>",
        updated_by=seller["user"],
        updated_at=timezone.now(),
    )
    response = owner_client.get(api(pid, URL))
    assert response["Content-Type"].startswith("application/json")
    assert response.json()["strings"]
