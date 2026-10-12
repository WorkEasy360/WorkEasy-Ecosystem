"""Register separately deployed products from an operator's definitions file.

Workeasy360 is platform-only: a product is a SKU (a catalog row) plus an
integration (where its launcher points), never code. This command turns a
JSON definitions file - by default ``infra/ecosystem/products.json`` - into
exactly those two things through the **same services** the Super Admin console
calls, so authorization, validation and audit are the ones the console has.

What it deliberately does **not** do:

- It defines no plan, price or credential. Those are commercial decisions
  made in the console; a product registered here has no price and therefore
  shows "Contact sales" until staff set one.
- A ``trial`` entry (``enabled``, ``duration_days``, optionally
  ``eligible_plan_codes``) is applied through the console's own
  ``configure_product_trial`` **only when the product has no trial
  configuration yet** - a duration the owner decided once, never a value that
  overwrites what staff later chose. A switched-on trial must name an
  eligible plan (``validate_trial_terms``), and this file defines no plan, so
  with none named the duration is stored **switched off** and the command
  says so: staff switch it on in the Product Manager once a plan is published.
- It never overwrites what staff have edited. An existing product or
  integration is left as it is (only a blank category or icon is filled in, so
  a product registered before those fields existed picks them up).
- It never fakes single sign-on. A new integration starts in ``draft``; with
  ``--activate-integrations`` it becomes ``active`` so the launcher opens the
  product's own launch URL, still with **no credential** - the console then
  reports "SSO integration required" until a product's owner supplies one.

The actor must be an existing staff identity that already holds the catalog
and integration grants (``platform.staff_catalog.manage`` and
``platform.staff_integration.manage``); the command grants nothing. Like the
seed scripts, it speaks for that actor at the second-factor level - a person
with a shell on the host can do anything the console can - and every write is
audited under the actor's name.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from workeasy360.commercial.catalog import selectors as catalog_selectors
from workeasy360.commercial.catalog import services as catalog
from workeasy360.contracts.enums import (
    FeatureKind,
    LicensingMode,
    ProductListing,
    ProductStatus,
)
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.contracts.values.manifest import MANIFEST_VERSION
from workeasy360.platform.authz.control_plane import acting_scope, platform_context
from workeasy360.platform.ecosystem import selectors as ecosystem_selectors
from workeasy360.platform.ecosystem import services as ecosystem
from workeasy360.platform.identity.email import normalize_email
from workeasy360.platform.identity.models import User
from workeasy360.platform.tenancy.unscoped import unscoped_access

DEFAULT_FILE = "infra/ecosystem/products.json"

#: The definition keys this command understands. Anything else is refused, so a
#: typo cannot silently drop a setting.
_PRODUCT_KEYS = frozenset(
    {
        "code",
        "slug",
        "name",
        "category",
        "icon",
        "tagline",
        "description",
        "licensing_mode",
        "sort_order",
        "marketing_url",
        "features",
        "integration",
        "trial",
    }
)
_TRIAL_KEYS = frozenset({"enabled", "duration_days", "eligible_plan_codes"})
_INTEGRATION_KEYS = frozenset(
    {
        "display_name",
        "launch_url",
        "redirect_uris",
        "health_url",
        "contact_email",
        "scopes",
        # ADR-044: how the product lets people in, its own sign-in page, and
        # where Workeasy360 may return a browser after a sign-out.
        "sign_in_mode",
        "login_url",
        "post_logout_redirect_uris",
    }
)


def _load(path: Path) -> list[dict[str, Any]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CommandError(f"Cannot read {path}: {exc}") from exc
    products = document.get("products") if isinstance(document, dict) else None
    if not isinstance(products, list) or not products:
        raise CommandError(f"{path} must hold a non-empty 'products' list.")
    for entry in products:
        if not isinstance(entry, dict) or not entry.get("code") or not entry.get("name"):
            raise CommandError("Every product needs at least a code and a name.")
        unknown = sorted(set(entry) - _PRODUCT_KEYS)
        integration = entry.get("integration") or {}
        unknown += [
            f"integration.{k}" for k in sorted(set(integration) - _INTEGRATION_KEYS)
        ]
        unknown += [
            f"trial.{k}" for k in sorted(set(entry.get("trial") or {}) - _TRIAL_KEYS)
        ]
        if unknown:
            raise CommandError(
                f"Unknown keys for {entry['code']!r}: {', '.join(unknown)}."
            )
    return products


def _manifest(entry: dict[str, Any]) -> dict[str, Any]:
    spec = entry["integration"]
    manifest: dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "product_code": entry["code"],
        "display_name": spec.get("display_name") or entry["name"],
        "launch_url": spec["launch_url"],
        "redirect_uris": list(spec["redirect_uris"]),
        "scopes": list(spec.get("scopes") or ["openid"]),
        # A capability is a statement the product makes about itself; none
        # is claimed on its behalf.
        "capabilities": [],
        "token_endpoint_auth_method": "private_key_jwt",
    }
    for optional in (
        "health_url",
        "contact_email",
        "sign_in_mode",
        "login_url",
        "post_logout_redirect_uris",
    ):
        if spec.get(optional):
            manifest[optional] = spec[optional]
    return manifest


class Command(BaseCommand):
    help = "Register products (catalog row + integration) from a definitions file."

    def add_arguments(self, parser) -> None:  # type: ignore[no-untyped-def]
        parser.add_argument(
            "--file",
            default=DEFAULT_FILE,
            help=f"Definitions file (default {DEFAULT_FILE}, relative to the repository).",
        )
        parser.add_argument(
            "--actor",
            required=True,
            help="Email of an existing staff identity holding the catalog and integration grants.",
        )
        parser.add_argument(
            "--activate-integrations",
            action="store_true",
            help="Move newly registered integrations from draft to active.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change; write nothing.",
        )

    def handle(self, *args, **options) -> None:  # type: ignore[no-untyped-def]
        path = Path(options["file"])
        if not path.is_absolute():
            path = Path(settings.ROOT_DIR) / path
        definitions = _load(path)

        address = normalize_email(str(options["actor"]))
        with unscoped_access(reason="Operator names the acting staff", module=__name__):
            actor = User.objects.filter(email=address, is_staff=True).first()
        if actor is None:
            raise CommandError("No staff identity has that address.")

        sctx = platform_context(user_id=actor.id, aal=2)
        dry = bool(options["dry_run"])
        with acting_scope(sctx):
            existing_products = {
                p["code"]: p for p in catalog_selectors.visible_products()
            }
            existing_integrations = {
                i.product_code: i
                for i in ecosystem_selectors.integrations(sctx, limit=500)
            }
            for entry in definitions:
                self._one(
                    sctx,
                    entry,
                    existing_products.get(entry["code"]),
                    existing_integrations.get(entry["code"]),
                    activate=bool(options["activate_integrations"]),
                    dry=dry,
                )

    def _say(self, code: str, message: str) -> None:
        self.stdout.write(f"{code}: {message}")

    def _one(
        self,
        sctx: Any,
        entry: dict[str, Any],
        product: dict[str, Any] | None,
        integration: Any,
        *,
        activate: bool,
        dry: bool,
    ) -> None:
        code = entry["code"]
        try:
            self._product(sctx, entry, product, dry=dry)
            self._integration(sctx, entry, integration, activate=activate, dry=dry)
        except CatalogValidationError as exc:
            raise CommandError(f"{code}: {exc}") from exc

    def _product(
        self,
        sctx: Any,
        entry: dict[str, Any],
        product: dict[str, Any] | None,
        *,
        dry: bool,
    ) -> None:
        code = entry["code"]
        if product is None:
            if dry:
                self._say(code, "would create the product")
                return
            row = catalog.create_product(
                sctx,
                code=code,
                name=entry["name"],
                description=entry.get("description", ""),
                licensing_mode=LicensingMode(entry.get("licensing_mode", "per_seat")),
                sort_order=int(entry.get("sort_order", 100)),
                slug=entry.get("slug"),
                tagline=entry.get("tagline", ""),
                marketing_url=entry.get("marketing_url", ""),
                listing=ProductListing.LISTED,
                category=entry.get("category", ""),
                icon=entry.get("icon", ""),
            )
            catalog.set_product_status(
                sctx, product_id=row.id, status=ProductStatus.AVAILABLE
            )
            product = catalog_selectors.product_row(row)
            self._say(code, "product created and made available")
        else:
            fill = {
                key: entry[key]
                for key in ("category", "icon")
                if entry.get(key) and not product.get(key)
            }
            if fill:
                if not dry:
                    catalog.update_product(sctx, product_id=product["id"], changes=fill)
                self._say(code, f"filled {', '.join(sorted(fill))}")
            else:
                self._say(code, "product exists - left unchanged")
        if product is not None and not dry:
            self._features(sctx, entry, product["id"])
        self._trial(sctx, entry, product, dry=dry)

    def _trial(
        self,
        sctx: Any,
        entry: dict[str, Any],
        product: dict[str, Any] | None,
        *,
        dry: bool,
    ) -> None:
        spec = entry.get("trial")
        code = entry["code"]
        if not spec:
            return
        if product is not None and catalog_selectors.trial_offer(code) is not None:
            self._say(code, "trial already configured - left unchanged")
            return
        if dry or product is None:
            self._say(code, f"would configure a {spec.get('duration_days')}-day trial")
            return
        plans = list(spec.get("eligible_plan_codes") or [])
        enabled = bool(spec.get("enabled")) and bool(plans)
        catalog.configure_product_trial(
            sctx,
            product_id=product["id"],
            enabled=enabled,
            duration_days=spec.get("duration_days"),
            eligible_plan_codes=plans,
        )
        if enabled:
            self._say(code, f"trial configured: {spec.get('duration_days')} days, on")
        elif spec.get("enabled"):
            self._say(
                code,
                f"trial configured: {spec.get('duration_days')} days, OFF until a "
                "published plan is marked eligible (Product Manager)",
            )
        else:
            self._say(code, f"trial configured: {spec.get('duration_days')} days, off")

    def _features(self, sctx: Any, entry: dict[str, Any], product_id: Any) -> None:
        known = {f["key"] for f in catalog_selectors.features_for_product(product_id)}
        for feature in entry.get("features", []):
            if feature["key"] in known:
                continue
            catalog.create_feature(
                sctx,
                product_id=product_id,
                key=feature["key"],
                name=feature["name"],
                kind=FeatureKind.BOOLEAN,
                default_value=False,
                is_public=True,
            )
            self._say(entry["code"], f"feature {feature['key']} added")

    def _integration(
        self,
        sctx: Any,
        entry: dict[str, Any],
        integration: Any,
        *,
        activate: bool,
        dry: bool,
    ) -> None:
        code = entry["code"]
        if not entry.get("integration"):
            self._say(code, "no integration defined")
            return
        if integration is None:
            if dry:
                self._say(code, "would register the integration")
                return
            integration = ecosystem.register_integration(sctx, manifest=_manifest(entry))
            self._say(code, "integration registered (draft, no credential)")
            status = "draft"
        else:
            status = integration.status
            self._say(code, f"integration exists ({status}) - left unchanged")
        if activate and status == "draft" and not dry:
            ecosystem.transition(
                sctx, client_id=integration.client_id, to_status="active"
            )
            self._say(code, "integration activated")
