"""Architecture pins for the partner portal's final menu (ADR-046): what the new
pieces may never become."""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.django_db

PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "workeasy360"
PARTNERS = PACKAGE / "commercial" / "partners"
NEW_MODULES = [
    PARTNERS / "api_keys.py",
    PARTNERS / "translations.py",
    PARTNERS / "notification_state.py",
    PACKAGE / "commercial" / "controlplane" / "partner_help.py",
    PACKAGE / "interfaces" / "api" / "v1" / "partner_api_views.py",
]


def test_the_migration_only_adds_tables_and_a_choices_change():
    source = sorted((PARTNERS / "migrations").glob("0006_*.py"))[0].read_text(
        encoding="utf-8"
    )
    ops = [
        n
        for n in re.findall(r"migrations\.(\w+)\(", source)
        if n != "swappable_dependency"
    ]
    assert set(ops) <= {"CreateModel", "AlterField"}, ops
    assert ops.count("CreateModel") == 4
    assert "RunPython" not in source and "RunSQL" not in source
    assert "RemoveField" not in source and "DeleteModel" not in source


def test_the_new_modules_never_write_an_entitlement_a_price_or_the_ledger():
    forbidden = re.compile(
        r"entitlements\.services|pricing\.services|publish_price|commissions\.(accrual|ledger|payouts)"
    )
    for path in NEW_MODULES:
        assert not forbidden.search(path.read_text(encoding="utf-8")), path.name


def test_the_new_modules_use_no_floating_point_money_and_no_raw_sql():
    for path in NEW_MODULES:
        source = path.read_text(encoding="utf-8")
        assert not re.search(
            r"\bfloat\(|\bDecimal\b|\.raw\(|cursor\(\)|\.extra\(", source
        ), path.name


def test_the_partner_api_accepts_get_only_and_never_reads_the_session():
    source = (PACKAGE / "interfaces" / "api" / "v1" / "partner_api_views.py").read_text(
        encoding="utf-8"
    )
    assert 'require_http_methods(["GET"])' in source
    code = re.sub(r'""".*?"""', "", source, flags=re.S)
    assert not re.search(
        r"request\.user|request\.session|login_required|partner_view|staff_view", code
    )


def test_a_key_secret_is_hashed_with_a_fast_hash_only_because_it_is_random():
    source = (PARTNERS / "api_keys.py").read_text(encoding="utf-8")
    assert "secrets.token_urlsafe(32)" in source  # 256 random bits
    assert "compare_digest" in source  # constant time
    assert "make_password" not in source and "check_password" not in source


def test_no_translation_value_can_carry_markup_or_template_syntax():
    source = (PARTNERS / "translations.py").read_text(encoding="utf-8")
    assert "clean_markup_free" in source
    assert "{}$%" in source  # the brace / template characters it refuses


def test_the_notification_center_has_no_table_of_its_own_for_events():
    from django.apps import apps

    for model in apps.get_app_config("partners").get_models():
        fields = {f.name for f in model._meta.get_fields()}
        if model.__name__.startswith("PartnerNotification"):
            assert not fields & {
                "title",
                "body",
                "message",
                "payload",
                "changes",
                "context",
            }


def test_every_permission_the_menu_asks_for_is_declared_by_the_registry():
    menu = (
        pathlib.Path(__file__).resolve().parents[3]
        / "frontend"
        / "src"
        / "features"
        / "partnerpanel"
        / "menu.ts"
    ).read_text(encoding="utf-8")
    asked = set()
    for call in re.findall(r"\bcan\(([^)]*)\)", menu):
        asked |= set(re.findall(r'"([a-z]+\.[a-z]+)"', call))
    assert len(asked) > 10
    from workeasy360.commercial.partners.permissions import PARTNER_PERMISSIONS

    declared = {str(d.key) for d in PARTNER_PERMISSIONS}
    assert {f"platform.partner_{k}" for k in asked} <= declared
