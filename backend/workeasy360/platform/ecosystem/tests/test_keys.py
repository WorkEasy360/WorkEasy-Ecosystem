"""Signing keys: publication, selection, rotation and the JWKS.

The property these exist to prove is that **rotation does not require every
product to redeploy, and does not invalidate tokens that have not expired.**
That is the difference between a key model that can be operated and one that
everybody is afraid to touch - and a signing key nobody dares rotate is a
signing key that is still in place the day it leaks.
"""

from __future__ import annotations

import base64
import os

import jwt
import pytest

from workeasy360.contracts.ports.signing import SigningKeyUnavailable
from workeasy360.platform.ecosystem import keys, tokens
from workeasy360.platform.ecosystem.models import SigningKey, SigningKeyStatus

pytestmark = pytest.mark.django_db


def _subject(**overrides):
    import uuid

    payload = {
        "user_id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "product_code": "crm",
        "client_id": "test-client",
        "scope": "openid",
    }
    payload.update(overrides)
    return tokens.TokenSubject(**payload)


# --- the private half never touches the database --------------------------


def test_the_private_key_is_never_stored_in_the_database(signing_key):
    """The single most valuable secret in the ecosystem.

    Asserted against the actual generated PEM, not against a field name: the
    point is not "there is no private_key column", it is that the material
    cannot be recovered from any column.
    """
    private_pem = signing_key.private_pem
    stored = SigningKey.objects.filter(pk=signing_key.pk).values().first()

    assert "PRIVATE KEY" not in str(stored)
    for fragment in private_pem.splitlines()[1:-1]:
        if len(fragment) > 20:
            assert fragment not in str(stored)
    # What *is* stored is a name in a secret manager, not a secret.
    assert stored["private_key_ref"] == signing_key.private_key_ref


def test_the_jwks_publishes_public_material_only(signing_key):
    document = keys.jwks()
    assert "PRIVATE" not in str(document)
    # RSA private parameters. Their absence is the assertion.
    for private_parameter in ("d", "p", "q", "dp", "dq", "qi"):
        assert private_parameter not in document["keys"][0]


def test_the_jwks_entry_carries_kid_alg_and_use(signing_key):
    """A product selects the verifying key by ``kid`` and must be told the
    algorithm by the key set rather than trusting the token's own header."""
    entry = keys.jwks()["keys"][0]
    assert entry["kid"] == signing_key.kid
    assert entry["alg"] == "RS256"
    assert entry["use"] == "sig"
    assert entry["kty"] == "RSA"


def test_an_unresolvable_private_key_fails_loudly(signing_key):
    """A missing secret is an outage, and must look like one.

    Returning an empty string would be worse than raising: a caller that
    received one would sign with it.
    """
    variable = keys.EnvSigningKeyProvider().variable_name(signing_key.private_key_ref)
    os.environ.pop(variable, None)
    with pytest.raises(SigningKeyUnavailable, match=variable):
        keys.private_key_pem(signing_key)


def test_signing_with_no_active_key_raises_rather_than_guessing(db):
    """Falling back to "the newest key" would sign with a retiring key during
    a botched rotation, producing tokens that leave the key set before they
    expire."""
    with pytest.raises(SigningKeyUnavailable, match="No active signing key"):
        keys.active_signing_key()


def test_only_one_key_can_be_active(signing_key, new_signing_key):
    """Two active keys would make "which key signed this?" depend on query
    ordering, and a half-finished rotation indistinguishable from a finished
    one. Enforced by a partial unique index, not by application discipline."""
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        new_signing_key(status=SigningKeyStatus.ACTIVE)


# --- rotation -------------------------------------------------------------


def test_a_token_signed_before_rotation_still_verifies_after(
    signing_key, new_signing_key
):
    """The property that makes rotation operable.

    If this failed, every rotation would log out every user of every product
    at the moment of the switch - so rotation would only ever happen during a
    maintenance window, which means it would never happen.
    """
    before = tokens.issue_id_token(_subject())

    replacement = new_signing_key()
    keys.promote_key(replacement.kid)

    claims = tokens.verify(
        before.value, audience="test-client", expected_type=tokens.TYPE_ID_TOKEN
    )
    assert claims["jti"] == before.jti


