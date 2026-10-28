"""Migration rehearsal for product-aware affiliate links (ADR-054).

    CHECK_PASSWORD=... python scripts/affiliate_products_check/prepare.py
    python scripts/affiliate_products_check/prepare.py --drop

1. **The committed code** (``git archive HEAD``) migrates ``w360_aff_fresh``
   and writes the history production would hold (``seed_history.py``: the
   ecosystem history plus an ADR-053 product link with a captured visit).
2. Every existing row of the tables these migrations touch is fingerprinted.
3. **The working tree** applies ``catalog.0006``, ``partners.0011`` and
   ``commissions.0006``: the fingerprints must be identical, the new columns
   hold their defaults, the product link became a ``product`` link, its
   partner got the promotion grant for that product (and nothing else), and
   the ledger still refuses UPDATE.
4. The three migrations are reversed and re-applied; the fingerprints again.

Scratch only (the local Docker PostgreSQL on 5442): never ``workeasy360`` or
the test database.
"""

# Fixed commands and constant SQL only - nothing here comes from input.
# ruff: noqa: S603, S607, S608

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ecosystem_browser_check"))

import prepare as eco  # the ADR-053 rehearsal's helpers
from rehearse_phase8_upgrade import (
    create_database,
    create_vector_extension,
    drop_database,
    q,
)

DB = "w360_aff_fresh"
HERE = Path(__file__).resolve().parent
eco.DB = DB  # its helpers read the module's database name

#: The pre-ADR-054 columns of every table these migrations touch.
FINGERPRINTS = {
    "commissions_accrual": "id, partner_id, organization_id, kind, amount_minor, currency, source_ref, event_at, rule_id, rule_version, rate_bp, basis_minor, product_code, plan_code, earning, idempotency_key",
    "commissions_referral_code": "id, partner_id, code, label, status, product_code, plan_code, campaign_id, created_at",
    "commissions_referral": "id, code_id, partner_id, token_hash, status, captured_at, expires_at, visitor_hash, registered_at, converted_at, organization_id",
    "commissions_rule": "id, code, version, partner_id, product_code, rate_bp, recurring, effective_from, retired_at",
    "partners_account": "id, code, display_name, status, payout_currency, default_experience, version",
    "partners_capability_grant": "id, partner_id, capability, status, granted_at, revoked_at",
    "partners_product_grant": "id, partner_id, product_id, plan_id, granted_at, revoked_at",
    "catalog_product": "code, name, slug, status, listing, affiliate_enabled",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--drop", action="store_true")
    args = parser.parse_args()
    if args.drop:
        drop_database(DB)
        print("dropped", DB)
        return 0

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(
            f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else "")
        )
        if not ok:
            failures.append(name)

    eco.FINGERPRINTS = FINGERPRINTS
    drop_database(DB)
    create_database(DB)
    create_vector_extension(DB)
    new = eco.ROOT / "backend"
    scratch = Path(tempfile.mkdtemp(prefix="w360-aff-head-"))
    try:
        print("== 1. the committed code builds today's data")
        old = eco.committed_backend(scratch)
        eco.run(old, "migrate", "--noinput")
        eco.run(old, "bootstrap_superadmin")
        eco.run(old, "register_products", "--actor", eco.STAFF, "--activate-integrations")
        eco.run(old, script=HERE / "seed_history.py")
        before = eco.fingerprints()
        for table, (count, _digest) in before.items():
            print(f"    {table}: {count} rows")
        check(
            "a product link exists before the upgrade",
            before["commissions_referral_code"][0] >= 2,
        )

        print("== 2. the working tree applies its migrations")
        eco.run(new, "migrate", "--noinput")
        plan = eco.run(new, "showmigrations", "--plan")
        check("no migration left unapplied", "[ ]" not in plan)
        for name in (
            "catalog.0006_product_updates",
            "partners.0011_promotion_grants",
            "commissions.0006_affiliate_products",
        ):
            check(f"{name} applied", f"[X]  {name}" in plan)

        print("== 3. every existing row is unchanged, and the data step did its one job")
        after = eco.fingerprints()
        for table in FINGERPRINTS:
            check(
                f"{table} identical",
                after[table] == before[table],
                f"{after[table][0]} rows",
            )
        _data_step(check)
        check("the ledger is still append-only (UPDATE refused)", eco._update_refused())

        print("== 4. the migrations reverse and re-apply without loss")
        for app, target in (
            ("commissions", "0005"),
            ("partners", "0010"),
            ("catalog", "0005"),
        ):
            eco.run(new, "migrate", app, target, "--noinput")
        check("reversed: rows intact", eco.fingerprints() == before)
        eco.run(new, "migrate", "--noinput")
        check("re-applied: rows intact", eco.fingerprints() == before)
        _data_step(check)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print("\nREHEARSAL", "PASS" if not failures else f"FAIL: {failures}")
    return 1 if failures else 0


def _data_step(check) -> None:
    kinds = dict(q(DB, "SELECT code, link_kind FROM commissions_referral_code"))
    check(
        "the product link is a product link",
        kinds.get("HISTCRM1") == "product",
        str(kinds),
    )
    check(
        "every link without a product stays general",
        all(kind == "general" for code, kind in kinds.items() if code != "HISTCRM1"),
    )
    grants = q(
        DB,
        "SELECT a.code, p.code, g.status FROM partners_promotion_grant g "
        "JOIN partners_account a ON a.id = g.partner_id JOIN catalog_product p ON p.id = g.product_id",
    )
    check(
        "only the partner with a product link got that product's promotion grant",
        [tuple(g) for g in grants] == [("hist-affiliate", "easy_crm", "active")],
        str(grants),
    )
    defaults = q(
        DB,
        "SELECT count(*) FILTER (WHERE payment_ref <> ''), "
        "(SELECT count(*) FROM commissions_referral WHERE trial_started_at IS NOT NULL), "
        "(SELECT count(*) FROM commissions_referral_code WHERE update_id IS NOT NULL) "
        "FROM commissions_accrual",
    )[0]
    check("new columns hold their defaults", tuple(defaults) == (0, 0, 0), str(defaults))


if __name__ == "__main__":
    raise SystemExit(main())
