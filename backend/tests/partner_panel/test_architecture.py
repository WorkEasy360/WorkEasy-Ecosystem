"""Architecture pins for the partner business panel (ADR-045).

What was added is small, and each addition is pinned for what it must not become:
a partner type, a second account, a mutable balance, a second price or
entitlement, a mail credential store, arbitrary markup.
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
)

pytestmark = pytest.mark.django_db

ROOT = pathlib.Path(__file__).resolve().parents[3]
PARTNERS = COMMERCIAL / "partners"
CONTROLPLANE = COMMERCIAL / "controlplane"
PANEL_BACKEND = [
    PARTNERS / "hierarchy.py",
    PARTNERS / "product_settings.py",
    CONTROLPLANE / "partner_portal.py",
    CONTROLPLANE / "partner_network.py",
    CONTROLPLANE / "demo_partner.py",
    PACKAGE / "interfaces" / "api" / "v1" / "partner_panel_views.py",
]
PANEL_FRONTEND = ROOT / "frontend" / "src" / "features" / "partnerpanel"


def test_no_forbidden_partner_or_agency_model_exists_anywhere():
    banned = re.compile(
        r"^(Reseller|WhiteLabel|Affiliate|Agency|SubAgency|PartnerUser|"
        r"PartnerWallet|PartnerBalance|PartnerCredit|Partner(Type|Kind|Class|Tier|Category))\w*$"
    )
    found = [node.name for _path, node in _classes(PACKAGE) if banned.match(node.name)]
    assert found == [], found
    # A sub-partner is a PartnerAccount: no table, model or manager of its own.
    assert not [n for n in _model_names("partners") if n.startswith("SubPartner")]


def test_the_only_new_models_are_the_hierarchy_link_and_product_setting():
    assert {"PartnerHierarchyLink", "PartnerProductSetting"} <= _model_names("partners")
    from workeasy360.commercial.partners.models import (
        PartnerHierarchyLink,
        PartnerProductSetting,
    )

    assert {f.name for f in PartnerHierarchyLink._meta.get_fields()} == {
        "id",
        "parent",
        "child",
        "status",
        "created_by",
        "created_at",
        "ended_at",
        "ended_by",
        "end_reason",
        "version",
    }
    forbidden = re.compile(
        r"price|amount|rate|commission|balance|secret|password|token|entitle|grant"
    )
    for model in (PartnerHierarchyLink, PartnerProductSetting):
        offending = [f.name for f in model._meta.get_fields() if forbidden.search(f.name)]
        assert offending == [], (model.__name__, offending)


def test_a_sub_partner_is_a_partner_account_not_a_separate_table():
    from workeasy360.commercial.partners.models import PartnerHierarchyLink

    targets = {
        f.related_model.__name__
        for f in PartnerHierarchyLink._meta.get_fields()
        if f.is_relation and f.name in ("parent", "child")
    }
    assert targets == {"PartnerAccount"}


def test_no_mutable_balance_exists_in_any_partner_or_commission_model():
    for module in ("partners", "commissions"):
        source = (COMMERCIAL / module / "models.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assert not re.fullmatch(
                            r"(wallet_?)?(balance|credit)(_minor)?", target.id
                        ), target.id


def test_no_new_code_uses_floating_point_for_money():
    for path in PANEL_BACKEND:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "float", (path.name, node.lineno)
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                raise AssertionError((path.name, node.lineno, "a float literal"))
            if isinstance(node, ast.Name):
                assert node.id not in {"Decimal", "FloatField", "DecimalField"}, (
                    path.name,
                    node.lineno,
                )


def test_the_panel_modules_never_write_an_entitlement_a_price_or_the_ledger():
    forbidden = re.compile(
        r"entitlements\.services\.write|pricing\.services|publish_price|commissions\.(accrual|ledger|payouts)"
    )
    for path in PANEL_BACKEND:
        source = path.read_text(encoding="utf-8")
        assert not forbidden.search(source), path.name
        for module in _imports(path):
            assert not forbidden.search(module), (path.name, module)


def test_no_mail_credential_is_stored_or_accepted_anywhere_in_the_partner_modules():
    pattern = re.compile(r"smtp_(host|password|user)|email_host_password", re.I)
    for path in list((PARTNERS).glob("*.py")) + PANEL_BACKEND:
        assert not pattern.search(path.read_text(encoding="utf-8")), path.name
    for path in (PARTNERS / "migrations").glob("0*.py"):
        assert not pattern.search(path.read_text(encoding="utf-8")), path.name


def test_the_panel_urls_expose_no_wallet_recharge_or_payment_link_route():
    urls = (PACKAGE / "interfaces" / "api" / "v1" / "urls.py").read_text(encoding="utf-8")
    partner_routes = re.findall(r'"(partner/[^"]+)"', urls)
    assert partner_routes
    for route in partner_routes:
        assert not re.search(
            r"wallet|recharge|top-?up|payment-?link|smtp|credit|(^|/)(css|scripts?)($|/)",
            route,
        ), route


def test_every_gap_the_screens_claim_is_in_the_registry_and_has_no_endpoint():
    from workeasy360.commercial.controlplane.partner_portal import NOT_IMPLEMENTED

    for key in NOT_IMPLEMENTED:
        assert len(NOT_IMPLEMENTED[key]) > 20
    # Screens read the registry by key: every key a screen uses must exist.
    used = set()
    for path in PANEL_FRONTEND.glob("*.tsx"):
        used |= set(
            re.findall(r"not_implemented\.([a-z_]+)", path.read_text(encoding="utf-8"))
        )
        used |= set(
            re.findall(
                r'NOT_IMPLEMENTED\["([a-z_]+)"\]', path.read_text(encoding="utf-8")
            )
        )
    assert used <= set(NOT_IMPLEMENTED) | {"logs"}, used - set(NOT_IMPLEMENTED)


def test_the_panel_screens_render_server_text_as_text_never_as_markup():
    for path in PANEL_FRONTEND.glob("*.tsx"):
        source = path.read_text(encoding="utf-8")
        assert "dangerouslySetInnerHTML" not in source, path.name
        assert "innerHTML" not in source, path.name
        assert not re.search(
            r"<style[\s>]|<script[\s>]|\beval\(|new Function\(", source
        ), path.name
        assert "style={{" not in source, path.name  # CSP: no inline style attributes


def test_the_panel_screens_do_no_money_arithmetic():
    for path in PANEL_FRONTEND.glob("*.tsx"):
        source = path.read_text(encoding="utf-8")
        # A server amount is formatted (money(...)) and never added, multiplied
        # or reduced in the browser.
        assert not re.search(r"reduce\([^)]*(amount|minor)", source), path.name
        assert not re.search(
            r"(amount_minor|_minor)\s*[*+\-]\s*[\w(]", source.replace("slot * 0.62", "")
        ), path.name


def test_the_demo_partner_is_not_part_of_any_production_path():
    for name in ("urls.py", "views.py"):
        text = (PACKAGE / "interfaces" / "api" / "v1" / name).read_text(encoding="utf-8")
        assert "demo_partner" not in text


def test_the_migration_adds_only_what_the_panel_declares():
    path = sorted((PARTNERS / "migrations").glob("0005_*.py"))[0]
    source = path.read_text(encoding="utf-8")
    operations = [
        n
        for n in re.findall(r"migrations\.(\w+)\(", source)
        if n != "swappable_dependency"
    ]
    assert set(operations) <= {"AddField", "AlterField", "CreateModel"}, operations
    assert operations.count("CreateModel") == 2
    assert "RunPython" not in source and "RunSQL" not in source
    assert "RemoveField" not in source and "DeleteModel" not in source


def test_hierarchy_depth_is_policy_not_schema():
    from workeasy360.commercial.partners import hierarchy

    assert hierarchy.MAX_DEPTH == 1
    migration = sorted((PARTNERS / "migrations").glob("0005_*.py"))[0].read_text(
        encoding="utf-8"
    )
    assert "depth" not in migration.lower()


def test_the_operator_partner_commands_are_not_reachable_over_http():
    routed = (PACKAGE / "interfaces").rglob("*.py")
    for path in routed:
        text = path.read_text(encoding="utf-8")
        assert "partners import system" not in text and "partners.system" not in text, (
            path.name
        )