def test_the_new_key_signs_after_promotion(signing_key, new_signing_key):
    replacement = new_signing_key()
    keys.promote_key(replacement.kid)

    issued = tokens.issue_id_token(_subject())
    assert jwt.get_unverified_header(issued.value)["kid"] == replacement.kid


def test_a_pending_key_is_published_before_it_signs(signing_key, new_signing_key):
    """Publication precedes use, deliberately.

    Products cache the key set. A key that started signing the moment it was
    created would produce tokens that fail verification at every product whose
    cache had not refreshed - which is the whole outage this ordering avoids.
    """
    pending = new_signing_key()

    published = {k["kid"] for k in keys.jwks()["keys"]}
    assert pending.kid in published

    issued = tokens.issue_id_token(_subject())
    assert jwt.get_unverified_header(issued.value)["kid"] == signing_key.kid


def test_a_retiring_key_still_verifies(signing_key, new_signing_key):
    replacement = new_signing_key()
    keys.promote_key(replacement.kid)

    signing_key.refresh_from_db()
    assert signing_key.status == SigningKeyStatus.RETIRING.value
    assert signing_key.kid in {k["kid"] for k in keys.jwks()["keys"]}


def test_a_retired_key_no_longer_verifies_anything(signing_key, new_signing_key):
    """Retirement is the moment tokens signed by that key stop working. It is
    terminal and it is why the procedure waits out the token lifetime first."""
    issued = tokens.issue_id_token(_subject())
    replacement = new_signing_key()
    keys.promote_key(replacement.kid)
    keys.retire_key(signing_key.kid)

    assert signing_key.kid not in {k["kid"] for k in keys.jwks()["keys"]}
    with pytest.raises(jwt.InvalidTokenError, match="unknown or retired"):
        tokens.verify(
            issued.value, audience="test-client", expected_type=tokens.TYPE_ID_TOKEN
        )


def test_the_active_key_cannot_be_retired(signing_key):
    """Doing so would leave the platform with no way to sign. The caller
    almost certainly meant to promote a replacement first."""
    with pytest.raises(SigningKeyUnavailable, match="active signing key"):
        keys.retire_key(signing_key.kid)


def test_a_retired_key_cannot_be_promoted_back(signing_key, new_signing_key):
    replacement = new_signing_key()
    keys.promote_key(replacement.kid)
    keys.retire_key(signing_key.kid)

    with pytest.raises(SigningKeyUnavailable, match="retired"):
        keys.promote_key(signing_key.kid)


def test_promotion_refuses_a_key_whose_secret_is_missing(signing_key, new_signing_key):
    """Resolvability is verified *before* the switch, never after.

    Promoting a key whose secret has not reached the secret manager yet would
    stop token issuance dead - an outage minutes after an operation that
    appeared to succeed.
    """
    orphan = new_signing_key()
    variable = keys.EnvSigningKeyProvider().variable_name(orphan.private_key_ref)
    os.environ.pop(variable, None)

    with pytest.raises(SigningKeyUnavailable):
        keys.promote_key(orphan.kid)

    # And the incumbent is untouched: a failed promotion is not a half-done one.
    signing_key.refresh_from_db()
    assert signing_key.status == SigningKeyStatus.ACTIVE.value


