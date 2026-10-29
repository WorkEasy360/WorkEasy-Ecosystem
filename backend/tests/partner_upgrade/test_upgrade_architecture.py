"""What the upgrade must never become (ADR-049, brief section 8).

Upgrading a partner is a request record and an additive grant through the one
transition service. These tests read the source so that a later change cannot
quietly give it a second user, a second account, a second permission system, a
price of its own, or a second writer for its table.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.phase10.test_10f_architecture import (
    COMMERCIAL,
    PACKAGE,
    _classes,
    _imports,
    _python_sources,
    _rel,
)

PARTNERS = COMMERCIAL / "partners"
CONTROLPLANE = COMMERCIAL / "controlplane"
UPGRADES = PARTNERS / "upgrades.py"
APPROVAL = CONTROLPLANE / "partner_upgrades.py"
MODULES = (UPGRADES, APPROVAL)
FRONTEND_SRC = Path(__file__).resolve().parents[3] / "frontend" / "src"


def test_only_the_request_module_writes_the_request_table():
    allowed = {
        "commercial/partners/models.py",
        "commercial/partners/upgrades.py",
    }
    users = {
        _rel(path).removeprefix("backend/workeasy360/")
        for path in _python_sources()
        if "PartnerUpgradeRequest" in path.read_text(encoding="utf-8")
        and "/tests/" not in path.as_posix()
        and "/migrations/" not in path.as_posix()
    }
    assert users <= allowed, users - allowed
    # The approval module reads and stamps requests through the request module.
    assert "PartnerUpgradeRequest" not in APPROVAL.read_text(encoding="utf-8")
    assert not [i for i in _imports(APPROVAL) if i.endswith("partners.models")]


@pytest.mark.parametrize("module", MODULES, ids=lambda p: p.name)
def test_the_upgrade_creates_no_user_login_password_or_partner(module):
    source = module.read_text(encoding="utf-8")
    for forbidden in (
        "create_account(",
        "create_partner(",
        "create_user(",
        "User.objects",
        "set_password(",
        "make_password(",
        "authenticate(",
        "django_login(",
        "PartnerAccount.objects.create",
        "PartnerMember.objects.create",
        "attach_user_as_member(",
        "invite_member(",
    ):
        assert forbidden not in source, forbidden
    assert "password" not in source.lower(), "an upgrade never touches a password"


@pytest.mark.parametrize("module", MODULES, ids=lambda p: p.name)
def test_the_upgrade_writes_no_capability_ledger_price_or_entitlement_directly(module):
    imported = _imports(module)
    offending = [
        m
        for m in imported
        if re.search(
            r"commissions\.models|commissions\.ledger|pricing\.models|"
            r"entitlements\.services\.write|subscriptions|provisioning",
            m,
        )
    ]
    assert offending == [], offending
    source = module.read_text(encoding="utf-8")
    # Grants come from the one transition service, never from a row written here.
    assert "PartnerCapabilityGrant" not in source
    assert "PartnerProductGrant" not in source
    assert "_grant_capability(" not in source
    assert "grant_capability(" not in source


def test_the_request_module_touches_only_the_request_table_and_reads_the_profile():
    models = set(
        re.findall(
            r"partners\.models import (\w+)",
            UPGRADES.read_text(encoding="utf-8"),
        )
    )
    # ADR-053: and the set-up fee setting it snapshots onto a request.
    assert models == {"PartnerProfile", "PartnerUpgradeRequest", "PartnerProgramFee"}


def test_approval_grants_through_the_one_transition_service():
    source = APPROVAL.read_text(encoding="utf-8")
    assert source.count("experience.transition_experience(") == 1
    # And the partner-side module cannot grant at all: it never imports it.
    assert not [i for i in _imports(UPGRADES) if i.endswith("partners.experience")]
    assert "transition_experience(" not in UPGRADES.read_text(encoding="utf-8")


@pytest.mark.parametrize("module", MODULES, ids=lambda p: p.name)
def test_no_fee_or_amount_is_written_into_the_upgrade(module):
    """Set-up fees belong to Super Admin configuration (ADR-053:
    ``PartnerProgramFee``, read and snapshotted, never written here as a
    figure); this workflow charges nothing and invents no amount."""
    source = module.read_text(encoding="utf-8")
    assert not re.search(r"lakh|\b(20_?000|30_?000|100_?000|200_?000)\b", source, re.I)
    assert not re.search(r"[₹$£€]", source)
    # No charge is made: nothing here creates an order, an invoice or a payment.
    assert not re.search(
        r"payments\.(models|orders|checkout)|Invoice\(|create_invoice|onboarding_fee",
        source,
    )


def test_no_model_or_column_makes_a_partner_a_reseller_a_white_label_or_an_affiliate():
    banned = re.compile(
        r"^(Reseller|WhiteLabel|Affiliate)\w*$|^Partner(Type|Kind|Class|Tier|Category)\w*$"
    )
    assert [n.name for _p, n in _classes(PACKAGE) if banned.match(n.name)] == []
    from workeasy360.commercial.partners.models import PartnerUpgradeRequest

    fields = PartnerUpgradeRequest._meta.get_fields()
    assert not {"password", "email", "partner_type"} & {f.name for f in fields}
    # It points at the one account and the one user model.
    points_at = {
        f.name: f.related_model.__name__
        for f in fields
        if f.is_relation and f.many_to_one
    }
    assert points_at["partner"] == "PartnerAccount"
    assert points_at["requested_by"] == "User"


def test_the_staff_routes_are_staff_views_and_the_partner_routes_are_partner_views():
    admin = (PACKAGE / "interfaces" / "api" / "v1" / "admin_views.py").read_text(
        encoding="utf-8"
    )
    for name in (
        "partner_upgrade_queue_view",
        "partner_upgrades_view",
        "partner_upgrade_decision_view",
    ):
        assert re.search(rf"@staff_view\(\[\"(GET|POST)\"\]\)\ndef {name}\(", admin), name
    panel = (PACKAGE / "interfaces" / "api" / "v1" / "partner_panel_views.py").read_text(
        encoding="utf-8"
    )
    for name in (
        "upgrade_state_view",
        "upgrade_start_view",
        "upgrade_section_view",
        "upgrade_submit_view",
        "upgrade_cancel_view",
    ):
        assert re.search(
            rf"@partner_view\(\[\"(GET|POST|PUT)\"\]\)\ndef {name}\(", panel
        ), name
    # No partner route decides anything: there is no approve/grant/decision
    # handler among them.
    assert not re.search(r"def upgrade_\w*(approve|decide|grant|decision)\w*\(", panel)


def test_no_upgrade_route_is_a_login():
    from django.urls import get_resolver

    names: list[str] = []

    def walk(patterns):
        for p in patterns:
            if hasattr(p, "url_patterns"):
                walk(p.url_patterns)
            elif p.name:
                names.append(p.name)

    walk(get_resolver().url_patterns)
    upgrade = [n for n in names if "upgrade" in n]
    assert len(upgrade) >= 8
    assert not [
        n for n in upgrade if re.search(r"(^|[-_])(login|signin|sign-in|password)", n)
    ]


def test_the_migration_only_creates_the_table():
    source = next((PARTNERS / "migrations").glob("0009_partner_upgrade_request.py"))
    text = source.read_text(encoding="utf-8")
    operations = re.findall(
        r"migrations\.(CreateModel|Add\w+|Alter\w+|Remove\w+|Rename\w+|Delete\w+|Run\w+)\(",
        text,
    )
    assert operations == ["CreateModel"]
    assert "RunPython" not in text and "RunSQL" not in text


def test_the_frontend_reads_nothing_from_browser_storage_to_decide_an_upgrade():
    """The brief's test: an affiliate editing ``partner_type`` in localStorage is
    still refused. The upgrade screens must not read any stored state at all."""
    folder = FRONTEND_SRC / "features" / "partnerpanel"
    present = list(folder.glob("Upgrade*.tsx")) if folder.exists() else []
    if not present:
        pytest.skip("the frontend upgrade screens are not part of this checkout")
    for path in present:
        text = path.read_text(encoding="utf-8")
        assert "localStorage" not in text and "sessionStorage" not in text, path.name
        assert "partner_type" not in text, path.name
