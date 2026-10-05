"""The SSRF policy, statically (Task 15). No sockets here - see
tests/integration/test_egress_client.py for the send-time guarantees."""

from __future__ import annotations

import pytest

from workeasy360.integration.egress.policy import (
    EgressPolicy,
    EgressRejected,
    check_address,
    parse_target,
)

STRICT = EgressPolicy()
LOCAL = EgressPolicy(allow_http=True, allow_loopback=True)


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.example.com/receive",
        "https://hooks.example.com:8443/receive?token=abc",
        "https://8.8.8.8/hook",
        "https://[2001:4860:4860::8888]/hook",
    ],
)
def test_public_https_destinations_are_accepted(url):
    target = parse_target(url, STRICT)
    assert target.scheme == "https"


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://hooks.example.com/", "scheme_not_allowed"),
        ("file:///etc/passwd", "scheme_not_allowed"),
        ("gopher://hooks.example.com/", "scheme_not_allowed"),
        ("ftp://hooks.example.com/", "scheme_not_allowed"),
        ("data:text/plain,hello", "scheme_not_allowed"),
        ("javascript:alert(1)", "scheme_not_allowed"),
        ("https://user:pass@hooks.example.com/", "credentials_in_url"),
        ("https://good.example.com@evil.example.com/", "credentials_in_url"),
        ("https://localhost/", "host_blocked"),
        ("https://metadata.google.internal/", "host_blocked"),
        ("https://payments.internal/", "host_blocked"),
        ("https://printer.local/", "host_blocked"),
        ("https://intranet/", "host_invalid"),
        ("https://127.0.0.1/", "address_blocked"),
        ("https://10.0.0.5/", "address_blocked"),
        ("https://172.16.3.4/", "address_blocked"),
        ("https://192.168.1.1/", "address_blocked"),
        ("https://169.254.169.254/latest/meta-data/", "address_blocked"),
        ("https://100.100.100.200/", "address_blocked"),
        ("https://0.0.0.0/", "address_blocked"),
        ("https://[::1]/", "address_blocked"),
        ("https://[fd00:ec2::254]/", "address_blocked"),
        ("https://[fe80::1]/", "address_blocked"),
        ("https://[::ffff:10.0.0.1]/", "address_blocked"),
        ("https://[::ffff:169.254.169.254]/", "address_blocked"),
        ("https://[64:ff9b::a9fe:a9fe]/", "address_blocked"),
        ("https://[2002:a00:1::]/", "address_blocked"),
        ("https://224.0.0.1/", "address_blocked"),
        ("https://hooks.example.com:22/", "port_not_allowed"),
        ("https://hooks.example.com:6379/", "port_not_allowed"),
        ("https://hooks.example.com:99999/", "url_invalid"),
        ("https://hooks.example.com/ spaces", "url_invalid"),
        ("https:///no-host", "host_invalid"),
        ("", "url_invalid"),
    ],
)
def test_ssrf_destinations_are_refused_before_any_lookup(url, reason):
    with pytest.raises(EgressRejected) as exc_info:
        parse_target(url, STRICT)
    assert exc_info.value.reason == reason


def test_an_overlong_url_is_refused():
    with pytest.raises(EgressRejected):
        parse_target("https://hooks.example.com/" + "a" * 3000, STRICT)


def test_the_fragment_is_dropped_and_the_query_kept():
    target = parse_target("https://hooks.example.com/a?b=1#secret", STRICT)
    assert target.request_target == "/a?b=1"


def test_display_never_includes_the_path_or_query():
    """A chat-service webhook URL carries its token in the path."""
    target = parse_target("https://hooks.example.com/T0/B0/XXXXSECRET?k=v", STRICT)
    assert target.display == "https://hooks.example.com"
    assert "SECRET" not in target.display


def test_the_local_policy_admits_loopback_but_never_private_or_metadata():
    assert parse_target("http://127.0.0.1:9999/hook", LOCAL).port == 9999
    assert parse_target("http://localhost:9999/hook", LOCAL).host == "localhost"
    for url in ("http://10.0.0.1/", "http://169.254.169.254/", "http://[fd00::1]/"):
        with pytest.raises(EgressRejected):
            parse_target(url, LOCAL)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "169.254.169.254",
        "100.64.0.1",
        "192.0.2.1",  # documentation range
        "198.18.0.1",  # benchmarking range
        "::1",
        "fc00::1",
        "fe80::1%eth0",
        "::ffff:192.168.0.1",
        "2001:0:4136:e378:8000:63bf:3fff:fdd2",  # Teredo
        "ff02::1",
    ],
)
def test_resolved_addresses_outside_public_unicast_are_refused(address):
    with pytest.raises(EgressRejected):
        check_address(address, STRICT)


def test_a_public_address_passes():
    assert str(check_address("93.184.216.34", STRICT)) == "93.184.216.34"
