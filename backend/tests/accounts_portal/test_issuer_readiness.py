"""Retiring the previous issuer name on evidence, not on a timer (ADR-067 stages d and e).

``w360_issuer_readiness`` answers READY only when the configuration, the database and a full
window of API logs all show that nothing depends on the name; NOT READY when anything still
does; NOT ENOUGH EVIDENCE when it cannot tell.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from django.core.management import call_command

from tests.accounts_portal.conftest import ACCOUNTS, AUTH

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 12, 20, 12, 0, tzinfo=timezone.utc)


def assess(**kwargs):
    from workeasy360.platform.ecosystem.management.commands.w360_issuer_readiness import (
        assess as run,
    )

    return run(retire=AUTH, days=30, now=NOW, **kwargs)


def on_issuer(registration: dict, issuer: str) -> None:
    from workeasy360.platform.ecosystem.models import ProductIntegration

    ProductIntegration.objects.filter(client_id=registration["client_id"]).update(
        last_token_issuer=issuer
    )


def logs(tmp_path, *events, start_days=31, end_days=0):
    """A log file covering [now - start_days, now - end_days], plus the given (days_ago, event) pairs,
    with the ``docker compose logs`` prefix production adds."""
    lines = [
        {
            "event": "http.request",
            "timestamp": (NOW - timedelta(days=start_days)).isoformat(),
        },
        {
            "event": "http.request",
            "timestamp": (NOW - timedelta(days=end_days, minutes=5)).isoformat(),
        },
    ]
    for days_ago, event in events:
        lines.append({**event, "timestamp": (NOW - timedelta(days=days_ago)).isoformat()})
    path = tmp_path / "api.jsonl"
    path.write_text(
        "".join(f"api-1  | {json.dumps(line)}\n" for line in lines) + "not json\n",
        encoding="utf-8",
    )
    return [str(path)]


@pytest.fixture
def two_products(register_product):
    return register_product("crm"), register_product("books")


def test_the_canonical_name_is_never_ready(stage, two_products):
    stage("b")
    for product in two_products:
        on_issuer(product, ACCOUNTS)
    report = assess(logs=[])
    assert report["verdict"] == "NOT READY"
    assert any("still the canonical issuer" in line for line in report["blocking"])


def test_a_product_still_on_the_name_blocks_and_is_named(stage, two_products, tmp_path):
    stage("c")
    crm, books = two_products
    on_issuer(crm, ACCOUNTS)
    on_issuer(books, AUTH)
    report = assess(logs=logs(tmp_path))
    assert report["verdict"] == "NOT READY"
    assert [line for line in report["blocking"] if line.startswith("books ")]
    assert not [line for line in report["blocking"] if line.startswith("crm ")]


def test_a_product_with_no_recorded_issuer_blocks(stage, two_products, tmp_path):
    stage("c")
    crm, _books = two_products
    on_issuer(crm, ACCOUNTS)  # books never redeemed a code since issuers were recorded
    report = assess(logs=logs(tmp_path))
    assert report["verdict"] == "NOT READY"
    assert any(
        line.startswith("books has redeemed no code") for line in report["blocking"]
    )


def test_without_logs_there_is_not_enough_evidence(stage, two_products):
    stage("c")
    for product in two_products:
        on_issuer(product, ACCOUNTS)
    report = assess(logs=[])
    assert report["verdict"] == "NOT ENOUGH EVIDENCE" and report["exit"] == 2


def test_logs_that_do_not_cover_the_window_are_not_enough(stage, two_products, tmp_path):
    stage("c")
    for product in two_products:
        on_issuer(product, ACCOUNTS)
    assert assess(logs=logs(tmp_path, start_days=10))["verdict"] == "NOT ENOUGH EVIDENCE"
    assert assess(logs=logs(tmp_path, end_days=5))["verdict"] == "NOT ENOUGH EVIDENCE"


def test_a_quiet_full_window_and_every_product_moved_is_ready(
    stage, two_products, tmp_path
):
    stage("c")
    for product in two_products:
        on_issuer(product, ACCOUNTS)
    report = assess(
        logs=logs(
            tmp_path,
            (
                3,
                {
                    "event": "ecosystem.token.issued",
                    "issuer": ACCOUNTS,
                    "product_code": "crm",
                },
            ),
        )
    )
    assert report["verdict"] == "READY" and report["exit"] == 0
    assert report["facts"]["refresh_tokens"].startswith("none issued")


def test_a_token_issued_as_the_name_in_the_window_blocks(stage, two_products, tmp_path):
    stage("c")
    for product in two_products:
        on_issuer(product, ACCOUNTS)
    issued = {"event": "ecosystem.token.issued", "issuer": AUTH, "product_code": "books"}
    assert assess(logs=logs(tmp_path, (12, issued)))["verdict"] == "NOT READY"
    # Before the window it no longer counts.
    assert assess(logs=logs(tmp_path, (40, issued), start_days=45))["verdict"] == "READY"


def test_traffic_served_by_the_old_host_blocks_but_forwarded_browsers_only_warn(
    stage, two_products, tmp_path
):
    stage("c")
    for product in two_products:
        on_issuer(product, ACCOUNTS)
    served = {"event": "accounts.legacy_host.request", "kind": "served"}
    forwarded = {"event": "accounts.legacy_host.request", "kind": "forwarded"}
    blocked = assess(logs=logs(tmp_path, (2, served)))
    assert blocked["verdict"] == "NOT READY"
    warned = assess(logs=logs(tmp_path, (2, forwarded), (4, forwarded)))
    assert warned["verdict"] == "READY"
    assert warned["warnings"] and "2 browser request(s)" in warned["warnings"][0]


def test_the_command_exits_with_the_verdict(stage, two_products, tmp_path, capsys):
    stage("c")
    for product in two_products:
        on_issuer(product, ACCOUNTS)
    with pytest.raises(SystemExit) as stopped:
        call_command("w360_issuer_readiness", "--retire", AUTH)
    assert stopped.value.code == 2
    assert "NOT ENOUGH EVIDENCE" in capsys.readouterr().out


def test_the_old_host_logs_each_request_without_its_path(stage, client, caplog):
    stage("b")
    caplog.set_level("INFO")
    client.get(
        "/.well-known/openid-configuration?resume=secret-token",
        HTTP_HOST="auth.workeasy360.com",
    )
    records = [
        r for r in caplog.records if "accounts.legacy_host.request" in r.getMessage()
    ]
    assert records, "no accounts.legacy_host.request event"
    assert all(
        "secret-token" not in r.getMessage()
        and "openid-configuration" not in r.getMessage()
        for r in records
    )
