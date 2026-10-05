"""Every URL-like field, re-audited against bypass spellings (Phase 12, Step 39).

Server-side fetches were already safe: the egress client resolves and checks
every address at connection time and pins the socket to it. What Phase 12
found was on the *registration* side: a manifest's ``launch_url`` and
``redirect_uris`` are where a browser is sent, and the validator accepted the
legacy IPv4 spellings a browser still honours, fullwidth host names and
NAT64-wrapped loopback (G-11). Also the partner support URL (G-23).
"""

from __future__ import annotations

import pytest

from tests.factories.manifests import manifest_for
from workeasy360.contracts.values.manifest import ManifestError, parse_manifest

#: Every field valid and public, so a refusal is caused by the field under
#: test - never by the factory's development (http loopback) default.
PUBLIC = "https://crm.workeasy360.com"


def _manifest(**overrides):
    return manifest_for("crm", origin=PUBLIC, **overrides)


def test_the_base_manifest_is_valid_so_refusals_below_are_not_vacuous():
    assert parse_manifest(_manifest()).launch_url == f"{PUBLIC}/launch"


LOOPBACK_AND_INTERNAL = [
    "localhost",
    "localhost.",
    "127.0.0.1",
    "127.0.0.1.",
    "127.1",
    "2130706433",
    "0",
    "0177.0.0.1",
    "0x7f.0.0.1",
    "0x7f000001",
    "0300.0250.0.1",  # 192.168.0.1
    "3232235521",  # 192.168.0.1
    "[::1]",
    "[::ffff:127.0.0.1]",
    "[64:ff9b::7f00:1]",  # NAT64 of 127.0.0.1
    "[2002:7f00:1::]",  # 6to4 of 127.0.0.1
    "169.254.169.254",
    "10.0.0.1",
    "100.64.0.1",
    "\uff4c\uff4f\uff43\uff41\uff4c\uff48\uff4f\uff53\uff54",  # fullwidth "localhost"
    "metadata.google.internal",
    "printer.local",
    "1.2.3.999",
    # Found by the final audit: forms a browser still opens as loopback or
    # the metadata address.
    "127.0.0.%31",
    "%6c%6f%63%61%6c%68%6f%73%74",  # percent-encoded "localhost"
    "127。0。0。1",  # ideographic full stops
    "127｡0｡0｡1",  # halfwidth ideographic full stops
    "127.0.0.1%2e",
    "127.0.0.1\\.evil.com",
    "169.254.169.254\\.x.com",
]


@pytest.mark.parametrize("host", LOOPBACK_AND_INTERNAL)
@pytest.mark.parametrize("field", ["launch_url", "redirect_uris", "health_url"])
def test_a_manifest_url_that_reaches_this_machine_or_network_is_refused(host, field):
    url = f"https://{host}/cb"
    value = [url] if field == "redirect_uris" else url
    with pytest.raises(ManifestError):
        parse_manifest(_manifest(**{field: value}))


@pytest.mark.parametrize(
    "host",
    [
        "crm.workeasy360.com",
        "ops.workeasy360.com",
        "8.8.8.8",
        "a1b2.example.com",
        "xn--bcher-kva.example",
    ],
)
def test_public_hosts_still_register(host):
    manifest = parse_manifest(
        _manifest(launch_url=f"https://{host}/", redirect_uris=[f"https://{host}/cb"])
    )
    assert manifest.launch_url == f"https://{host}/"


@pytest.mark.parametrize(
    "url",
    [
        "https://*.workeasy360.com/cb",
        "https://crm.workeasy360.com/cb?next=/x",
        "https://crm.workeasy360.com/cb#frag",
        "https://user:pw@crm.workeasy360.com/cb",
        "http://crm.workeasy360.com/cb",
        "javascript:alert(1)",
        "https://[bad/cb",
    ],
)
def test_redirect_uri_shapes_that_enable_open_redirects_are_refused(url):
    with pytest.raises(ManifestError):
        parse_manifest(_manifest(redirect_uris=[url]))


@pytest.mark.parametrize(
    "url", ["https://[bad/", "https://exa mple.com/", "javascript:alert(1)"]
)
def test_a_malformed_partner_support_url_is_a_validation_error_not_a_500(url):
    """G-23: ``urlsplit``'s ValueError used to escape as a 500."""
    from workeasy360.commercial.partners import branding
    from workeasy360.commercial.partners.branding import PartnerValidationError

    with pytest.raises(PartnerValidationError):
        branding.validate_support_url(url)
