"""Where the private signing key comes from - never the database.

The platform signs every identity token products verify. That private key is
the single most valuable secret in the ecosystem: whoever holds it can mint a
token for any user, in any organization, for any product. It therefore
follows the rule the rest of the system already applies to MFA secrets
(``identity.AuthFactor.secret_ref``): **the database stores a reference, the
secret lives in the secret manager** (12-security.md section 5).

``SigningKey`` rows hold the public half, the ``kid``, the algorithm and the
lifecycle. They hold ``private_key_ref`` - a name, not a key. Resolving that
name to key material is this port's entire job, and it is a port so that the
resolution strategy is deployment configuration rather than a code change:
environment today, KMS or Vault later, with no caller touched.

A consequence worth stating: **key rotation requires no deployment.** An
operator adds the new secret to the secret manager and runs the rotation
command; the new row points at the new reference. Nothing is rebuilt
(docs/phase-3/08-key-management.md section 5).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class SigningKeyUnavailable(RuntimeError):
    """The private key behind a reference could not be resolved.

    A distinct type because the correct response is specific: token issuance
    must fail with a 503 rather than a 400, since nothing the caller sent is
    wrong, and retrying after the operator restores the secret will work.
    Collapsing it into a generic error produces an outage that looks like a
    client bug (docs/phase-3/14-failure-mode-review.md).
    """


@runtime_checkable
class SigningKeyProvider(Protocol):
    """Resolves a stored reference to PEM-encoded private key material."""

    def private_key_pem(self, reference: str) -> str:
        """The PEM for ``reference``.

        Raises :class:`SigningKeyUnavailable` when the reference cannot be
        resolved. Never returns an empty string or a placeholder: a caller
        that received one would sign with it.
        """
        ...


# --- runtime registry -------------------------------------------------------

_provider: SigningKeyProvider | None = None


def register_signing_key_provider(provider: SigningKeyProvider) -> None:
    """Install the provider. Called once, from ``platform.ecosystem``'s
    ``AppConfig.ready()``, which selects it from settings."""
    global _provider
    _provider = provider


def get_signing_key_provider() -> SigningKeyProvider:
    """The registered provider, or raise.

    Never falls back to a default. A fallback provider is a second place a
    signing key can come from, and the whole point of this indirection is
    that there is exactly one.
    """
    if _provider is None:
        raise SigningKeyUnavailable(
            "No SigningKeyProvider is registered. platform.ecosystem must be "
            "installed and its AppConfig.ready() must have run before any "
            "token can be signed."
        )
    return _provider


def reset_signing_key_provider() -> None:
    """Test hook only."""
    global _provider
    _provider = None
