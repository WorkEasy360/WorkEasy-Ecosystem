"""The upgrade payloads match the TypeScript interfaces that read them (ADR-049).

TypeScript trusts the interfaces declared in ``partnerPanel.ts`` (the partner's
upgrade page) and ``partnerConsole.ts`` (the Super Admin panel), so a field
renamed on the server would show up as a blank cell, not an error. This checks
them against responses produced by the real stack: a partner created through the
Add Partner command asks, fills and submits through its own endpoints, and staff
decide through theirs.
"""

from __future__ import annotations

import pytest

from tests.partner_upgrade.conftest import (
    admin_path,
    decide,
    fill,
    make_affiliate,
    pp,
    send,
    start,
    submit,
)
from tests.phase10 import ts_contract as ts

pytestmark = pytest.mark.django_db

_PANEL = ts.interfaces(ts.FRONTEND_API / "partnerPanel.ts")
EMAIL = "owner@af-up.example.com"


@pytest.fixture(autouse=True)
def panel_interfaces(monkeypatch):
    """The panel's interfaces beside the console's. A name both files declare keeps
    the console's meaning (``UpgradeProposal`` and ``UpgradeChecklistItem`` are
    declared identically in both)."""
    merged = dict(ts.INTERFACES)
    for name, spec in _PANEL.items():
        merged.setdefault(name, spec)
    monkeypatch.setattr(ts, "INTERFACES", merged)


def test_the_reader_sees_the_upgrade_interfaces():
    for name in (
        "UpgradeState",
        "UpgradeRequest",
        "UpgradeProposal",
        "UpgradeChecklistItem",
        "PartnerUpgrades",
        "UpgradeRequestRow",
        "UpgradeQueue",
        "UpgradeQueueItem",
        "UpgradeBadge",
    ):
        assert name in {**ts.INTERFACES, **_PANEL}, name
    assert "to_experience" in ts.all_fields("UpgradeRequest")
    assert "commercial_reference" in ts.all_fields("UpgradeRequestRow")


def test_the_partner_pages_match_their_types(catalog, admin):
    client, pid = make_affiliate(admin, email=EMAIL)
    empty = client.get(pp(pid, "/upgrade")).json()
    ts.assert_matches(empty, "UpgradeState", "state with nothing open")
    assert empty["eligible"] and empty["open"] is None

    started = start(client, pid, "white_label")
    ts.assert_matches(started.json(), "UpgradeRequest", "started")
    filled = fill(client, pid, started.json(), "white_label")
    ts.assert_matches(filled, "UpgradeRequest", "after the last section")
    state = client.get(pp(pid, "/upgrade")).json()
    ts.assert_matches(state, "UpgradeState", "state with a request open")
    assert state["open"]["proposal"]["brand"] and state["products"] and state["checklist"]
    for item in state["checklist"]:
        ts.assert_matches(item, "UpgradeChecklistItem", item["key"])
    for steps in state["steps"].values():
        assert steps and all(set(s) == {"key", "label"} for s in steps)

    submitted = submit(client, pid, filled)
    ts.assert_matches(submitted.json(), "UpgradeRequest", "submitted")


def test_the_staff_pages_match_their_types(catalog, admin):
    client, pid = make_affiliate(admin, email=EMAIL)
    started = start(client, pid, "reseller")
    filled = fill(client, pid, started.json(), "reseller")
    request = submit(client, pid, filled).json()

    standing = admin.get(admin_path(pid, "/upgrades")).json()
    ts.assert_matches(standing, "PartnerUpgrades", "standing with a request open")
    assert (
        standing["status"] == "pending_approval" and standing["requested"] == "reseller"
    )
    ts.assert_matches(standing["open"], "UpgradeRequestRow", "open request")

    queue = admin.get("/api/v1/admin/partner-upgrades").json()
    ts.assert_matches(queue, "UpgradeQueue", "queue")
    ts.assert_matches(queue["items"][0], "UpgradeQueueItem", "queue item")

    page = admin.get("/api/v1/admin/partners/resellers", {"scope": "all"}).json()
    ts.assert_page(page, "ResellerRow", "list")
    row = next(r for r in page["results"] if r["id"] == str(pid))
    ts.assert_matches(row["upgrade"], "UpgradeBadge", "list badge")
    assert row["upgrade"]["status"] == "pending_approval"
    other = [r for r in page["results"] if r["id"] != str(pid)]
    assert all(r["upgrade"] is None for r in other)
    overview = admin.get("/api/v1/admin/partners/overview").json()
    ts.assert_matches(overview, "PartnersOverview", "overview")
    assert overview["upgrades_pending"] == 1

    asked = decide(admin, pid, request["id"], "request_info", note="Please confirm VAT")
    ts.assert_matches(asked.json(), "UpgradeRequestRow", "sent back")
    resent = submit(client, pid, client.get(pp(pid, "/upgrade")).json()["open"])
    assert resent.status_code == 200, resent.content
    approved = decide(admin, pid, request["id"], "approve", commercial_reference="PO-1")
    assert approved.status_code == 200, approved.content
    ts.assert_matches(approved.json(), "UpgradeRequestRow", "approved")

    after = admin.get(admin_path(pid, "/upgrades")).json()
    ts.assert_matches(after, "PartnerUpgrades", "standing after approval")
    assert after["status"] == "none" and after["history"][0]["status"] == "approved"
    ts.assert_matches(after["history"][0], "UpgradeRequestRow", "history item")
    assert admin.get("/api/v1/admin/partners/overview").json()["upgrades_pending"] == 0
    # A decision is not a form: it answers the same shape whoever reads it again.
    again = send(
        admin,
        admin_path(pid, f"/upgrades/{request['id']}/decision"),
        {"action": "approve"},
    )
    assert again.status_code == 409
