"""WorkEasy360 Accounts (ADR-067): the central sign-in moves from
``auth.workeasy360.com`` to ``accounts.workeasy360.com``.

Each stage is configured exactly as ``deploy/single-host/make_env.py accounts``
writes it (test_configuration.py proves the two agree): the portal origins,
the sign-in's previous name, the canonical issuer and the issuer honoured on
its own host during the move.

Two kinds of HTTP client, because the move is about which host answers:

- :class:`Browser` keeps one cookie jar per host, as a browser does, and
  follows the platform's redirects across hosts until one leaves the platform
  (the product's callback).
- :class:`Internet` is a product's server: it sends each absolute URL to that
  URL's host, with no cookies. The reference product client
  (tests/factories/reference_product_client.py) is given it, with its
  ``_path`` shim lifted so discovered URLs keep their host.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import pytest
from django.test import Client

ADMIN = "https://admin.workeasy360.com"
PARTNER = "https://partner.workeasy360.com"
APP = "https://app.workeasy360.com"
API = "https://api.workeasy360.com"
ACCOUNTS = "https://accounts.workeasy360.com"
#: The central sign-in's previous name.
AUTH = "https://auth.workeasy360.com"


def host(origin: str) -> str:
    return origin.removeprefix("https://")


#: stage -> (current sign-in origin, previous name kept, canonical issuer,
#: transition issuer). The same table as make_env.py's ACCOUNTS_STAGES, in
#: origins rather than names.
STAGES: dict[str, tuple[str, str, str, str]] = {
    "off": (AUTH, "", AUTH, ""),
    "a": (ACCOUNTS, AUTH, AUTH, ""),
    "b": (ACCOUNTS, AUTH, AUTH, ACCOUNTS),
    "c": (ACCOUNTS, AUTH, ACCOUNTS, AUTH),
    "d": (ACCOUNTS, AUTH, ACCOUNTS, ""),
    "e": (ACCOUNTS, "", ACCOUNTS, ""),
}


def configure(settings, stage: str):
    current, previous, issuer, transition = STAGES[stage]
    origins = {
        "admin": ADMIN,
        "partner": PARTNER,
        "app": APP,
        "auth": current,
        "api": API,
        "store": "",
    }
    settings.PORTALS_ENABLED = True
    settings.W360_PORTAL_ORIGINS = origins
    settings.W360_AUTH_LEGACY_ORIGIN = previous
    settings.OIDC_ISSUER = issuer
    settings.OIDC_TRANSITION_ISSUERS = [transition] if transition else []
    settings.ALLOWED_HOSTS = [host(o) for o in origins.values() if o] + (
        [host(previous)] if previous else []
    )
    settings.CSRF_TRUSTED_ORIGINS = [ADMIN, PARTNER, APP, current]
    settings.W360_FRONTEND_URL = APP
    return settings


@pytest.fixture
def stage(settings):
    """``stage("b")`` puts the platform in that stage of the move."""

    def put(name: str):
        return configure(settings, name)

    return put


def _split(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return parts.netloc, path


class Internet:
    """A product's server talking to the platform: each absolute URL goes to
    its own host over https, with no cookie jar of a browser's."""

    def __init__(self) -> None:
        self.client = Client()
        self.hosts: list[str] = []

    def get(self, url: str, data: Any = None, **extra: Any):
        netloc, path = _split(url)
        self.hosts.append(netloc)
        return self.client.get(path, data, HTTP_HOST=netloc, secure=True, **extra)

    def post(self, url: str, data: Any = None, **extra: Any):
        netloc, path = _split(url)
        self.hosts.append(netloc)
        return self.client.post(path, data, HTTP_HOST=netloc, secure=True, **extra)


@dataclass
class Browser:
    """A browser: one cookie jar per host, and the trail of every address it
    was sent to."""

    jars: dict[str, Client] = field(default_factory=dict)
    trail: list[str] = field(default_factory=list)

    def at(self, netloc: str) -> Client:
        return self.jars.setdefault(netloc, Client())

    def sign_in(self, origin: str, user: Any) -> None:
        """A session on ``origin``'s host only, as signing in there leaves.

        Automatic sign-in for products, so a signed-in person enters a product
        without the access chooser (the flows here are about hosts)."""
        from workeasy360.platform.ecosystem.models import UserSignInPreference

        UserSignInPreference.objects.update_or_create(
            user=user, product_code="", defaults={"mode": "auto_sso"}
        )
        self.at(host(origin)).force_login(user)

    def get(self, url: str, **extra: Any):
        netloc, path = _split(url)
        self.trail.append(url)
        return self.at(netloc).get(path, HTTP_HOST=netloc, secure=True, **extra)

    def follow(self, url: str, *, platform_hosts: set[str], limit: int = 8):
        """Navigate to ``url`` and follow redirects while they stay on the
        platform; returns the last response (the one leaving it, or a page)."""
        response = self.get(url)
        for _ in range(limit):
            if response.status_code not in (301, 302, 303, 307, 308):
                return response
            location = response["Location"]
            netloc = urlsplit(location).netloc
            if netloc not in platform_hosts:
                return response
            response = self.get(location)
        raise AssertionError(f"redirect loop: {self.trail}")


def platform_hosts() -> set[str]:
    return {host(o) for o in (ADMIN, PARTNER, APP, API, ACCOUNTS, AUTH)}


@pytest.fixture
def internet() -> Internet:
    return Internet()


@pytest.fixture
def browser() -> Browser:
    return Browser()


@pytest.fixture
def product(monkeypatch, internet):
    """A product configured with ``issuer``, reaching the platform over
    :class:`Internet` - every endpoint from discovery, at its discovered host."""
    from tests.factories import reference_product_client as rpc

    monkeypatch.setattr(rpc, "_path", lambda absolute_url: absolute_url)

    def make(registration: dict, *, issuer: str) -> rpc.ReferenceProductClient:
        return rpc.ReferenceProductClient(
            http=internet,
            issuer=issuer,
            client_id=registration["client_id"],
            redirect_uri=registration["redirect_uri"],
            private_key_pem=registration["private_key_pem"],
            credential_kid=registration["credential_id"],
            client_secret=registration["client_secret"],
        )

    return make


def sign_in_to_product(rp, browser: Browser, organization_id: str) -> dict[str, Any]:
    """The whole authorization code flow, as a product and a browser run it:
    PKCE, state and nonce generated by the product; the browser sent to the
    discovered authorization endpoint and followed across the platform's hosts;
    state checked, the code redeemed with the verifier, the ID token verified
    against the product's configured issuer and its nonce."""
    from urllib.parse import urlencode

    begun = rp.begin_authorization(organization_id=organization_id)
    start = f"{begun['url']}?{urlencode(begun['query'])}"
    landed = browser.follow(start, platform_hosts=platform_hosts())
    assert landed.status_code == 302, (landed.status_code, browser.trail)
    code = rp.handle_callback(landed["Location"], expected_state=begun["state"])
    issued = rp.exchange_code(code=code, code_verifier=begun["code_verifier"])
    claims = rp.verify_id_token(issued["id_token"], expected_nonce=begun["nonce"])
    return {
        "claims": claims,
        "tokens": issued,
        "start": start,
        "trail": list(browser.trail),
    }


def challenge_of(verifier: str) -> str:
    """RFC 7636 S256: BASE64URL(SHA256(verifier)), unpadded."""
    import base64
    import hashlib

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