def test_unknown_kid_is_rejected_rather_than_fetched(signing_key):
    """An unknown ``kid`` is ordinary untrusted input - a forged token, or one
    from another issuer. It must be a rejection, not a key lookup anywhere the
    token points."""
    issued = tokens.issue_id_token(_subject())
    header, payload, signature = issued.value.split(".")

    import base64
    import json

    forged_header = (
        base64.urlsafe_b64encode(
            json.dumps({"alg": "RS256", "kid": "does-not-exist", "typ": "JWT"}).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    with pytest.raises(jwt.InvalidTokenError, match="unknown or retired"):
        tokens.verify(
            f"{forged_header}.{payload}.{signature}",
            audience="test-client",
            expected_type=tokens.TYPE_ID_TOKEN,
        )


def test_safe_retirement_window_covers_the_longest_token(settings, signing_key):
    """The wait before retiring is derived, not guessed: the longest-lived
    thing the key could have signed, plus the accepted clock skew."""
    settings.OIDC_ID_TOKEN_TTL_S = 300
    settings.OIDC_ACCESS_TOKEN_TTL_S = 900
    settings.OIDC_CLOCK_SKEW_S = 30
    assert keys.safe_retirement_after() == 930


# --- the environment-backed provider --------------------------------------


def test_the_provider_reads_the_environment_live(signing_key):
    """An operator adding a key to the secret manager must not need a restart
    for a rotation to proceed."""
    provider = keys.EnvSigningKeyProvider()
    variable = provider.variable_name("later")
    os.environ.pop(variable, None)

    with pytest.raises(SigningKeyUnavailable):
        provider.private_key_pem("later")

    os.environ[variable] = "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----"
    try:
        assert "BEGIN PRIVATE KEY" in provider.private_key_pem("later")
    finally:
        os.environ.pop(variable, None)


def test_escaped_newlines_in_a_secret_are_accepted():
    """Most secret-manager CLIs deliver a PEM with literal backslash-n.

    Accepting both spellings removes a class of rotation failure that only
    appears in the deployed environment and looks like a corrupt key.
    """
    provider = keys.EnvSigningKeyProvider()
    variable = provider.variable_name("escaped")
    os.environ[variable] = (
        "-----BEGIN PRIVATE KEY-----\\nbody\\n-----END PRIVATE KEY-----"
    )
    try:
        assert "\n" in provider.private_key_pem("escaped")
        assert "\\n" not in provider.private_key_pem("escaped")
    finally:
        os.environ.pop(variable, None)


def test_the_provider_never_logs_key_material(signing_key, caplog):
    variable = keys.EnvSigningKeyProvider().variable_name(signing_key.private_key_ref)
    private_pem = os.environ[variable]
    os.environ.pop(variable, None)

    with caplog.at_level("ERROR"), pytest.raises(SigningKeyUnavailable):
        keys.private_key_pem(signing_key)

    # The variable name is logged, because that is what an operator needs.
    # The value never is - and it is not present to be logged.
    assert variable in caplog.text
    assert "PRIVATE KEY" not in caplog.text
    os.environ[variable] = private_pem


# --- algorithms -----------------------------------------------------------


@pytest.mark.parametrize("algorithm", ["RS256", "ES256"])
def test_both_supported_algorithms_sign_and_verify(db, new_signing_key, algorithm):
    """RS256 is the default because OpenID Connect Core requires every
    implementation to support it. ES256 is offered for smaller keys and
    tokens; the code path is identical, which is what this asserts."""
    key = new_signing_key(status=SigningKeyStatus.ACTIVE, algorithm=algorithm)
    issued = tokens.issue_id_token(_subject())

    assert jwt.get_unverified_header(issued.value)["alg"] == algorithm
    claims = tokens.verify(
        issued.value, audience="test-client", expected_type=tokens.TYPE_ID_TOKEN
    )
    assert claims["jti"] == issued.jti
    assert key.kid == jwt.get_unverified_header(issued.value)["kid"]


def test_an_unsupported_algorithm_cannot_be_generated():
    for rejected in ("HS256", "none", "RS1"):
        with pytest.raises(ValueError, match="Unsupported signing algorithm"):
            keys.generate_key_pair(rejected)


def test_kids_are_unpredictable():
    """A sequential or date-derived kid tells an observer how many rotations
    have happened and roughly when the next is due."""
    generated = {keys.new_kid() for _ in range(50)}
    assert len(generated) == 50
    assert all(not k.isdigit() for k in generated)


# --- the registries fail closed -------------------------------------------


def test_signing_fails_closed_when_no_provider_is_registered():
    """A missing provider must raise, never fall back.

    A default provider would be a second place a signing key could come from,
    and the whole point of the indirection in ``contracts/ports/signing.py``
    is that there is exactly one.
    """
    from workeasy360.contracts.ports import signing

    original = signing.get_signing_key_provider()
    signing.reset_signing_key_provider()
    try:
        with pytest.raises(signing.SigningKeyUnavailable, match="No SigningKeyProvider"):
            signing.get_signing_key_provider()
    finally:
        signing.register_signing_key_provider(original)


def test_registration_fails_closed_when_no_product_directory_is_registered():
    """Same rule for the catalog port.

    Quietly accepting any product code would turn a typo into an integration
    that can never be entitled and that looks correct in every listing.
    """
    from workeasy360.contracts.ports import catalog

    original = catalog.get_product_directory()
    catalog.reset_product_directory()
    try:
        with pytest.raises(RuntimeError, match="No ProductDirectory"):
            catalog.get_product_directory()
    finally:
        catalog.register_product_directory(original)


# --- rotation is recorded, where a platform event belongs ------------------


def test_key_rotation_is_recorded_in_the_security_log(db, signing_key, caplog):
    """Rotation has no ``AuditEvent``, and that is the decision, not a gap.

    ``AuditEvent`` is tenant-scoped and customer-readable; a platform signing
    key belongs to no customer, and writing the rotation against whichever
    organization an operator happened to be in would put a platform event in
    one tenant's trail and no other's. It goes to the security-event stream
    instead (11-observability.md section 9), which is what this asserts - so
    "key rotation is audited" is a checked claim rather than a sentence in a
    document. See the signing-keys note in ``ecosystem/services.py``.
    """
    # The real rotation sequence, through the functions an operator's command
    # calls - not the fixture, which inserts a row directly and would skip the
    # creation path this is asserting.
    reference = "rotationlog"
    variable = keys.EnvSigningKeyProvider().variable_name(reference)
    try:
        with caplog.at_level("INFO"):
            replacement, private_pem = keys.create_pending_key(
                algorithm="RS256", private_key_ref=reference
            )
            os.environ[variable] = private_pem
            keys.promote_key(replacement.kid)
            keys.retire_key(signing_key.kid)
    finally:
        os.environ.pop(variable, None)

    text = caplog.text
    for event in (
        "ecosystem.signing_key.created",
        "ecosystem.signing_key.promoted",
        "ecosystem.signing_key.retired",
    ):
        assert event in text, event

    # The kid is recorded, because "which key?" is the first question in an
    # incident. The key material is not, and is not present to be recorded.
    assert replacement.kid in text
    assert signing_key.kid in text
    assert "PRIVATE KEY" not in text


def test_expired_authorization_codes_are_pruned(
    client, signing_key, register_product, entitled_org, signed_in
):
    """The housekeeping task, and the retention window it deliberately keeps.

    A code is worthless once it expires, but the row is retained for an hour
    so that a replay attempt still *finds* it and is logged as a replay rather
    than as an unknown code. Without the task the table grows for the life of
    the deployment.
    """
    import hashlib
    import secrets
    from urllib.parse import parse_qs, urlsplit

    from django.utils import timezone

    from workeasy360.platform.ecosystem.models import AuthorizationCode
    from workeasy360.platform.ecosystem.tasks import prune_authorization_codes

    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    granted = client.get(
        "/api/v1/integrations/oauth/authorize",
        {
            "response_type": "code",
            "client_id": registration["client_id"],
            "redirect_uri": registration["redirect_uri"],
            "scope": "openid",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "organization": org["organization_id"],
        },
    )
    assert parse_qs(urlsplit(granted["Location"]).query)["code"]
    assert AuthorizationCode.objects.count() == 1

    # Recently expired: kept, so a replay is still recognisable as one.
    AuthorizationCode.objects.update(expires_at=timezone.now())
    assert prune_authorization_codes() == 0
    assert AuthorizationCode.objects.count() == 1

    # Past the retention window: removed.
    AuthorizationCode.objects.update(
        expires_at=timezone.now() - timezone.timedelta(hours=2)
    )
    assert prune_authorization_codes() == 1
    assert AuthorizationCode.objects.count() == 0
