"""The manifest validator - the first thing an external product touches.

Every rejection below is a real attack or a real operational failure, not a
schema preference. The redirect URI cases in particular are the ones that
have produced published account-takeover advisories against other providers,
so each is named for what it would allow.
"""

from __future__ import annotations

import pytest

from workeasy360.contracts.values.manifest import (
    MANIFEST_VERSION,
    MAX_REDIRECT_URIS,
    ManifestError,
    from_stored_manifest,
    parse_manifest,
)

pytestmark = pytest.mark.security


def _manifest(**overrides) -> dict:
    payload = {
        "manifest_version": MANIFEST_VERSION,
        "product_code": "crm",
        "display_name": "CRM",
        "launch_url": "https://crm.example.com/launch",
        "redirect_uris": ["https://crm.example.com/auth/callback"],
        "scopes": ["openid"],
        "capabilities": ["offline_token_verification"],
        "token_endpoint_auth_method": "private_key_jwt",
        "health_url": "https://crm.example.com/healthz",
        "contact_email": "crm@example.com",
    }
    payload.update(overrides)
    return payload


# --- the happy path -------------------------------------------------------


def test_a_complete_manifest_round_trips():
    """``as_dict`` must produce something ``from_stored_manifest`` accepts.

    Not a tautology: the registry stores ``as_dict()`` and rebuilds with
    ``from_stored_manifest`` on every authorization request, so a field that
    serialises under one name and loads under another would be a redirect URI
    list that silently reads as empty.
    """
    parsed = parse_manifest(_manifest())
    rebuilt = from_stored_manifest(parsed.as_dict())
    assert rebuilt == parsed


def test_scopes_default_to_openid_only():
    """The default is the minimum. A product gets no personal data unless it
    registered for it."""
    parsed = parse_manifest(_manifest(scopes=None))
    assert parsed.scopes == ("openid",)


def test_scope_order_is_normalised():
    """Two manifests requesting the same scopes compare equal regardless of
    the order or duplication the product sent."""
    a = parse_manifest(_manifest(scopes=["email", "openid", "profile"]))
    b = parse_manifest(_manifest(scopes=["openid", "profile", "email", "openid"]))
    assert a.scopes == b.scopes == ("openid", "profile", "email")


# --- redirect URI security ------------------------------------------------


@pytest.mark.parametrize(
    "uri,why",
    [
        (
            "https://crm.example.com/auth/*",
            "a wildcard could never match exact comparison, so accepting it "
            "would register a callback that silently never works - or, under "
            "a future looser comparison, one that matches anything",
        ),
        (
            "javascript:alert(1)",
            "a javascript: redirect executes in the platform's own origin",
        ),
        (
            "data:text/html;base64,PHNjcmlwdD4=",
            "a data: URI renders attacker HTML with the code in the URL",
        ),
        (
            "file:///etc/passwd",
            "a non-http scheme has no business being a browser redirect",
        ),
        (
            "//evil.example.com/callback",
            "a protocol-relative URL has no scheme and no host of its own",
        ),
        (
            "https://good.example.com@evil.example.com/cb",
            "reads as good.example.com to a human, resolves to evil",
        ),
        (
            "https://crm.example.com/cb?next=x",
            "a pre-existing query makes appending code and state ambiguous",
        ),
        (
            "https://crm.example.com/cb#frag",
            "RFC 6749 section 3.1.2 forbids a fragment",
        ),
        (
            "http://crm.example.com/cb",
            "plaintext delivery of an authorization code is a disclosed "
            "credential; only loopback is exempt",
        ),
    ],
)
def test_unsafe_redirect_uris_are_refused(uri, why):
    with pytest.raises(ManifestError) as exc:
        parse_manifest(_manifest(redirect_uris=[uri]))
    assert exc.value.field == "redirect_uris", why


def test_loopback_http_is_permitted_only_when_allowed():
    """OAuth 2.1 permits http on loopback for native and development clients.

    Both directions are asserted: permitted when the deployment allows it,
    refused when it does not. Production sets ``allow_insecure_urls=False``
    and ``test_production_refuses_insecure_configuration`` pins that.
    """
    loopback = _manifest(
        launch_url="http://127.0.0.1:9000/launch",
        redirect_uris=["http://127.0.0.1:9000/cb"],
        health_url="",
    )
    parsed = parse_manifest(loopback, allow_insecure_urls=True)
    assert parsed.redirect_uris == ("http://127.0.0.1:9000/cb",)

    with pytest.raises(ManifestError):
        parse_manifest(loopback, allow_insecure_urls=False)


def test_http_on_a_non_loopback_host_is_refused_even_when_insecure_is_allowed():
    """The loopback exemption is for loopback, not for http generally.

    Without this, a development flag would permit a plaintext callback to any
    host on the internet - which is the exemption becoming the hole.
    """
    with pytest.raises(ManifestError, match="loopback"):
        parse_manifest(
            _manifest(redirect_uris=["http://crm.example.com/cb"]),
            allow_insecure_urls=True,
        )


