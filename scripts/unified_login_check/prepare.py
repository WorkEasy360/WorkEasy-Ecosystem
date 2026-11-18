"""Create, migrate and seed the scratch database for the unified-login check (ADR-066).

    CHECK_PASSWORD=<random> python scripts/unified_login_check/prepare.py   # w360_ulogin_fresh
    python scripts/unified_login_check/prepare.py --drop                    # remove it

The Digital Store check's database recipe (the User Admin seed, then the Store's),
then ``unified_seed.py``: Easy CRM and EasyBook connected for single sign-on with a
throwaway key, both on the owner's second organization with seats, a second person,
and a change-of-email link. Scratch only - the local Docker PostgreSQL on 5442. The
journeys sign people out and change an email, so re-create it before every run, and
never while a backend pytest run is going.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STORE = HERE.parent / "store_domain_check"
sys.path.insert(0, str(STORE))
sys.path.insert(0, str(HERE.parent))

import prepare as store_prepare  # noqa: E402

store_prepare.DB = "w360_ulogin_fresh"


def main() -> int:
    if "--drop" in sys.argv:
        return store_prepare.main()
    rc = store_prepare.main()
    if rc:
        return rc
    return store_prepare._script(HERE / "unified_seed.py")


if __name__ == "__main__":
    raise SystemExit(main())
