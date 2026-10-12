"""Universal, user-controlled product sign-in (ADR-044): the registry half.

What each product **declares** - its manifest's ``sign_in_mode`` and
``login_url``, and whether it holds a credential the token endpoint would
accept - and what each person **chose** (``UserSignInPreference``), combined
through the one pure rule, ``contracts.values.sign_in.resolve``. The launcher,
the authorization endpoint and the settings page all read through here, so
they cannot disagree (docs/universal-sso/design.md).

**Nothing here grants access.** Every caller asks only after membership,
entitlement, seat and access policy have said yes; the answer is *how* the
person signs in, never *whether*. And nothing here names a product: a product
registered tomorrow is read exactly like the four registered today.

Preferences are written on the person's own behalf only - every writer takes
the signed-in user's id from the session, never from a request field - and
each change is recorded in that person's own account trail
(``AccountEvent``), never in an organization's audit log.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.db import transaction

from workeasy360.contracts.enums import SignInPreference
from workeasy360.contracts.errors import SignInPreferenceInvalid
from workeasy360.contracts.values.manifest import ProductManifest, from_stored_manifest
from workeasy360.contracts.values.sign_in import (
    DEFAULT_CHOICES,
    ProductDoors,
    SignInDecision,
    resolve,
)
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

#: The stored key of a person's default (``UserSignInPreference.product_code``).
DEFAULT_KEY = ""


def doors(
    manifest: ProductManifest, *, status: str, has_usable_credential: bool
) -> ProductDoors:
    """What a registered product offers, from registry facts alone."""
    from workeasy360.platform.ecosystem.models import IntegrationStatus

    return ProductDoors(
        mode=manifest.sign_in_mode,
        sso_ready=IntegrationStatus(status).can_authenticate and has_usable_credential,
        has_product_login=bool(manifest.product_login_url),
    )


@dataclass(frozen=True, slots=True)
class ProductSignInDTO:
    """One product's sign-in, for one person: what the product offers, what
    the person chose, and what therefore happens. No client id, no redirect
    URI, no credential detail - the fields a member may see."""

    product_code: str
    display_name: str
    doors: ProductDoors
    #: The person's per-product setting (``inherit`` when there is no row).
    override: SignInPreference
    decision: SignInDecision

    def as_dict(self) -> dict[str, Any]:
        return {
            "product_code": self.product_code,
            "display_name": self.display_name,
            "mode": self.doors.mode.value,
            "sso": self.doors.sso_state,
            "product_login_available": self.doors.product_login_available,
            "override": self.override.value,
            "preference": self.decision.preference.value,
            "effective": self.decision.effective.value,
            "constraint": self.decision.constraint,
            "choices": [c.value for c in self.doors.override_choices()],
        }


def preferences_of(
    user_id: Any,
) -> tuple[SignInPreference | None, dict[str, SignInPreference]]:
    """The person's default (``None`` when never chosen) and per-product
    overrides, in one query."""
    from workeasy360.platform.ecosystem.models import UserSignInPreference

    default: SignInPreference | None = None
    overrides: dict[str, SignInPreference] = {}
    for code, mode in UserSignInPreference.objects.filter(user_id=user_id).values_list(
        "product_code", "mode"
    ):
        if code == DEFAULT_KEY:
            default = SignInPreference(mode)
        else:
            overrides[code] = SignInPreference(mode)
    return default, overrides


def product_sign_in(
    user_id: Any, product_codes: Iterable[str] | None = None
) -> dict[str, ProductSignInDTO]:
    """Every registered product that can be entered - not a draft, not
    retired - (or just ``product_codes``) with this person's effective
    sign-in, in two queries however many products.

    Products the registry has never heard of are simply absent: there is no
    door to describe."""
    from workeasy360.platform.ecosystem.models import (
        IntegrationStatus,
        ProductIntegration,
    )
    from workeasy360.platform.ecosystem.selectors import _with_sso

    # A draft is still being built and a retired one will never return:
    # neither can be entered, so neither has a sign-in to describe.
    rows = ProductIntegration.objects.exclude(
        status__in=[IntegrationStatus.DRAFT.value, IntegrationStatus.RETIRED.value]
    )
    if product_codes is not None:
        codes = sorted({str(c) for c in product_codes})
        if not codes:
            return {}
        rows = rows.filter(product_code__in=codes)
    default, overrides = preferences_of(user_id)
    found: dict[str, ProductSignInDTO] = {}
    for row in _with_sso(rows).values(
        "product_code", "status", "manifest", "has_usable_credential"
    ):
        manifest = from_stored_manifest(row["manifest"])
        offered = doors(
            manifest,
            status=row["status"],
            has_usable_credential=bool(row["has_usable_credential"]),
        )
        override = overrides.get(row["product_code"])
        found[row["product_code"]] = ProductSignInDTO(
            product_code=row["product_code"],
            display_name=manifest.display_name or row["product_code"],
            doors=offered,
            override=override or SignInPreference.INHERIT,
            decision=resolve(offered, default=default, override=override),
        )
    return found


def decision_at_authorize(
    user_id: Any, integration: Any
) -> tuple[SignInDecision, ProductDoors]:
    """The decision for the authorization endpoint, which already holds the
    integration row (and has checked it is ``active``). Two small queries:
    whether a usable credential exists, and the person's two relevant rows."""
    from workeasy360.platform.ecosystem.models import UserSignInPreference
    from workeasy360.platform.ecosystem.selectors import _with_sso

    has_credential = bool(
        _with_sso(integration.__class__.objects.filter(pk=integration.pk))
        .values_list("has_usable_credential", flat=True)
        .first()
    )
    offered = doors(
        integration.manifest_obj,
        status=integration.status,
        has_usable_credential=has_credential,
    )
    default: SignInPreference | None = None
    override: SignInPreference | None = None
    for code, mode in UserSignInPreference.objects.filter(
        user_id=user_id, product_code__in=[DEFAULT_KEY, integration.product_code]
    ).values_list("product_code", "mode"):
        if code == DEFAULT_KEY:
            default = SignInPreference(mode)
        else:
            override = SignInPreference(mode)
    return resolve(offered, default=default, override=override), offered