def test_duplicate_redirect_uris_are_refused():
    with pytest.raises(ManifestError, match="Duplicate"):
        parse_manifest(
            _manifest(
                redirect_uris=[
                    "https://crm.example.com/cb",
                    "https://crm.example.com/cb",
                ]
            )
        )


def test_redirect_uri_count_is_bounded():
    """Registration is authenticated, but an authenticated caller submitting
    a thousand redirect URIs is still an outage."""
    many = [f"https://crm.example.com/cb{i}" for i in range(MAX_REDIRECT_URIS + 1)]
    with pytest.raises(ManifestError, match="at most"):
        parse_manifest(_manifest(redirect_uris=many))


def test_exact_matching_is_the_only_comparison():
    """Not a prefix test, not a normalising one, not a host test.

    Each candidate below is accepted by some real-world implementation that
    got this wrong, and each is a full account-takeover primitive.
    """
    parsed = parse_manifest(_manifest(redirect_uris=["https://crm.example.com/cb"]))

    assert parsed.permits_redirect("https://crm.example.com/cb")
    for hostile in (
        "https://crm.example.com/cb/../../evil",
        "https://crm.example.com/cb/extra",
        "https://crm.example.com/cb?x=1",
        "https://crm.example.com.evil.net/cb",
        "https://crm.example.com:443/cb",
        "HTTPS://CRM.EXAMPLE.COM/cb",
        "https://crm.example.com/cb#",
    ):
        assert not parsed.permits_redirect(hostile), hostile


# --- strictness -----------------------------------------------------------


def test_unknown_fields_are_rejected_not_ignored():
    """A typo in a security-relevant field name must fail at registration.

    ``redirect_uri`` (singular) silently ignored would leave the manifest with
    no callbacks at all, and the product would discover it when its first user
    could not log in.
    """
    with pytest.raises(ManifestError, match="Unknown manifest field"):
        parse_manifest(_manifest(redirect_uri="https://crm.example.com/cb"))


@pytest.mark.parametrize(
    "field", ["manifest_version", "product_code", "launch_url", "redirect_uris"]
)
def test_required_fields_are_required(field):
    payload = _manifest()
    payload.pop(field)
    with pytest.raises(ManifestError) as exc:
        parse_manifest(payload)
    assert exc.value.field == field


def test_an_unsupported_manifest_version_is_refused_not_guessed():
    """Best-effort parsing of an unrecognised shape is how a security field
    gets silently dropped."""
    with pytest.raises(ManifestError, match="Unsupported manifest_version"):
        parse_manifest(_manifest(manifest_version="2099-01-01"))


def test_an_invalid_product_code_is_refused():
    with pytest.raises(ManifestError) as exc:
        parse_manifest(_manifest(product_code="CRM!"))
    assert exc.value.field == "product_code"


def test_an_unregistered_scope_is_refused():
    with pytest.raises(ManifestError, match="Unknown scopes"):
        parse_manifest(_manifest(scopes=["openid", "admin"]))


def test_openid_scope_is_mandatory():
    with pytest.raises(ManifestError, match="openid"):
        parse_manifest(_manifest(scopes=["profile"]))


def test_an_unknown_capability_is_refused():
    with pytest.raises(ManifestError, match="Unknown capabilities"):
        parse_manifest(_manifest(capabilities=["do_anything"]))


def test_an_unsupported_auth_method_is_refused():
    with pytest.raises(ManifestError, match="token_endpoint_auth_method"):
        parse_manifest(_manifest(token_endpoint_auth_method="none"))


def test_a_manifest_must_be_an_object():
    with pytest.raises(ManifestError, match="JSON object"):
        parse_manifest(["not", "an", "object"])


def test_a_scalar_where_a_list_belongs_is_refused():
    """A bare string is iterable, so a lenient parser would read
    ``"https://x/cb"`` as eleven single-character URIs."""
    with pytest.raises(ManifestError, match="must be a list"):
        parse_manifest(_manifest(redirect_uris="https://crm.example.com/cb"))


def test_a_malformed_contact_email_is_refused():
    with pytest.raises(ManifestError) as exc:
        parse_manifest(_manifest(contact_email="not-an-email"))
    assert exc.value.field == "contact_email"


def test_an_over_long_url_is_refused():
    with pytest.raises(ManifestError, match="exceeds"):
        parse_manifest(_manifest(redirect_uris=["https://crm.example.com/" + "a" * 600]))


def test_health_url_is_optional_but_validated_when_present():
    assert parse_manifest(_manifest(health_url="")).health_url == ""
    with pytest.raises(ManifestError) as exc:
        parse_manifest(_manifest(health_url="javascript:alert(1)"))
    assert exc.value.field == "health_url"
