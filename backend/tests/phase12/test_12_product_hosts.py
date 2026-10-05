"""W360.W010 (Phase 12, G-19): product URLs on IP addresses or wildcard-DNS
names (three of the four real products today) are flagged at deploy time."""

from __future__ import annotations

import pytest

from workeasy360.platform.ecosystem.checks import (
    check_product_hosts_are_owned,
    unstable_host_reason,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://65-1-134-3.sslip.io/",
        "https://3-110-142-102.sslip.io/api/auth/callback/workeasy360",
        "https://10.0.0.1.nip.io/",
        "https://13.200.218.28/",
        "https://[2001:db8::1]/",
    ],
)
def test_unowned_hosts_are_flagged(url):
    assert unstable_host_reason(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://ops.workeasy360.com/",
        "https://crm.workeasy360.com/cb",
        "https://sslip.io.example.com/",
    ],
)
def test_owned_names_are_not(url):
    assert unstable_host_reason(url) is None


@pytest.mark.django_db
def test_the_deploy_check_warns_for_each_product_url(signing_key, register_product):
    from workeasy360.platform.ecosystem.models import ProductIntegration

    registration = register_product("crm")
    integration = ProductIntegration.objects.get(client_id=registration["client_id"])
    manifest = dict(integration.manifest)
    manifest["launch_url"] = "https://65-1-134-3.sslip.io/"
    ProductIntegration.objects.filter(id=integration.id).update(manifest=manifest)
    messages = check_product_hosts_are_owned(None)
    assert any(m.id == "W360.W010" and "sslip.io" in m.msg for m in messages)
