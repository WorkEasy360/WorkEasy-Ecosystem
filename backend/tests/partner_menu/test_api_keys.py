"""The API Key Manager and the key-authenticated partner API (ADR-046).

A key is a scoped, expiring, revocable, hash-only credential for a partner's own
systems. These tests pin what makes it safe: the secret is shown once and never
stored, a key can never do more than the person who made it, every refusal is the
same refusal, and a key reaches nothing but its own partner's read-only slice.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from tests.partner_menu.conftest import api, partner_audit, send

pytestmark = pytest.mark.django_db

PARTNER_API = "/api/v1/partner-api"


def bearer(secret: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {secret}"}


def make_key(client, pid, *, scopes=("customers.read",), name="Reporting", days=30):
    response = send(
        client,
        "post",
        api(pid, "api-keys"),
        {"name": name, "scopes": list(scopes), "expires_in_days": days},
    )
    assert response.status_code == 201, response.content
    return response.json()


@pytest.fixture
def anon(anonymous):
    return anonymous()


@pytest.fixture(autouse=True)
def _fresh_throttle():
    cache.clear()
    yield
    cache.clear()


# --- creating, listing and the secret -------------------------------------------------


def test_the_secret_is_shown_once_and_only_a_hash_is_stored(seller, owner_client):
    from workeasy360.commercial.partners.models import PartnerApiKey

    pid = seller["partner"].id
    created = make_key(owner_client, pid)
    secret = created["secret"]
    assert secret.startswith("w360pk_")
    assert set(created["key"]) >= {
        "id",
        "name",
        "key_id",
        "scopes",
        "status",
        "expires_at",
    }
    row = PartnerApiKey.objects.get(id=created["key"]["id"])
    assert secret not in (row.secret_hash, row.key_id, row.name)
    assert len(row.secret_hash) == 64 and row.secret_hash != secret
    # Nothing returns it again: not the list, not a second read.
    for _ in range(2):
        listing = owner_client.get(api(pid, "api-keys"))
        assert secret not in listing.content.decode()
        assert "secret_hash" not in listing.content.decode()
    assert listing.json()["keys"][0]["key_id"] == f"w360pk_{row.key_id}"
    # The response that carries a secret is never cached.
    first = send(
        owner_client,
        "post",
        api(pid, "api-keys"),
        {"name": "Two", "scopes": ["account.read"]},
    )
    assert "no-store" in first["Cache-Control"]


def test_the_secret_never_reaches_the_audit_trail_or_a_log(seller, owner_client, caplog):
    pid = seller["partner"].id
    with caplog.at_level("DEBUG"):
        created = make_key(owner_client, pid)
        send(
            owner_client, "post", api(pid, f"api-keys/{created['key']['id']}/rotate"), {}
        )
    secret = created["secret"]
    body = secret.split("_", 2)[2]
    trail = json.dumps(partner_audit(pid, "action", "changes"), default=str)
    assert "partner.api_key_created" in trail and "partner.api_key_rotated" in trail
    assert secret not in trail and body not in trail
    assert body not in caplog.text and secret not in caplog.text


def test_creating_needs_the_second_factor(seller, owner_client, http_login):
    from workeasy360.commercial.partners.models import PartnerApiKey

    low = http_login(seller["user"], aal2=False)
    response = send(
        low,
        "post",
        api(seller["partner"].id, "api-keys"),
        {"name": "x", "scopes": ["account.read"]},
    )
    assert response.status_code in (401, 403)
    assert not PartnerApiKey.objects.filter(partner_id=seller["partner"].id).exists()


def test_rotating_and_revoking_need_the_second_factor(seller, owner_client, http_login):
    pid = seller["partner"].id
    key = make_key(owner_client, pid)["key"]
    low = http_login(seller["user"], aal2=False)
    for action in ("rotate", "revoke"):
        assert send(
            low, "post", api(pid, f"api-keys/{key['id']}/{action}"), {}
        ).status_code in (401, 403)
    assert owner_client.get(api(pid, "api-keys")).json()["keys"][0]["status"] == "active"


@pytest.mark.parametrize("role", ["sales", "finance", "support", "marketing", "viewer"])
def test_only_the_roles_that_run_the_account_see_or_manage_keys(
    seller, member_client, role
):
    pid = seller["partner"].id
    client, _user = member_client(seller, role)
    assert client.get(api(pid, "api-keys")).status_code == 403
    assert (
        send(
            client,
            "post",
            api(pid, "api-keys"),
            {"name": "x", "scopes": ["account.read"]},
        ).status_code
        == 403
    )


def test_an_admin_but_not_a_viewer_can_manage_keys(seller, member_client):
    client, _user = member_client(seller, "admin")
    assert client.get(api(seller["partner"].id, "api-keys")).status_code == 200


def test_a_key_cannot_carry_a_scope_the_creator_does_not_hold(
    partner_factory, http_login
):
    from workeasy360.commercial.partners.models import PartnerMember

    fx = partner_factory("branding.manage")  # no customer, no commission capability
    fx["user"].set_password("correct horse battery staple 42")
    fx["user"].save(update_fields=["password"])
    PartnerMember.objects.filter(partner_id=fx["partner"].id, user=fx["user"]).update(
        role="owner"
    )
    client = http_login(fx["user"])
    pid = fx["partner"].id
    for scope in ("customers.read", "commissions.read"):
        refused = send(
            client, "post", api(pid, "api-keys"), {"name": "x", "scopes": [scope]}
        )
        assert refused.status_code == 400, scope
    ok = send(
        client, "post", api(pid, "api-keys"), {"name": "x", "scopes": ["account.read"]}
    )
    assert ok.status_code == 201
    # The form only offers what the person may grant.
    offered = {s["key"] for s in client.get(api(pid, "api-keys")).json()["scopes"]}
    assert offered == {"account.read"}


@pytest.mark.parametrize(
    "body",
    [
        {"name": "", "scopes": ["account.read"]},
        {"name": "x", "scopes": []},
        {"name": "x", "scopes": ["admin.write"]},
        {"name": "x", "scopes": "account.read"},
        {"name": "x", "scopes": ["account.read"], "expires_in_days": 9999},
        {"name": "x", "scopes": ["account.read"], "expires_in_days": 0},
        {"name": "x", "scopes": ["account.read"], "expires_in_days": True},
        {"name": "<b>x</b>", "scopes": ["account.read"]},
        {"name": "x" * 81, "scopes": ["account.read"]},
    ],
)
def test_a_key_is_always_made_from_a_closed_list_and_always_expires(
    seller, owner_client, body
):
    assert (
        send(
            owner_client, "post", api(seller["partner"].id, "api-keys"), body
        ).status_code
        == 400
    )


def test_a_partner_holds_at_most_a_bounded_number_of_live_keys(
    seller, owner_client, settings
):
    from workeasy360.commercial.partners import api_keys

    settings.CONTROL_PLANE_RATE_LIMITS = {
        **settings.CONTROL_PLANE_RATE_LIMITS,
        "partner_apikey_user": (1000, 60),
        "partner_apikey_ip": (1000, 60),
    }
    pid = seller["partner"].id
    for n in range(api_keys.MAX_ACTIVE_KEYS):
        make_key(owner_client, pid, name=f"k{n}")
    assert (
        send(
            owner_client,
            "post",
            api(pid, "api-keys"),
            {"name": "one more", "scopes": ["account.read"]},
        ).status_code
        == 400
    )


# --- rotating and revoking ---------------------------------------------------------------


def test_rotating_replaces_the_secret_at_once(seller, owner_client, anon):
    pid = seller["partner"].id
    created = make_key(owner_client, pid)
    assert (
        anon.get(f"{PARTNER_API}/customers", **bearer(created["secret"])).status_code
        == 200
    )
    rotated = send(
        owner_client, "post", api(pid, f"api-keys/{created['key']['id']}/rotate"), {}
    )
    assert rotated.status_code == 200
    new_secret = rotated.json()["secret"]
    assert new_secret != created["secret"]
    assert (
        anon.get(f"{PARTNER_API}/customers", **bearer(created["secret"])).status_code
        == 401
    )
    assert anon.get(f"{PARTNER_API}/customers", **bearer(new_secret)).status_code == 200
    states = [k["status"] for k in owner_client.get(api(pid, "api-keys")).json()["keys"]]
    assert sorted(states) == ["active", "revoked"]
    # The old key cannot be rotated twice.
    assert (
        send(
            owner_client, "post", api(pid, f"api-keys/{created['key']['id']}/rotate"), {}
        ).status_code
        == 404
    )


def test_revoking_stops_a_key_at_once_and_only_once(seller, owner_client, anon):
    pid = seller["partner"].id
    created = make_key(owner_client, pid)
    assert (
        send(
            owner_client, "post", api(pid, f"api-keys/{created['key']['id']}/revoke"), {}
        ).status_code
        == 200
    )
    assert (
        anon.get(f"{PARTNER_API}/customers", **bearer(created["secret"])).status_code
        == 401
    )
    assert (
        send(
            owner_client, "post", api(pid, f"api-keys/{created['key']['id']}/revoke"), {}
        ).status_code
        == 404
    )


def test_another_partners_key_is_a_404(seller, owner_client, partner_factory, http_login):
    from workeasy360.commercial.partners.models import PartnerMember

    other = partner_factory("customer.provision")
    other["user"].set_password("correct horse battery staple 42")
    other["user"].save(update_fields=["password"])
    PartnerMember.objects.filter(
        partner_id=other["partner"].id, user=other["user"]
    ).update(role="owner")
    theirs = make_key(
        http_login(other["user"]), other["partner"].id, scopes=("account.read",)
    )
    pid = seller["partner"].id
    for action in ("rotate", "revoke"):
        assert (
            send(
                owner_client,
                "post",
                api(pid, f"api-keys/{theirs['key']['id']}/{action}"),
                {},
            ).status_code
            == 404
        )
    # ...and it is not in this partner's list.
    assert (
        theirs["key"]["id"] not in owner_client.get(api(pid, "api-keys")).content.decode()
    )


# --- the key-authenticated API ----------------------------------------------------------------


def test_a_key_reads_its_own_partner_and_nothing_else(seller, owner_client, anon):
    pid = seller["partner"].id
    created = make_key(
        owner_client, pid, scopes=("account.read", "customers.read", "commissions.read")
    )
    token = bearer(created["secret"])
    account = anon.get(f"{PARTNER_API}/account", **token)
    assert account.status_code == 200
    assert account.json()["name"] == seller["partner"].display_name
    assert "no-store" in account["Cache-Control"]
    for path in ("customers", "commissions"):
        page = anon.get(f"{PARTNER_API}/{path}", **token)
        assert page.status_code == 200
        assert set(page.json()) == {"results", "next_cursor", "has_more"}
    # No write verb exists on any of them.
    for path in ("account", "customers", "commissions"):
        assert (
            anon.post(
                f"{PARTNER_API}/{path}", "{}", content_type="application/json", **token
            ).status_code
            == 405
        )
        assert anon.delete(f"{PARTNER_API}/{path}", **token).status_code == 405


def test_a_key_is_limited_to_its_scopes(seller, owner_client, anon):
    created = make_key(owner_client, seller["partner"].id, scopes=("account.read",))
    token = bearer(created["secret"])
    assert anon.get(f"{PARTNER_API}/account", **token).status_code == 200
    denied = anon.get(f"{PARTNER_API}/customers", **token)
    assert denied.status_code == 403
    assert denied.json()["code"] == "insufficient_scope"
    assert anon.get(f"{PARTNER_API}/commissions", **token).status_code == 403


def test_every_bad_credential_is_the_same_answer(seller, owner_client, anon):
    pid = seller["partner"].id
    created = make_key(owner_client, pid)
    secret = created["secret"]
    head, _, tail = secret.rpartition("_")
    bad = [
        {},
        bearer(""),
        bearer("not-a-key"),
        bearer("w360pk_"),
        bearer(secret + "x"),
        bearer(head + "_" + "A" * len(tail)),
        bearer("w360pk_" + "0" * 12 + "_" + tail),
        {"HTTP_AUTHORIZATION": f"Basic {secret}"},
        {"HTTP_AUTHORIZATION": secret},
    ]
    answers = {
        (r.status_code, r.content)
        for r in (anon.get(f"{PARTNER_API}/customers", **extra) for extra in bad)
    }
    assert len(answers) == 1
    ((status, content),) = answers
    assert status == 401 and json.loads(content)["code"] == "invalid_credentials"


def test_a_key_dies_with_its_expiry_its_partner_and_its_creator(
    seller, owner_client, anon, member_client
):
    from workeasy360.commercial.partners import services
    from workeasy360.commercial.partners.models import PartnerApiKey, PartnerMember
    from workeasy360.contracts.enums import PartnerStatus

    pid = seller["partner"].id
    token_of = lambda created: bearer(created["secret"])  # noqa: E731

    expiring = make_key(owner_client, pid, name="expiring")
    PartnerApiKey.objects.filter(id=expiring["key"]["id"]).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert anon.get(f"{PARTNER_API}/account", **token_of(expiring)).status_code == 401

    # A paused creator: the key stops with the person.
    admin_client, admin_user = member_client(seller, "admin")
    by_admin = make_key(admin_client, pid, scopes=("account.read",), name="by admin")
    assert anon.get(f"{PARTNER_API}/account", **token_of(by_admin)).status_code == 200
    admin_member = PartnerMember.objects.get(partner_id=pid, user=admin_user)
    services.suspend_member(seller["staff_ctx"], pid, member_id=admin_member.id)
    assert anon.get(f"{PARTNER_API}/account", **token_of(by_admin)).status_code == 401
    services.resume_member(seller["staff_ctx"], pid, member_id=admin_member.id)
    assert anon.get(f"{PARTNER_API}/account", **token_of(by_admin)).status_code == 200
    services.remove_member(seller["staff_ctx"], pid, member_id=admin_member.id)
    assert anon.get(f"{PARTNER_API}/account", **token_of(by_admin)).status_code == 401

    # A suspended partner reads nothing.
    live = make_key(owner_client, pid, scopes=("account.read",), name="live")
    assert anon.get(f"{PARTNER_API}/account", **token_of(live)).status_code == 200
    services.set_partner_status(
        seller["staff_ctx"],
        pid,
        target=PartnerStatus.SUSPENDED,
        reason="Suspended for the test",
    )
    assert anon.get(f"{PARTNER_API}/account", **token_of(live)).status_code == 401


def test_a_scope_narrows_when_its_creator_is_demoted(seller, owner_client, anon):
    from workeasy360.commercial.partners.models import PartnerMember

    pid = seller["partner"].id
    created = make_key(owner_client, pid, scopes=("customers.read",))
    assert (
        anon.get(f"{PARTNER_API}/customers", **bearer(created["secret"])).status_code
        == 200
    )
    # Finance cannot see customers: the key can no longer do what its maker cannot.
    PartnerMember.objects.filter(partner_id=pid, user=seller["user"]).update(
        role="finance"
    )
    assert (
        anon.get(f"{PARTNER_API}/customers", **bearer(created["secret"])).status_code
        == 401
    )


def test_a_session_is_not_a_key_and_a_key_is_not_a_session(seller, owner_client, anon):
    pid = seller["partner"].id
    created = make_key(owner_client, pid)
    # A signed-in browser gets nothing from the key API without a key...
    assert owner_client.get(f"{PARTNER_API}/customers").status_code == 401
    # ...and a key opens none of the portal's own endpoints.
    for path in (
        "dashboard",
        "members",
        "profile",
        "api-keys",
        "notifications",
        "customers",
    ):
        assert anon.get(api(pid, path), **bearer(created["secret"])).status_code in (
            401,
            403,
        ), path


def test_a_key_of_one_partner_never_sees_another_partners_rows(
    seller, owner_client, anon, partner_factory
):
    pid = seller["partner"].id
    created = make_key(owner_client, pid, scopes=("account.read",))
    other = partner_factory("customer.provision")
    body = anon.get(f"{PARTNER_API}/account", **bearer(created["secret"])).json()
    assert body["name"] == seller["partner"].display_name
    assert str(other["partner"].id) not in json.dumps(body)
    # There is no partner id in the URL to change.
    assert (
        anon.get(
            f"{PARTNER_API}/{other['partner'].id}/account", **bearer(created["secret"])
        ).status_code
        == 404
    )


def test_use_is_recorded_without_a_write_per_request(seller, owner_client, anon):
    from workeasy360.commercial.partners.models import PartnerApiKey

    created = make_key(owner_client, seller["partner"].id)
    row = PartnerApiKey.objects.get(id=created["key"]["id"])
    assert row.last_used_at is None
    anon.get(f"{PARTNER_API}/customers", **bearer(created["secret"]))
    first = PartnerApiKey.objects.get(id=row.id).last_used_at
    assert first is not None
    anon.get(f"{PARTNER_API}/customers", **bearer(created["secret"]))
    assert (
        PartnerApiKey.objects.get(id=row.id).last_used_at == first
    )  # inside the resolution window


def test_a_key_is_rate_limited_and_so_are_bad_credentials(
    seller, owner_client, anon, settings
):
    created = make_key(owner_client, seller["partner"].id)
    settings.PARTNER_API_RATE_PER_KEY = 2
    token = bearer(created["secret"])
    codes = [anon.get(f"{PARTNER_API}/customers", **token).status_code for _ in range(4)]
    assert codes[:2] == [200, 200] and codes[2:] == [429, 429]
    cache.clear()
    settings.CONTROL_PLANE_RATE_LIMITS = {
        **settings.CONTROL_PLANE_RATE_LIMITS,
        "partner_api_fail_ip": (3, 60),
    }
    refused = [
        anon.get(f"{PARTNER_API}/customers", **bearer("w360pk_nope")).status_code
        for _ in range(5)
    ]
    assert refused[:2] == [401, 401] and refused[-1] == 429


def test_managing_keys_is_rate_limited(seller, owner_client, settings):
    settings.CONTROL_PLANE_RATE_LIMITS = {
        **settings.CONTROL_PLANE_RATE_LIMITS,
        "partner_apikey_user": (2, 60),
    }
    pid = seller["partner"].id
    codes = [
        send(
            owner_client,
            "post",
            api(pid, "api-keys"),
            {"name": f"k{n}", "scopes": ["account.read"]},
        ).status_code
        for n in range(4)
    ]
    assert codes[:2] == [201, 201] and codes[-1] == 429


def test_creating_a_key_needs_a_csrf_token(seller, owner_client, http_login):
    client = http_login(seller["user"], enforce_csrf=True)
    response = client.post(
        api(seller["partner"].id, "api-keys"),
        json.dumps({"name": "x", "scopes": ["account.read"]}),
        content_type="application/json",
    )
    assert response.status_code == 403


def test_no_secret_of_workeasy360_is_stored_or_returned_by_the_key_screens():
    """The models and modules hold no cloud, master, signing or database secret."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "workeasy360"
    source = "\n".join(
        (root / "commercial" / "partners" / name).read_text(encoding="utf-8")
        for name in ("api_keys.py",)
    ) + (root / "interfaces" / "api" / "v1" / "partner_api_views.py").read_text(
        encoding="utf-8"
    )
    code = re.sub(r'""".*?"""', "", source, flags=re.S)
    code = re.sub(r"#.*", "", code)
    assert not re.search(
        r"AWS_|SECRET_KEY|DB_APP_PASSWORD|settings\.DATABASES|private_key", code
    )
