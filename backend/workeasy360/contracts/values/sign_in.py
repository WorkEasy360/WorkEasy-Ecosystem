"""Which door a person takes into a product: the one pure rule.

A person chooses (:class:`SignInPreference`, once as a default and optionally
per product); a product declares which doors it has
(:class:`ProductSignInMode`, in its integration manifest) and the registry
knows whether its Workeasy360 sign-in can actually complete. This module
combines the three. Pure and Django-free, so the launcher, the authorization
endpoint and the settings page cannot disagree - they all call :func:`resolve`
(docs/universal-sso/design.md section 2.3).

**What it can never do is grant anything.** It runs only after a person has
been found entitled - membership, organization, entitlement, seat and access
policy are all decided before it is asked - and its only outputs are *how* to
sign in: silently, after asking, or at the product's own page.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from workeasy360.contracts.enums import ProductSignInMode, SignInPreference

#: The preference of a person who never chose one. Automatic: click Open (or
#: type the product's address) and the product opens with the Workeasy360
#: session - the "one login for all products" brief's recommended experience
#: (docs/universal-sso/one-login.md section 1). It only decides *how*: every
#: access check has already passed, and a person can still choose "Ask me each
#: time" or the product's own sign-in.
DEFAULT_PREFERENCE: Final = SignInPreference.AUTO_SSO

#: What a person may store as their default for every product.
DEFAULT_CHOICES: Final[tuple[SignInPreference, ...]] = (
    SignInPreference.AUTO_SSO,
    SignInPreference.ASK,
    SignInPreference.PRODUCT_LOGIN,
)

#: Why the effective sign-in differs from what the person chose.
SSO_UNAVAILABLE: Final = "sso_unavailable"
PRODUCT_REQUIRES_SSO: Final = "product_requires_sso"


@dataclass(frozen=True, slots=True)
class ProductDoors:
    """What a product offers, as the registry knows it - never anything a
    browser or a person supplied."""

    mode: ProductSignInMode
    #: The integration is active, its mode offers Workeasy360 sign-in, and it
    #: holds a credential the token endpoint would accept.
    sso_ready: bool
    #: A product sign-in page Workeasy360 may send a person to exists
    #: (``ProductManifest.product_login_url`` is not empty).
    has_product_login: bool

    @property
    def sso_available(self) -> bool:
        return self.mode.offers_sso and self.sso_ready

    @property
    def product_login_available(self) -> bool:
        return self.mode.offers_product_login and self.has_product_login

    @property
    def sso_state(self) -> str:
        """The honest one-word answer a console or a card shows:
        ``enabled``, ``integration_required`` or ``disabled``."""
        if not self.mode.offers_sso:
            return "disabled"
        return "enabled" if self.sso_ready else "integration_required"

    def override_choices(self) -> tuple[SignInPreference, ...]:
        """The per-product values worth offering. None at all when the product
        cannot be entered through Workeasy360 (there is nothing to choose);
        ``product_login`` only where the product has a page of its own."""
        if not self.sso_available:
            return ()
        choices = [
            SignInPreference.INHERIT,
            SignInPreference.AUTO_SSO,
            SignInPreference.ASK,
        ]
        if self.product_login_available:
            choices.append(SignInPreference.PRODUCT_LOGIN)
        return tuple(choices)


@dataclass(frozen=True, slots=True)
class SignInDecision:
    """The answer for one person and one product."""

    #: What the person asked for: their override, else their default, else
    #: :data:`DEFAULT_PREFERENCE`. Never ``inherit``.
    preference: SignInPreference
    #: What happens: ``auto_sso``, ``ask`` or ``product_login``.
    effective: SignInPreference
    #: Empty when ``effective == preference``; otherwise why not.
    constraint: str = ""

    @property
    def uses_sso(self) -> bool:
        """Whether entry goes through Workeasy360 sign-in at all (silently or
        after the access chooser)."""
        return self.effective is not SignInPreference.PRODUCT_LOGIN

    @property
    def silent(self) -> bool:
        """Whether Workeasy360 may complete a product's sign-in request with
        the existing session and no question."""
        return self.effective is SignInPreference.AUTO_SSO


def stored_preference(
    default: SignInPreference | None, override: SignInPreference | None
) -> SignInPreference:
    """The person's own wish for one product: the override unless it is
    absent or ``inherit``, then the default, then :data:`DEFAULT_PREFERENCE`."""
    if override is not None and override is not SignInPreference.INHERIT:
        return override
    if default is not None and default is not SignInPreference.INHERIT:
        return default
    return DEFAULT_PREFERENCE


def resolve(
    doors: ProductDoors,
    *,
    default: SignInPreference | None,
    override: SignInPreference | None,
) -> SignInDecision:
    """The effective sign-in. The product's constraints always win over the
    person's convenience; the person's wish not to be signed in automatically
    always survives a constraint (design section 2.3)."""
    preference = stored_preference(default, override)
    if not doors.sso_available:
        return SignInDecision(
            preference=preference,
            effective=SignInPreference.PRODUCT_LOGIN,
            constraint=""
            if preference is SignInPreference.PRODUCT_LOGIN
            else SSO_UNAVAILABLE,
        )
    if preference is SignInPreference.PRODUCT_LOGIN and not doors.product_login_available:
        return SignInDecision(
            preference=preference,
            effective=SignInPreference.ASK,
            constraint=PRODUCT_REQUIRES_SSO,
        )
    return SignInDecision(preference=preference, effective=preference)
