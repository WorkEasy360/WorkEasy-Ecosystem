"""Give a person a password in the SCRATCH database (what the emailed setup link does).

    python set_password.py admin@north.test

The browser check cannot read an email, so this stands in for the one step the
link performs - ``lifecycle.complete_with_password`` - for a sub-partner's
administrator. Scratch databases only.
"""

import os
import sys

import django

django.setup()

from workeasy360.platform.identity import lifecycle
from workeasy360.platform.identity.models import User
from workeasy360.platform.tenancy.unscoped import unscoped_access

PASSWORD = os.environ["CHECK_PASSWORD"]

with unscoped_access(reason="partner panel browser check", module="set_password"):
    user = User.objects.get(email=sys.argv[1])
    lifecycle.complete_with_password(user, PASSWORD, full_name=user.full_name or "Sub Admin", activate=True)
print("ok")
