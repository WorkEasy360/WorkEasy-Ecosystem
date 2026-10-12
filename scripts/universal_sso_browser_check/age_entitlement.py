"""The browser check's controlled clock for the products' entitlement re-check: age each scratch
product's cached Workeasy360 answer for one organization past its window, as if the 15 minutes had
passed, so the next request asks Workeasy360 again (EasyBook sso/entitlement.py, Easy CRM
apps/accounts/workeasy360/entitlement.py). The answer itself is left alone: what Workeasy360 says next
is what counts.

    USSO_WORK=<dir> python scripts/universal_sso_browser_check/age_entitlement.py <workeasy360-organization-id>

Scratch only: each product's own Django, with prepare.py's scratch environment.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prepare import CRM, EASYBOOK, crm_env, easybook_env

AGE = """
from django.core.cache import cache
key = "workeasy360:entitlement:{organization}"
record = cache.get(key)
if record:
    record["checked_at"] -= 3600
    cache.set(key, record, 7 * 24 * 3600)
print("aged" if record else "no answer cached")
"""


def main(organization: str) -> int:
    organization = str(uuid.UUID(organization))
    for name, root, env in (
        ("Easy CRM", CRM, crm_env()),
        ("EasyBook", EASYBOOK, easybook_env()),
    ):
        result = subprocess.run(  # noqa: S603 - the product's own interpreter
            [
                str(root / ".venv" / "Scripts" / "python.exe"),
                "manage.py",
                "shell",
                "-c",
                AGE.format(organization=organization),
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode:
            print(name, "failed:", result.stderr[-1500:])
            return result.returncode
        print(
            name, result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
