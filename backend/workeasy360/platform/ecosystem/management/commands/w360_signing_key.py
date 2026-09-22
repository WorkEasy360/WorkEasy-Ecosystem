"""Operator entry point for signing-key rotation.

A management command rather than an API, deliberately. The generate step
produces private key material that has to reach the secret manager and must
never be returned by an HTTP response, stored, or written to the audit log.
Printing it once, on a terminal, to the person performing the rotation is the
narrowest delivery path available.

The full procedure, including the incident variant, is in
docs/phase-3/08-key-management.md. In normal operation:

    manage.py w360_signing_key generate --ref k2       # publish, do not sign
    ... place the printed PEM in the secret manager as W360_OIDC_SIGNING_KEY_K2
    ... wait for products' JWKS caches to refresh
    manage.py w360_signing_key promote --kid <kid>     # start signing
    ... wait out the longest token lifetime
    manage.py w360_signing_key retire --kid <old-kid>  # stop verifying

``--force`` on retire is what makes the compromise procedure possible: it
skips the wait, invalidating every token the compromised key signed. That is
the correct response to a leaked key and the wrong one to a routine rotation,
so it is a separate flag rather than a shorter timer.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Generate, promote or retire a platform token-signing key."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("action", choices=("generate", "promote", "retire", "list"))
        parser.add_argument(
            "--ref",
            default="",
            help="Secret-manager reference for a generated key, e.g. k2.",
        )
        parser.add_argument("--kid", default="", help="Key id to promote or retire.")
        parser.add_argument(
            "--algorithm",
            default="",
            help="RS256 or ES256. Defaults to W360_OIDC_SIGNING_ALGORITHM.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Retire a key before its safe retirement time. Invalidates "
                "every unexpired token it signed. For key compromise only."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        action = options["action"]
        handler = {
            "generate": self._generate,
            "promote": self._promote,
            "retire": self._retire,
            "list": self._list,
        }[action]
        handler(options)

    # --- actions ----------------------------------------------------------

    def _generate(self, options: dict[str, Any]) -> None:
        from django.conf import settings

        from workeasy360.platform.ecosystem import keys

        reference = options["ref"]
        if not reference:
            raise CommandError(
                "--ref is required: it names where the private half will live "
                "in the secret manager."
            )

        algorithm = options["algorithm"] or str(settings.OIDC_SIGNING_ALGORITHM)
        key, private_pem = keys.create_pending_key(
            algorithm=algorithm, private_key_ref=reference
        )
        variable = keys.EnvSigningKeyProvider().variable_name(reference)

        self.stdout.write(self.style.SUCCESS(f"Created pending key {key.kid}"))
        self.stdout.write(f"  algorithm        {key.algorithm}")
        self.stdout.write(f"  private_key_ref  {reference}")
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                f"Store the following under {variable} in the secret manager. "
                f"It is shown once and is not stored anywhere by this platform."
            )
        )
        self.stdout.write("")
        self.stdout.write(private_pem)
        self.stdout.write(
            "The key is published in the JWKS but is not signing. Allow "
            "products' key-set caches to refresh, then promote it."
        )

    def _promote(self, options: dict[str, Any]) -> None:
        from workeasy360.platform.ecosystem import keys

        kid = self._require_kid(options)
        key = keys.promote_key(kid)
        self.stdout.write(self.style.SUCCESS(f"{key.kid} is now the active signing key."))
        self.stdout.write(
            f"The previous key is retiring. Wait at least "
            f"{keys.safe_retirement_after()}s before retiring it, so that "
            f"every token it signed has expired."
        )

    def _retire(self, options: dict[str, Any]) -> None:
        from workeasy360.platform.ecosystem import keys
        from workeasy360.platform.ecosystem.models import SigningKey

        kid = self._require_kid(options)
        row = SigningKey.objects.filter(kid=kid).first()
        if row is None:
            raise CommandError(f"No signing key with kid {kid!r}.")

        if not options["force"] and row.retiring_at is not None:
            elapsed = (timezone.now() - row.retiring_at).total_seconds()
            required = keys.safe_retirement_after()
            if elapsed < required:
                raise CommandError(
                    f"{kid} stopped signing {int(elapsed)}s ago; tokens it "
                    f"signed remain valid for another {int(required - elapsed)}s. "
                    f"Retiring now would reject them at every product. Wait, "
                    f"or pass --force if this key is compromised."
                )

        keys.retire_key(kid)
        self.stdout.write(self.style.SUCCESS(f"{kid} retired and removed from the JWKS."))
        if options["force"]:
            self.stdout.write(
                self.style.WARNING(
                    "Forced. Every unexpired token signed by this key will now "
                    "fail verification; affected users must sign in again."
                )
            )

    def _list(self, options: dict[str, Any]) -> None:
        from workeasy360.platform.ecosystem.models import SigningKey

        rows = SigningKey.objects.all()
        if not rows:
            self.stdout.write("No signing keys. Token issuance is not possible.")
            return
        for row in rows:
            self.stdout.write(
                f"{row.kid:<24} {row.status:<10} {row.algorithm:<8} "
                f"ref={row.private_key_ref}"
            )

    @staticmethod
    def _require_kid(options: dict[str, Any]) -> str:
        kid = options["kid"]
        if not kid:
            raise CommandError("--kid is required.")
        return str(kid)
