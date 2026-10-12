"""Connect a registered product's single sign-on: the operator's one step.

``register_products`` creates each product's integration as a draft with
placeholder redirect URIs and **no credential**, so the console reports "SSO
integration required". Once a product has implemented its OpenID Connect
client (each product repository's docs/workeasy360-sso.md),
this command finishes the registration through the **same services** the
staff integration API calls, so authorization, validation and audit are
unchanged:

1. replaces the manifest's redirect URIs and scopes (``update_manifest``,
   which audits the before/after redirect URI sets), and - ADR-044 - its
   sign-in mode when asked (a product stored ``local_only`` becomes
   ``sso_only``), its own sign-in page and its post-logout URIs;
2. registers the product's **public** key as a ``private_key_jwt``
   credential (``create_credential``) - the private half never reaches the
   platform;
3. activates the integration if it is still a draft;
4. prints what the product's deployment needs: issuer, client id and the
   credential id to use as the assertion ``kid``.

Like ``register_products``, the actor must be an existing staff identity
already holding the integration grants; the command grants nothing and every
write is audited under the actor's name. Re-running it with a new key adds a
credential without revoking the old one - that overlap is how a key is
rotated; revoke the old credential once the product runs on the new one.
A re-run also REPLACES the redirect URIs and scopes with what it is given
(``--scopes`` defaults to openid email profile organization): pass the
current ones when rotating a key for an integration whose scopes staff have
narrowed. Every change is audited before and after.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from workeasy360.contracts.errors import ManifestRejected, ProductIntegrationError
from workeasy360.contracts.values.manifest import SUPPORTED_SCOPES
from workeasy360.platform.authz.control_plane import acting_scope, platform_context
from workeasy360.platform.ecosystem import discovery
from workeasy360.platform.ecosystem import selectors as ecosystem_selectors
from workeasy360.platform.ecosystem import services as ecosystem
from workeasy360.platform.identity.email import normalize_email
from workeasy360.platform.identity.models import User
from workeasy360.platform.tenancy.unscoped import unscoped_access

DEFAULT_SCOPES = ("openid", "email", "profile", "organization")


def _algorithm(public_key_pem: str) -> str:
    """RS256 for an RSA key, ES256 for a P-256 key - the two the platform
    verifies. Derived from the key so the operator cannot pick a mismatch."""
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    try:
        key = load_pem_public_key(public_key_pem.encode())
    except ValueError:
        raise CommandError("The public key file is not a PEM public key.") from None
    if isinstance(key, rsa.RSAPublicKey):
        return "RS256"
    if isinstance(key, ec.EllipticCurvePublicKey) and key.curve.name == "secp256r1":
        return "ES256"
    raise CommandError("The public key must be RSA or EC P-256.")


class Command(BaseCommand):
    help = "Set a product's redirect URIs and scopes, register its public key, and activate its SSO."

    def add_arguments(self, parser) -> None:  # type: ignore[no-untyped-def]
        parser.add_argument(
            "--actor",
            required=True,
            help="Email of an existing staff identity with the integration grants.",
        )
        parser.add_argument(
            "--product",
            required=True,
            help="The product code, as in infra/ecosystem/products.json.",
        )
        parser.add_argument(
            "--redirect-uri",
            action="append",
            required=True,
            dest="redirect_uris",
            help="Exact callback URL; repeat for several (matched by string equality).",
        )
        parser.add_argument(
            "--public-key-file", required=True, help="The product's PEM public key."
        )
        parser.add_argument(
            "--scopes",
            nargs="+",
            default=list(DEFAULT_SCOPES),
            help=f"Scopes to register (default: {' '.join(DEFAULT_SCOPES)}).",
        )
        parser.add_argument(
            "--label", default="", help="Credential label, e.g. 'production 2026-10'."
        )
        parser.add_argument(
            "--valid-days", type=int, default=ecosystem.DEFAULT_CREDENTIAL_DAYS
        )
        parser.add_argument(
            "--sign-in-mode",
            choices=("sso_only", "sso_optional"),
            default=None,
            help=(
                "ADR-044 sign-in mode. Default: keep the stored mode, except that a "
                "product stored local_only becomes sso_only - connecting its single "
                "sign-on is the point of this command."
            ),
        )
        parser.add_argument(
            "--login-url",
            default=None,
            help="The product's own sign-in page (required with --sign-in-mode sso_optional).",
        )
        parser.add_argument(
            "--post-logout-redirect-uri",
            action="append",
            default=None,
            dest="post_logout_redirect_uris",
            help="Where Workeasy360 may return a browser after RP-initiated sign-out; repeat for several. Replaces the stored list.",
        )

    def handle(self, *args, **options) -> None:  # type: ignore[no-untyped-def]
        unknown = sorted(set(options["scopes"]) - set(SUPPORTED_SCOPES))
        if unknown:
            raise CommandError(f"Unsupported scopes: {', '.join(unknown)}.")
        if "openid" not in options["scopes"]:
            raise CommandError("The scopes must include openid.")
        public_key_pem = Path(options["public_key_file"]).read_text(encoding="utf-8")
        algorithm = _algorithm(public_key_pem)

        address = normalize_email(str(options["actor"]))
        with unscoped_access(reason="Operator names the acting staff", module=__name__):
            actor = User.objects.filter(email=address, is_staff=True).first()
        if actor is None:
            raise CommandError("No staff identity has that address.")

        product = str(options["product"])
        sctx = platform_context(user_id=actor.id, aal=2)
        with acting_scope(sctx):
            integration = ecosystem_selectors.integration_for_product(
                sctx, product_code=product
            )
            if integration is None:
                raise CommandError(
                    f"{product} has no integration; run register_products first."
                )
            manifest = self._manifest(integration.manifest, options)
            try:
                ecosystem.update_manifest(
                    sctx, client_id=integration.client_id, manifest=manifest
                )
            except (ManifestRejected, ProductIntegrationError) as exc:
                # Before the credential: a refused manifest changes nothing.
                raise CommandError(f"The manifest was refused: {exc}") from None
            issued = ecosystem.create_credential(
                sctx,
                client_id=integration.client_id,
                kind="public_key",
                public_key_pem=public_key_pem,
                algorithm=algorithm,
                label=str(options["label"]),
                valid_days=int(options["valid_days"]),
            )
            if integration.status == "draft":
                ecosystem.transition(
                    sctx, client_id=integration.client_id, to_status="active"
                )
                status = "active"
            else:
                status = integration.status
            issuer = discovery.document()["issuer"]

        self.stdout.write(
            f"{product}: redirect URIs and scopes updated, credential registered, integration {status}"
            f" (sign-in mode {manifest.get('sign_in_mode', 'sso_only')})."
        )
        self.stdout.write("Set these in the product's environment:")
        self.stdout.write(f"  WORKEASY360_ISSUER={issuer}")
        self.stdout.write(f"  WORKEASY360_CLIENT_ID={integration.client_id}")
        self.stdout.write(f"  WORKEASY360_KEY_ID={issued.credential_id}")
        self.stdout.write(f"  WORKEASY360_REDIRECT_URI={options['redirect_uris'][0]}")
        self.stdout.write(f"Credential expires {issued.expires_at:%Y-%m-%d}.")

    @staticmethod
    def _manifest(current: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        # Everything else in the stored manifest (launch URL, health URL,
        # contact, capabilities) is kept as staff left it.
        manifest = {
            **current,
            "redirect_uris": list(dict.fromkeys(options["redirect_uris"])),
            "scopes": list(dict.fromkeys(options["scopes"])),
            "token_endpoint_auth_method": "private_key_jwt",
        }
        # A product registered before it had a client (Easy Project and Easy
        # Help Desk until 2026-10-12) is stored local_only, and the
        # authorization endpoint refuses local_only whatever credential it
        # holds - so connecting it without changing the mode would register a
        # key nobody can use.
        mode = options.get("sign_in_mode") or (
            "sso_only" if current.get("sign_in_mode") == "local_only" else None
        )
        if mode:
            manifest["sign_in_mode"] = mode
        if options.get("login_url"):
            manifest["login_url"] = options["login_url"]
        if options.get("post_logout_redirect_uris"):
            manifest["post_logout_redirect_uris"] = list(
                dict.fromkeys(options["post_logout_redirect_uris"])
            )
        return manifest
