"""People who have just signed up: an active account and a password, no organization.

    python seed_people.py <password> <email:Full Name> ...

Run inside the scratch platform's process space (``run.py`` passes the harness's own
environment), like ``Accounts/e2e/platform/ops.py``. Prints one JSON line.
"""

import json
import sys

import django

django.setup()

from workeasy360.platform.identity import lifecycle  # noqa: E402
from workeasy360.platform.identity.models import User  # noqa: E402
from workeasy360.platform.tenancy.unscoped import unscoped_access  # noqa: E402

password = sys.argv[1]
out = {}
with unscoped_access(reason="ADR-078 browser check seed", module="scripts.auto_activation_check"):
    for spec in sys.argv[2:]:
        email, _, name = spec.partition(":")
        user = User.objects.filter(email=email).first()
        if user is None:
            user = User.objects.create_user(email=email, password=None)
            lifecycle.complete_with_password(user, password, full_name=name, activate=True)
        out[email.split("@")[0]] = {"email": email, "id": str(user.id), "name": name}
print(json.dumps(out))
