"""Domain proof against a real DNS server (Phase 7 final audit, item 44).

The rest of the suite swaps the resolver for a fake. This file does not: it
runs a small authoritative DNS server on a local UDP port and points the
**production** ``DnsPythonResolver`` at it, so the TXT parsing, the
treatment of NXDOMAIN / empty answers / SERVFAIL / silence, and the absence
of any caching are exercised over real DNS wire format - without depending on
a registrar or the public internet.
"""

from __future__ import annotations

import socket
import threading

import dns.flags
import dns.message
import dns.rcode
import dns.rdataclass
import dns.rdatatype
import dns.rrset
import pytest

from workeasy360.commercial.partners import branding, domains

pytestmark = pytest.mark.django_db


class LocalDns:
    """A tiny authoritative server: ``zone`` maps a name to a behaviour.

    A behaviour is a list of TXT records (each a list of character-strings,
    so a record split into several strings can be served), or one of
    ``"nxdomain"``, ``"servfail"`` or ``"silent"`` (never answer).
    """

    def __init__(self):
        self.zone: dict[str, object] = {}
        self.queries = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=2)
        self.sock.close()

    def _serve(self):
        while not self._stop.is_set():
            try:
                wire, peer = self.sock.recvfrom(4096)
            except (TimeoutError, OSError):
                continue
            self.queries += 1
            query = dns.message.from_wire(wire)
            question = query.question[0]
            name = question.name.to_text().rstrip(".").lower()
            behaviour = self.zone.get(name, "nxdomain")
            if behaviour == "silent":
                continue
            response = dns.message.make_response(query)
            response.flags |= dns.flags.AA
            if behaviour == "nxdomain":
                response.set_rcode(dns.rcode.NXDOMAIN)
            elif behaviour == "servfail":
                response.set_rcode(dns.rcode.SERVFAIL)
            elif question.rdtype == dns.rdatatype.TXT:
                texts = [" ".join(f'"{part}"' for part in record) for record in behaviour]
                if texts:
                    response.answer.append(
                        dns.rrset.from_text_list(
                            question.name,
                            300,
                            dns.rdataclass.IN,
                            dns.rdatatype.TXT,
                            texts,
                        )
                    )
            self.sock.sendto(response.to_wire(), peer)


@pytest.fixture
def local_dns(settings):
    with LocalDns() as server:
        settings.W360_DNS_RESOLVERS = ("127.0.0.1",)
        settings.W360_DNS_PORT = server.port
        settings.W360_DNS_TIMEOUT_SECONDS = 1
        settings.W360_DNS_LIFETIME_SECONDS = 1
        yield server


def _resolver():
    return domains.DnsPythonResolver()


RECORD = "_w360-verify.app.acme-example.com"


def test_a_single_txt_record_is_read_exactly(local_dns):
    local_dns.zone[RECORD] = [["w360-verify=abc123"]]
    assert _resolver().txt_values(RECORD) == ["w360-verify=abc123"]


def test_every_txt_record_at_the_name_is_returned(local_dns):
    local_dns.zone[RECORD] = [["v=spf1 -all"], ["w360-verify=abc123"], ["other"]]
    assert sorted(_resolver().txt_values(RECORD)) == [
        "other",
        "v=spf1 -all",
        "w360-verify=abc123",
    ]


def test_a_record_split_into_several_strings_is_joined(local_dns):
    """Long TXT values are published as several character-strings; the proof
    must still match (RFC 7208 section 3.3 joining, no separator)."""
    local_dns.zone[RECORD] = [["w360-verify=", "abc", "123"]]
    assert _resolver().txt_values(RECORD) == ["w360-verify=abc123"]


def test_nxdomain_and_an_empty_answer_are_absence_not_failure(local_dns):
    assert _resolver().txt_values(RECORD) == []  # NXDOMAIN
    local_dns.zone[RECORD] = []  # the name exists, no TXT
    assert _resolver().txt_values(RECORD) == []


def test_servfail_and_silence_are_failed_lookups_never_absence(local_dns):
    local_dns.zone[RECORD] = "servfail"
    assert _resolver().txt_values(RECORD) is None
    local_dns.zone[RECORD] = "silent"
    assert _resolver().txt_values(RECORD) is None  # bounded by the 1 s lifetime


def test_nothing_is_cached_between_checks(local_dns):
    """A re-check must see the record as it is *now*: a removed proof is a
    miss at once, not after some TTL."""
    local_dns.zone[RECORD] = [["w360-verify=abc123"]]
    assert _resolver().txt_values(RECORD) == ["w360-verify=abc123"]
    local_dns.zone[RECORD] = []
    assert _resolver().txt_values(RECORD) == []
    assert local_dns.queries >= 2


def test_domain_proof_end_to_end_over_real_dns(partner_factory, local_dns):
    """The whole lifecycle with the production resolver: pending while the
    record is absent, a failed lookup changes nothing, the proof verifies, and
    the scheduled re-check disables the domain once control is lost."""
    fx = partner_factory("branding.manage", "domain.manage")
    actor = fx["actor"]()
    branding.update_brand(actor, brand_name="Acme")
    domain = domains.request_domain(actor, hostname="app.acme-example.com")

    assert (
        domains.verify_domain(actor, domain_id=domain.id).failure_code
        == "record_not_found"
    )
    local_dns.zone[RECORD] = "servfail"
    assert (
        domains.verify_domain(actor, domain_id=domain.id).failure_code == "lookup_failed"
    )

    local_dns.zone[RECORD] = [
        ["unrelated"],
        ["w360-verify=", domain.verification_token.split("=", 1)[1]],
    ]
    assert domains.verify_domain(actor, domain_id=domain.id).status == "verified"
    assert domains.activate_domain(actor, domain_id=domain.id).status == "active"
    assert domains.public_brand_for_host("app.acme-example.com")["brand_name"] == "Acme"

    local_dns.zone[RECORD] = "servfail"  # an outage is never a miss
    assert domains.recheck_proven_domains()["lookup_failed"] == 1
    local_dns.zone[RECORD] = []  # the proof is gone: three misses disable it
    for _ in range(domains.MAX_MISSED_CHECKS):
        domains.recheck_proven_domains()
    domain.refresh_from_db()
    assert domain.status == "disabled" and domain.failure_code == "control_lost"
    assert domains.public_brand_for_host("app.acme-example.com") is None
