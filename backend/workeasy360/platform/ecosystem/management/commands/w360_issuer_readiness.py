"""Is a previous issuer name safe to stop serving? Evidence, not a timer (ADR-067, stages d and e).

    python manage.py w360_issuer_readiness --retire https://auth.workeasy360.com \\
        --log api-logs.jsonl [--log ...] [--days 30] [--json]

The answer is the exit status: **0** ready, **1** not ready (something still depends on the name),
**2** not enough evidence to say. Nothing is changed; the stage move stays a separate, owner-approved
step (deploy/single-host/make_env.py accounts --stage d|e).

What it weighs:

- **Configuration.** The name must no longer be the canonical issuer (stage c or later).
- **The database (durable).** Every active integration records the issuer it last redeemed an
  authorization code under (``ProductIntegration.last_token_issuer``, ADR-069). One still on the name
  blocks. One with no record blocks too: nothing shows which name it trusts, so the product must sign
  someone in once after the release (or be suspended) before the name can go.
- **The API logs (``w360 logs api``, JSON lines, kept by the operator for the window).**
  ``ecosystem.token.issued`` naming the issuer blocks: a product redeemed a code under it.
  ``accounts.legacy_host.request`` with ``kind=served`` blocks: something read discovery, the key set or
  a server-to-server API through the old host. ``kind=forwarded`` is people's browsers following old
  links; it is reported, because stopping the name breaks those links, but it does not block.
  The logs must cover the whole window, or the answer is "not enough evidence".
- **Refresh tokens.** The token endpoint grants ``authorization_code`` only, so no refresh-token flow
  can depend on the name; nothing to measure.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

UTC = timezone.utc
READY, NOT_READY, NO_EVIDENCE = 0, 1, 2
VERDICTS = {READY: "READY", NOT_READY: "NOT READY", NO_EVIDENCE: "NOT ENOUGH EVIDENCE"}


def _events(paths: list[str]):
    """Each JSON log event, from lines that may carry a ``docker compose logs`` prefix."""
    for name in paths:
        with Path(name).open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                start = line.find("{")
                if start < 0:
                    continue
                try:
                    event = json.loads(line[start:])
                except ValueError:
                    continue
                if isinstance(event, dict) and isinstance(event.get("event"), str):
                    yield event


def _when(event: dict) -> datetime | None:
    raw = event.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def assess(
    *, retire: str, logs: list[str], days: int, now: datetime | None = None
) -> dict[str, Any]:
    from workeasy360.platform.ecosystem import tokens
    from workeasy360.platform.ecosystem.models import (
        IntegrationStatus,
        ProductIntegration,
        ProductSignIn,
    )

    now = now or datetime.now(UTC)
    retire = retire.rstrip("/")
    blocking: list[str] = []
    warnings: list[str] = []
    missing: list[str] = []
    facts: dict[str, Any] = {
        "retire": retire,
        "canonical_issuer": tokens.canonical_issuer(),
        "issuers": list(tokens.issuers()),
        "window_days": days,
        "refresh_tokens": "none issued (authorization_code is the only grant)",
    }

    if retire == tokens.canonical_issuer():
        blocking.append(f"{retire} is still the canonical issuer: move to stage c first.")

    products = []
    for integration in ProductIntegration.objects.filter(
        status=IntegrationStatus.ACTIVE
    ).order_by("product_code"):
        last_seen = (
            ProductSignIn.objects.filter(integration=integration)
            .order_by("-last_seen_at")
            .values_list("last_seen_at", flat=True)
            .first()
        )
        row = {
            "product_code": integration.product_code,
            "last_token_issuer": integration.last_token_issuer,
            "last_product_sign_in": last_seen.isoformat() if last_seen else None,
        }
        products.append(row)
        if integration.last_token_issuer.rstrip("/") == retire:
            blocking.append(
                f"{integration.product_code} last redeemed a code as {retire}: it is still configured "
                "with that name. Move it (its issuer setting and identity-link move) first."
            )
        elif not integration.last_token_issuer:
            blocking.append(
                f"{integration.product_code} has redeemed no code since issuers were recorded, so "
                "nothing shows which name it trusts: have it sign someone in once, or suspend it."
            )
    facts["active_products"] = products

    if not logs:
        missing.append(
            "no API logs given (--log): the database cannot see discovery, key-set or "
            "server-to-server traffic through the old host."
        )
    else:
        since = now - timedelta(days=days)
        earliest = latest = None
        tokens_under: dict[str, int] = {}
        legacy: dict[str, int] = {}
        for event in _events(logs):
            moment = _when(event)
            if moment is None:
                continue
            earliest = moment if earliest is None or moment < earliest else earliest
            latest = moment if latest is None or moment > latest else latest
            if moment < since:
                continue
            if (
                event["event"] == "ecosystem.token.issued"
                and str(event.get("issuer", "")).rstrip("/") == retire
            ):
                code = str(event.get("product_code", "?"))
                tokens_under[code] = tokens_under.get(code, 0) + 1
            elif event["event"] == "accounts.legacy_host.request":
                kind = str(event.get("kind", "?"))
                legacy[kind] = legacy.get(kind, 0) + 1
        facts["logs"] = {
            "earliest": earliest.isoformat() if earliest else None,
            "latest": latest.isoformat() if latest else None,
            "tokens_issued_as_retiring_name": tokens_under,
            "legacy_host_requests": legacy,
        }
        if earliest is None or earliest > since + timedelta(hours=1):
            missing.append(
                f"the logs do not reach back {days} days (earliest event: "
                f"{earliest.isoformat() if earliest else 'none'})."
            )
        if latest is None or latest < now - timedelta(days=1):
            missing.append(
                f"the logs stop before the last day (latest event: "
                f"{latest.isoformat() if latest else 'none'})."
            )
        for code, count in sorted(tokens_under.items()):
            blocking.append(
                f"{count} token(s) issued as {retire} to {code} in the window."
            )
        for kind in sorted(k for k in legacy if k != "forwarded"):
            blocking.append(
                f"{legacy[kind]} request(s) {kind} by the old host in the window "
                "(discovery, keys or APIs read through it)."
            )
        if legacy.get("forwarded"):
            warnings.append(
                f"{legacy['forwarded']} browser request(s) forwarded from the old host in the window: "
                "old links and bookmarks stop working when the name stops resolving."
            )

    verdict = NOT_READY if blocking else NO_EVIDENCE if missing else READY
    return {
        "verdict": VERDICTS[verdict],
        "exit": verdict,
        "blocking": blocking,
        "missing": missing,
        "warnings": warnings,
        "facts": facts,
    }


class Command(BaseCommand):
    help = "Report whether a previous issuer name can stop being served (ADR-067 stages d/e). Changes nothing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--retire",
            required=True,
            help="The issuer name to retire, e.g. https://auth.workeasy360.com",
        )
        parser.add_argument(
            "--log",
            action="append",
            default=[],
            help="API log file (JSON lines); repeatable",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="The quiet window the evidence must cover",
        )
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        if options["days"] < 1:
            raise CommandError("--days must be at least 1")
        report = assess(
            retire=options["retire"], logs=options["log"], days=options["days"]
        )
        if options["json"]:
            self.stdout.write(json.dumps(report, indent=2))
        else:
            self.stdout.write(
                f"{report['verdict']}: retiring {report['facts']['retire']}"
            )
            for title, key in (
                ("Blocking", "blocking"),
                ("Evidence missing", "missing"),
                ("Warnings", "warnings"),
            ):
                for line in report[key]:
                    self.stdout.write(f"  [{title}] {line}")
            for row in report["facts"]["active_products"]:
                self.stdout.write(
                    f"  product {row['product_code']}: last issuer {row['last_token_issuer'] or '-'}, "
                    f"last product sign-in {row['last_product_sign_in'] or '-'}"
                )
        if report["exit"]:
            raise SystemExit(report["exit"])
