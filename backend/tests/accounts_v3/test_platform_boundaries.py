"""Accounts V3 platform boundaries (sections 03, 2.7, 6.7, 10.1): the built-in
AI needs no customer key, and personal Accounts holds no wallet, store,
catalog, organization admin or deployment module of its own."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def test_ai_provider_keys_are_the_platforms_never_an_organizations():
    """No customer LLM key: a provider belongs to the platform (no
    organization column), and only a reference to its key is stored."""
    from workeasy360.intelligence.ai.models import AIProvider

    columns = {f.name for f in AIProvider._meta.get_fields()}
    assert "organization" not in columns and "organization_id" not in columns
    assert "credential_ref" in columns
    assert not {"api_key", "secret", "key_value"} & columns


def test_personal_accounts_endpoints_hold_no_wallet_store_or_admin_module():
    from django.urls import get_resolver

    names: list[str] = []

    def walk(patterns, prefix=""):
        for p in patterns:
            if hasattr(p, "url_patterns"):
                walk(p.url_patterns, prefix + str(p.pattern))
            elif p.name:
                names.append(prefix + str(p.pattern))

    walk(get_resolver().url_patterns)
    personal = [n for n in names if "auth/account/" in n]
    assert personal, "the Accounts Control Center routes exist"
    forbidden = (
        "wallet",
        "store",
        "catalog",
        "members",
        "roles",
        "deploy",
        "github",
        "checkout",
    )
    assert not [n for n in personal if any(word in n for word in forbidden)]
