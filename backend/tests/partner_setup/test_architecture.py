"""Architecture pins for the complete reseller and white-label setup.

The two new tables are the *only* structure this work adds, and each is pinned
for what it must not become: a partner type, an account, a permission store, a
second price or commission, a second email system.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from tests.phase10.test_10f_architecture import (
    COMMERCIAL,
    PACKAGE,
    _classes,
    _imports,
    _model_names,
    _python_sources,
    _rel,
)

pytestmark = pytest.mark.django_db

PARTNERS = COMMERCIAL / "partners"
ROOT = pathlib.Path(__file__).resolve().parents[3]


def test_the_partner_models_are_the_frozen_set_plus_the_declared_additions():
    assert _model_names("partners") == {
        "PartnerAccount",
        "PartnerCapabilityGrant",
        "PartnerMember",
        "PartnerOrganizationLink",
        "PartnerProductGrant",
        "PartnerBrand",
        "PartnerDomain",
        "PartnerOwnerInvite",
        "PartnerLogoUpload",
        "PartnerProfile",
        "PartnerContact",
        # Partner business control panel (ADR-045): the sub-partner relationship
        # and a partner's per-product presentation. Pinned in tests/partner_panel.
        "PartnerHierarchyLink",
        "PartnerProductSetting",
        # The final partner menu (ADR-046): API keys, translations and read-state.
        "PartnerApiKey",
        "PartnerTranslation",
        "PartnerNotificationRead",
        "PartnerNotificationState",
        # The partner upgrade (ADR-049): the one workflow record an Affiliate ->
        # Reseller / White-label request needed. tests/partner_upgrade pins what
        # it may never become (no kind of partner, no grant, no amount).
        "PartnerUpgradeRequest",
    }


def test_there_is_no_partner_type_and_no_reseller_or_white_label_model_anywhere():
    banned = re.compile(
        r"^(Reseller|WhiteLabel|Affiliate|Partner(Type|Kind|Class|Tier|Category))\w*$"
    )
    found = [node.name for _path, node in _classes(PACKAGE) if banned.match(node.name)]
    assert found == [], found
    from workeasy360.commercial.partners.models import PartnerAccount

    columns = {f.name for f in PartnerAccount._meta.get_fields()}
    assert (
        not {
            "type",
            "kind",
            "partner_type",
            "category",
            "tier",
            "is_reseller",
            "is_white_label",
            "is_affiliate",
        }
        & columns
    )


def test_the_profile_and_contact_tables_hold_no_authority():
    from workeasy360.commercial.partners.models import PartnerContact, PartnerProfile

    forbidden = re.compile(
        r"password|permission|role|capabilit|grant|price|amount|rate|commission|secret|token|bank|iban|account_number|card"
    )
    for model in (PartnerProfile, PartnerContact):
        offending = [f.name for f in model._meta.get_fields() if forbidden.search(f.name)]
        assert offending == [], (model.__name__, offending)


def test_a_contact_is_not_an_identity():
    from workeasy360.commercial.partners.models import PartnerContact

    names = {f.name for f in PartnerContact._meta.get_fields()}
    assert names == {
        "id",
        "partner",
        "kind",
        "name",
        "job_title",
        "email",
        "phone",
        "updated_by",
        "updated_at",
    }
    assert not any(f.name == "user" for f in PartnerContact._meta.get_fields())


def test_the_migration_only_adds_columns_and_two_tables_and_rewrites_no_data():
    source = (
        PARTNERS / "migrations" / "0004_partner_setup_profile_contacts.py"
    ).read_text(encoding="utf-8")
    operations = [
        n
        for n in re.findall(r"migrations\.(\w+)\(", source)
        if n != "swappable_dependency"
    ]
    assert set(operations) <= {"AddField", "CreateModel"}, operations
    assert operations.count("CreateModel") == 2
    assert "RunPython" not in source and "RunSQL" not in source  # no data is rewritten
    assert (
        "RemoveField" not in source
        and "DeleteModel" not in source
        and "AlterField" not in source
    )


def test_no_other_app_gained_a_migration_for_this():
    for app in (
        "commissions",
        "pricing",
        "provisioning",
        "subscriptions",
        "billing",
        "catalog",
        "payments",
    ):
        newest = sorted((COMMERCIAL / app / "migrations").glob("0*.py"))[-1].name
        assert "partner_setup" not in newest, (app, newest)


def test_the_profile_modules_read_and_write_no_money_and_no_entitlement():
    for name in ("profile.py", "portal.py", "inputs.py"):
        imported = _imports(PARTNERS / name)
        offending = [
            m
            for m in imported
            if re.search(
                r"commercial\.(pricing|commissions|payments|billing|subscriptions|provisioning)|entitlements",
                m,
            )
        ]
        assert offending == [], (name, offending)


def test_white_label_is_enabled_by_granting_capabilities_not_by_creating_anything():
    source = (PARTNERS / "services.py").read_text(encoding="utf-8")
    body = source[source.index("def enable_white_label(") : source.index("# --- members")]
    assert "grant_capability(" in body
    assert "PartnerAccount" not in body.replace(
        "PartnerAccount.objects.select_for_update", ""
    )
    assert ".create(" not in body


def test_the_wizard_writes_through_the_existing_commands_only():
    """The reseller review is a read; the staff endpoints call the one command for each write."""
    review = (COMMERCIAL / "controlplane" / "reseller_setup.py").read_text(
        encoding="utf-8"
    )
    assert not re.search(r"\.(create|save|update|delete|bulk_create)\(", review)
    assert "audit.record" not in review
    views = (PACKAGE / "interfaces" / "api" / "v1" / "admin_views.py").read_text(
        encoding="utf-8"
    )
    for command in (
        "partner_profile.update_profile",
        "partner_services.enable_white_label",
        "partner_services.update_product_grant",
        "partner_services.change_member_role",
    ):
        assert command in views, command


def test_there_is_still_exactly_one_price_function_and_one_commission_function():
    prices = [
        _rel(p)
        for p in _python_sources()
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
        if isinstance(n, ast.FunctionDef) and n.name == "resolve_price"
    ]
    assert prices == ["backend/workeasy360/commercial/pricing/resolution.py"]
    amounts = [
        _rel(p)
        for p in _python_sources()
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
        if isinstance(n, ast.FunctionDef) and n.name == "commission_amount"
    ]
    assert amounts == ["backend/workeasy360/commercial/commissions/domain.py"]


def test_there_is_one_notification_path_and_no_second_email_system():
    import_lines = [
        (p, line)
        for p in _python_sources(COMMERCIAL)
        for line in p.read_text(encoding="utf-8").splitlines()
        if re.match(
            r"\s*(from|import)\s+(smtplib|django\.core\.mail|sendgrid|boto3|requests)",
            line,
        )
    ]
    assert import_lines == [], import_lines


def test_the_money_figures_the_review_shows_are_computed_in_python_only():
    """No source file of this work computes a price, discount or commission in the browser."""
    frontend = ROOT / "frontend" / "src" / "features" / "controlplane"
    suspicious = re.compile(
        r"(\*\s*(rate|bp|basis|percent)\w*\s*/\s*10[_]?000|\bMath\.(round|floor|ceil)\([^)]*(price|amount|minor|commission|rate))"
    )
    offenders = []
    for path in list(frontend.rglob("*.ts")) + list(frontend.rglob("*.tsx")):
        if ".test." in path.name:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if suspicious.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert offenders == [], offenders


def test_the_country_list_the_form_offers_is_the_list_the_server_accepts():
    from workeasy360.contracts.values.countries import COUNTRY_CODES

    text = (
        ROOT / "frontend" / "src" / "features" / "controlplane" / "countries.ts"
    ).read_text(encoding="utf-8")
    block = text[text.index("COUNTRY_CODES") : text.index(".split")]
    codes = set(re.findall(r'"([A-Z0-9 ]+)"', block.replace('" +\n  "', " ")))
    offered = set(" ".join(codes).split())
    assert offered == set(COUNTRY_CODES)
    assert len(COUNTRY_CODES) == 249


def test_the_decision_is_recorded():
    adr = (
        ROOT
        / "docs"
        / "architecture"
        / "adrs"
        / "ADR-042-partner-setup-profile-and-contacts.md"
    )
    text = adr.read_text(encoding="utf-8")
    for needle in (
        "PartnerProfile",
        "PartnerContact",
        "trial_allowed",
        "portal_visible",
        "invitation_sent_at",
        "Alternatives",
    ):
        assert needle in text, needle