# --- writes: the person's own, recorded in their own trail ------------------


def _parse(value: Any, *, field: str) -> SignInPreference:
    try:
        return SignInPreference(str(value))
    except ValueError:
        raise SignInPreferenceInvalid(
            "Unknown sign-in preference.", field=field
        ) from None


def set_preferences(
    user_id: Any,
    *,
    default: Any = None,
    products: dict[str, Any] | None = None,
    ip_prefix: str = "",
    device_label: str = "",
) -> None:
    """Change the person's default and/or per-product overrides. Only the
    keys given change. ``inherit`` removes an override.

    Refused, whole, if any value is unknown, a product has no registered
    integration, or an override is one the product does not offer (nothing
    can be chosen for a product that cannot be entered through Workeasy360;
    ``product_login`` only where the product has its own sign-in page).
    Takes effect on the next launch; nothing is signed out.
    """
    from workeasy360.platform.ecosystem.models import UserSignInPreference

    changes: dict[str, SignInPreference] = {}
    if default is not None:
        chosen = _parse(default, field="default")
        if chosen not in DEFAULT_CHOICES:
            raise SignInPreferenceInvalid(
                "Choose automatic, ask, or the product's own sign-in.", field="default"
            )
        changes[DEFAULT_KEY] = chosen

    requested = dict(products or {})
    if requested:
        offered = product_sign_in(user_id, requested.keys())
        for code, raw in requested.items():
            field = f"products.{code}"
            chosen = _parse(raw, field=field)
            product = offered.get(str(code))
            if product is None:
                raise SignInPreferenceInvalid(
                    "That product has no Workeasy360 sign-in to configure.", field=field
                )
            allowed = product.doors.override_choices()
            if chosen is not SignInPreference.INHERIT and chosen not in allowed:
                raise SignInPreferenceInvalid(
                    "That product does not offer this way of signing in.", field=field
                )
            changes[str(code)] = chosen

    if not changes:
        return
    with transaction.atomic():
        current = dict(
            UserSignInPreference.objects.select_for_update()
            .filter(user_id=user_id, product_code__in=list(changes))
            .values_list("product_code", "mode")
        )
        for code, chosen in changes.items():
            before = current.get(code)
            if chosen is SignInPreference.INHERIT:
                if before is None:
                    continue
                UserSignInPreference.objects.filter(
                    user_id=user_id, product_code=code
                ).delete()
            else:
                if before == chosen.value:
                    continue
                UserSignInPreference.objects.update_or_create(
                    user_id=user_id, product_code=code, defaults={"mode": chosen.value}
                )
            _record(
                user_id,
                f"{code or 'default'}={chosen.value}",
                ip_prefix=ip_prefix,
                device_label=device_label,
            )


def remember_choice(
    user_id: Any, product_code: str, choice: SignInPreference, **where: str
) -> None:
    """The access chooser's "Remember my choice for <product>"."""
    set_preferences(user_id, products={product_code: choice.value}, **where)


def reset_preferences(
    user_id: Any, *, ip_prefix: str = "", device_label: str = ""
) -> bool:
    """ "Reset all to default": the default back to *ask*, every override
    gone. Returns whether anything was stored."""
    from workeasy360.platform.ecosystem.models import UserSignInPreference

    deleted, _ = UserSignInPreference.objects.filter(user_id=user_id).delete()
    if deleted:
        _record(user_id, "reset", ip_prefix=ip_prefix, device_label=device_label)
    return bool(deleted)


def _record(user_id: Any, reason: str, *, ip_prefix: str, device_label: str) -> None:
    from workeasy360.platform.identity.account import record_account_event
    from workeasy360.platform.identity.models import AccountEvent

    record_account_event(
        user_id,
        AccountEvent.Kind.SIGN_IN_PREFERENCE_CHANGED,
        reason=reason,
        ip_prefix=ip_prefix,
        device_label=device_label,
    )
    logger.info("ecosystem.sign_in_preference.changed", change=reason.split("=")[-1])
