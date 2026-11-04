"""Migration rehearsal and acceptance data for the Store Console (ADR-055).

    CHECK_PASSWORD=... python scripts/store_console_check/prepare.py
    python scripts/store_console_check/prepare.py --drop

1. **The committed code** (``git archive HEAD``, which has ``partners.0012``)
   migrates ``w360_sc_fresh`` and writes the history production would hold
   (``seed_history.py``: the ecosystem history, a live and a revoked
   promotion grant, a product link with a captured visit, an approved and a
   pending affiliate request, a product grant).
2. Every existing row of the tables ADR-055 reads or writes is fingerprinted.
3. **The working tree** applies ``partners.0013`` (the request's kind): the
   fingerprints must be identical, every existing request is ``promote`` with
   no product grant, and the ledger still refuses UPDATE.
4. ``partners.0013`` is reversed and re-applied; the fingerprints again.
5. ``scripts/ecosystem_browser_check/seed.py`` adds the acceptance data (the
   four products on the Store with TEST prices, a 10% programme rule, the
   affiliate Aman with no promotion grant, Super Admin ``staff@eco.test``).

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

DB = "w360_sc_fresh"
HERE = Path(__file__).resolve().parent
eco.DB = DB  # its helpers read the module's database name

FINGERPRINTS = {
    "partners_promotion_grant": "id, partner_id, product_id, status, granted_at, revoked_at",
    "partners_promotion_request": "id, partner_id, product_id, status, channel, website, audience, strategy, notes, requested_at, decided_at, decision_reason, grant_id, revoked_at, revoke_reason",
    "partners_product_grant": "id, partner_id, product_id, plan_id, status, granted_at, revoked_at",
    "partners_account": "id, code, display_name, status, version",
    "partners_capability_grant": "id, partner_id, capability, status, granted_at, revoked_at",
    "commissions_referral_code": "id, partner_id, code, status, product_code, link_kind, campaign_id",
    "commissions_referral": "id, code_id, partner_id, token_hash, status, captured_at, expires_at",
    "commissions_accrual": "id, partner_id, organization_id, kind, amount_minor, currency, source_ref, rate_bp, product_code, payment_ref",
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
    scratch = Path(tempfile.mkdtemp(prefix="w360-sc-head-"))
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
            "promotion grants exist before the upgrade",
            before["partners_promotion_grant"][0] == 2,
        )

        print("== 2. the working tree applies its migration")
        eco.run(new, "migrate", "--noinput")
        plan = eco.run(new, "showmigrations", "--plan")
        check("no migration left unapplied", "[ ]" not in plan)
        check(
            "partners.0013_product_request_kind applied",
            "[X]  partners.0013_product_request_kind" in plan,
        )

        print("== 3. every existing row is unchanged; nothing is invented")
        after = eco.fingerprints()
        for table in FINGERPRINTS:
            check(
                f"{table} identical",
                after[table] == before[table],
                f"{after[table][0]} rows",
            )
        kinds = q(
            DB,
            "SELECT kind, count(*), count(product_grant_id) FROM partners_promotion_request GROUP BY kind",
        )
        check(
            "existing requests are 'promote' with no product grant",
            [tuple(k) for k in kinds] == [("promote", 2, 0)],
            str(kinds),
        )
        check("the ledger is still append-only (UPDATE refused)", eco._update_refused())

        print("== 4. the migration reverses and re-applies without loss")
        eco.run(new, "migrate", "partners", "0012", "--noinput")
        check("reversed: rows intact", eco.fingerprints() == before)
        check(
            "reversed: the kind column is gone",
            q(
                DB,
                "SELECT count(*) FROM information_schema.columns WHERE "
                "table_name = 'partners_promotion_request' AND column_name = 'kind'",
            )[0][0]
            == 0,
        )
        eco.run(new, "migrate", "--noinput")
        check("re-applied: rows intact", eco.fingerprints() == before)

        print("== 5. the acceptance data")
        out = eco.run(new, script=eco.HERE / "seed.py")
        for line in out.splitlines():
            if line.startswith(("SEED ", "PARTNER ")):
                print("   ", line)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print("\nREHEARSAL", "PASS" if not failures else f"FAIL: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
