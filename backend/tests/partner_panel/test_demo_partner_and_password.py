"""The development-only demo partner and the temporary-password rule."""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from tests.partner_panel.conftest import api
from workeasy360.commercial.controlplane import demo_partner
from workeasy360.commercial.partners import capabilities
from workeasy360.commercial.partners.models import (
    PartnerAccount,
    PartnerCapabilityGrant,
    PartnerMember,
)
from workeasy360.platform.identity.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def demo(catalog):
    return demo_partner.create_demo_partner()


def test_it_builds_one_ordinary_partner_with_every_capability_and_an_owner(demo):
    partner = PartnerAccount.objects.get(code=demo_partner.DEMO_CODE)
    assert partner.status == "active"
    held = set(
        PartnerCapabilityGrant.objects.filter(
            partner=partner, status="active"
        ).values_list("capability", flat=True)
    )
    assert held == set(capabilities.CAPABILITIES)
    member = PartnerMember.objects.get(partner=partner)
    assert member.user.email == demo_partner.DEMO_EMAIL and member.role == "owner"
    assert {c["label"] for c in capabilities.experience_labels(held)} == {
        "Reseller",
        "White-label",
        "Affiliate",
    }


def test_the_password_is_random_returned_once_and_never_stored_in_clear(demo):
    assert demo.user_created and demo.temporary_password
    assert len(demo.temporary_password) >= 24
    again = demo_partner.create_demo_partner()
    assert again.temporary_password is None  # not changed, not shown again
    assert not again.user_created and not again.partner_created
    user = User.objects.get(email=demo_partner.DEMO_EMAIL)
    assert user.check_password(demo.temporary_password)
    assert demo.temporary_password not in user.password
    assert demo_partner.new_temporary_password() != demo_partner.new_temporary_password()


def test_running_twice_does_not_duplicate_anything(demo):
    demo_partner.create_demo_partner()
    assert PartnerAccount.objects.filter(code=demo_partner.DEMO_CODE).count() == 1
    assert (
        PartnerMember.objects.filter(
            partner__code=demo_partner.DEMO_CODE, status="active"
        ).count()
        == 1
    )
    assert User.objects.filter(email=demo_partner.DEMO_EMAIL).count() == 1


def test_a_reset_gives_a_new_password_and_requires_a_change(demo):
    reset = demo_partner.create_demo_partner(reset_password=True)
    assert (
        reset.temporary_password and reset.temporary_password != demo.temporary_password
    )
    user = User.objects.get(email=demo_partner.DEMO_EMAIL)
    assert user.password_change_required is True
    assert not user.check_password(demo.temporary_password)


@pytest.mark.parametrize("environment", ["production", "staging", "", "prod", "live"])
def test_it_refuses_to_exist_outside_development(settings, environment):
    settings.ENVIRONMENT = environment
    with pytest.raises(demo_partner.DemoPartnerRefused):
        demo_partner.create_demo_partner()
    assert not PartnerAccount.objects.filter(code=demo_partner.DEMO_CODE).exists()
    assert not User.objects.filter(email=demo_partner.DEMO_EMAIL).exists()
    with pytest.raises(CommandError):
        call_command("create_demo_partner", stdout=io.StringIO())


def test_the_command_will_not_run_where_it_could_not_show_the_password_once(
    monkeypatch, catalog
):
    # Under pytest stdout is not a terminal: a password that could not be shown
    # must never be created.
    with pytest.raises(CommandError):
        call_command("create_demo_partner", stdout=io.StringIO())
    assert not User.objects.filter(email=demo_partner.DEMO_EMAIL).exists()


def test_the_command_shows_the_password_once_in_an_interactive_terminal(
    monkeypatch, catalog, capsys
):
    import sys

    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    out = Terminal()
    monkeypatch.setattr(sys, "stdout", out)
    call_command("create_demo_partner", stdout=out)
    shown = out.getvalue()
    assert demo_partner.DEMO_EMAIL in shown and "shown ONCE" in shown
    user = User.objects.get(email=demo_partner.DEMO_EMAIL)
    password = next(
        line.strip() for line in shown.splitlines() if line.startswith("    ")
    )
    assert user.check_password(password)
    second = Terminal()
    call_command("create_demo_partner", stdout=second)
    assert password not in second.getvalue()


def test_the_demo_login_is_blocked_everywhere_but_the_password_change(
    demo, http_login_with_password
):
    client = http_login_with_password(demo_partner.DEMO_EMAIL, demo.temporary_password)
    assert client.get("/api/v1/auth/session").json()["password_change_required"] is True
    pid = demo.partner_id
    blocked = client.get(api(pid, "dashboard"))
    assert (
        blocked.status_code == 403
        and blocked.json()["code"] == "password_change_required"
    )
    assert client.get("/api/v1/organizations").status_code == 403
    assert (
        client.get("/api/v1/partner/portal").status_code == 200
    )  # the shell needs it to render the screen


def test_changing_the_password_lifts_the_block(demo, http_login_with_password):
    client = http_login_with_password(demo_partner.DEMO_EMAIL, demo.temporary_password)
    new = "a brand new passphrase for the owner 7"
    changed = client.post(
        "/api/v1/auth/password",
        {"current_password": demo.temporary_password, "new_password": new},
        content_type="application/json",
    )
    assert changed.status_code == 200, changed.content
    assert (
        User.objects.get(email=demo_partner.DEMO_EMAIL).password_change_required is False
    )
    assert client.get("/api/v1/auth/session").json()["password_change_required"] is False
    assert client.get(api(demo.partner_id, "dashboard")).status_code == 200


def test_the_demo_owner_runs_the_whole_panel(demo, http_login_with_password):
    client = http_login_with_password(
        demo_partner.DEMO_EMAIL,
        demo.temporary_password,
        change_to="x" * 3 + " long enough passphrase 99",
    )
    pid = demo.partner_id
    portal = client.get("/api/v1/partner/portal").json()["partners"][0]
    assert portal["role"] == "owner"
    for path in (
        "dashboard",
        "balance",
        "products",
        "plans",
        "members",
        "profile",
        "activity",
        "sub-partners",
        "affiliates",
        "currencies",
        "price-book",
        "packages",
        "communication",
        "authentication",
        "orders",
        "features",
    ):
        response = client.get(api(pid, path))
        assert response.status_code == 200, (
            path,
            response.status_code,
            response.content[:200],
        )


def test_no_password_is_committed_anywhere_in_the_demo_code():
    import pathlib
    import re

    root = pathlib.Path(demo_partner.__file__).resolve().parent
    for source in (
        root / "demo_partner.py",
        root / "management" / "commands" / "create_demo_partner.py",
    ):
        text = source.read_text(encoding="utf-8")
        assert not re.search(r"password\s*=\s*[\"'][^\"']{6,}[\"']", text, re.I), (
            source.name
        )
    assert "token_urlsafe" in (root / "demo_partner.py").read_text(encoding="utf-8")
