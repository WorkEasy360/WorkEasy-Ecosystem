"""Migration rehearsal for the full-stack Store upgrade (ADR-056).

    python scripts/store_upgrade_check/prepare.py
    python scripts/store_upgrade_check/prepare.py --drop

1. **The committed code** (``git archive HEAD``) migrates ``w360_su_fresh`` and
   writes the history production would hold (``store_console_check``'s
   ``seed_history.py``: products, published plans and prices, partners, grants,
   a sale, a commission rule and a ledger row).
2. Every existing row of the tables ADR-056 touches is fingerprinted.
3. **The working tree** applies ``catalog.0007``, ``pricing.0004``,
   ``commissions.0007`` and ``partners.0014``: fingerprints identical, the new
   columns at their defaults (no discount, no fee, no badges, every plan and
   every rule as before), and the triggers now refuse a change to a published
   price's discount and a rule's ``recurring``.
4. The four migrations are reversed and re-applied; the fingerprints again.
5. ``scripts/ecosystem_browser_check/seed.py`` adds the acceptance data for
   ``browser_check.js`` (four products with INR prices, margins, an affiliate,
   a reseller, the white-label partner ``wl@eco.test``, customers, Super Admin
   ``staff@eco.test``).

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

DB = "w360_su_fresh"
HISTORY = Path(__file__).resolve().parents[1] / "store_console_check" / "seed_history.py"
eco.DB = DB

FINGERPRINTS = {
    "pricing_plan_price": "id, price_book_id, plan_version_id, interval, model, amount_minor, valid_from, valid_to, published_at",
    "pricing_price_book": "id, code, channel, partner_id, currency, priority",
    "catalog_product": "id, code, name, slug, status, listing, category, sort_order",
    "catalog_plan": "id, code, name, status, is_public, sort_order",
    "commissions_rule": "id, code, version, partner_id, product_code, rate_bp, recurring, effective_from, retired_at",
    "commissions_margin_rule": "id, code, version, experience, partner_id, product_code, discount_bp",
    "commissions_accrual": "id, partner_id, organization_id, kind, amount_minor, currency, source_ref, rate_bp",
    "partners_account": "id, code, display_name, status, version",
    "partners_product_grant": "id, partner_id, product_id, plan_id, status",
}

UPGRADE = (
    ("catalog", "0006", "0007_store_merchandising"),
    ("pricing", "0003", "0004_price_discount_setup_fee"),
    ("commissions", "0006", "0007_rule_plan_fixed_duration"),
    ("partners", "0013", "0014_selling_price_range"),
)


def _refused(sql: str) -> bool:
    try:
        q(DB, sql)
    except Exception:
        return True
    return False


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
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    eco.FINGERPRINTS = FINGERPRINTS
    drop_database(DB)
    create_database(DB)
    create_vector_extension(DB)
    new = eco.ROOT / "backend"
    scratch = Path(tempfile.mkdtemp(prefix="w360-su-head-"))
    try:
        print("== 1. the committed code builds today's data")
        old = eco.committed_backend(scratch)
        eco.run(old, "migrate", "--noinput")
        eco.run(old, "bootstrap_superadmin")
        eco.run(old, "register_products", "--actor", eco.STAFF, "--activate-integrations")
        eco.run(old, script=HISTORY)
        before = eco.fingerprints()
        for table, (count, _digest) in before.items():
            print(f"    {table}: {count} rows")
        check("published prices exist before the upgrade", before["pricing_plan_price"][0] > 0)
        check("commission rules exist before the upgrade", before["commissions_rule"][0] > 0)

        print("== 2. the working tree applies its migrations")
        eco.run(new, "migrate", "--noinput")
        plan = eco.run(new, "showmigrations", "--plan")
        check("no migration left unapplied", "[ ]" not in plan)
        for app, _prev, name in UPGRADE:
            check(f"{app}.{name} applied", f"[X]  {app}.{name}" in plan)

        print("== 3. every existing row is unchanged; new columns at their defaults")
        after = eco.fingerprints()
        for table in FINGERPRINTS:
            check(f"{table} identical", after[table] == before[table], f"{after[table][0]} rows")
        prices = q(DB, "SELECT count(*) FILTER (WHERE discount_bp <> 0 OR setup_fee_minor IS NOT NULL) FROM pricing_plan_price")[0][0]
        check("no price gained a discount or set-up fee", prices == 0)
        badges = q(DB, "SELECT count(*) FILTER (WHERE featured OR popular OR badge_new OR tags <> '[]'::jsonb) FROM catalog_product")[0][0]
        check("no product gained a badge or tag", badges == 0)
        rules = q(DB, "SELECT count(*) FILTER (WHERE plan_code <> '' OR fixed_amount_minor IS NOT NULL OR recurring_months IS NOT NULL) FROM commissions_rule")[0][0]
        check("every rule is still a whole-product rate rule", rules == 0)
        check("no product has a recommended plan yet", q(DB, "SELECT count(*) FROM catalog_product WHERE recommended_plan_id IS NOT NULL")[0][0] == 0)
        check("no price range exists", q(DB, "SELECT count(*) FROM partners_selling_price_range")[0][0] == 0)
        check(
            "a published price's discount is frozen (UPDATE refused)",
            _refused("UPDATE pricing_plan_price SET discount_bp = 100 WHERE published_at IS NOT NULL"),
        )
        check(
            "a published price's amount is still frozen",
            _refused("UPDATE pricing_plan_price SET amount_minor = amount_minor + 1 WHERE published_at IS NOT NULL"),
        )
        check(
            "a rule's recurring flag is frozen (UPDATE refused)",
            _refused("UPDATE commissions_rule SET recurring = NOT recurring"),
        )
        check("the ledger is still append-only (UPDATE refused)", eco._update_refused())

        print("== 4. the migrations reverse and re-apply without loss")
        for app, prev, _name in reversed(UPGRADE):
            eco.run(new, "migrate", app, prev, "--noinput")
        check("reversed: rows intact", eco.fingerprints() == before)
        check(
            "reversed: the discount column is gone",
            q(DB, "SELECT count(*) FROM information_schema.columns WHERE table_name = 'pricing_plan_price' AND column_name = 'discount_bp'")[0][0] == 0,
        )
        check(
            "reversed: the old price trigger still freezes the amount",
            _refused("UPDATE pricing_plan_price SET amount_minor = amount_minor + 1 WHERE published_at IS NOT NULL"),
        )
        eco.run(new, "migrate", "--noinput")
        check("re-applied: rows intact", eco.fingerprints() == before)

        print("== 5. the acceptance data")
        out = eco.run(new, script=eco.HERE / "seed.py")
        for line in out.splitlines():
            if line.startswith(("SEED ", "PARTNER ", "ORG ")):
                print("   ", line)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print("\nREHEARSAL", "PASS" if not failures else f"FAIL: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
