"""Permissions for the product integration control plane.

Every one of these is ``dangerous=True``, and that is the answer to the
carried step-up trigger rather than a precaution.

**Trigger reached: yes.** ``dangerous`` is already implemented and already
enforced in ``platform/authz/services.py::check`` - it demands ``aal >= 2``,
blocks the action outright under break-glass impersonation, and refuses it
when the organization is read-only. The mechanism exists, the actions here
are the highest-value control-plane writes in the platform, so leaving them
at AAL 1 would be leaving a working control switched off.

What each of these can do if it is misused:

- ``integration.register`` / ``integration.update`` - change a registered
  redirect URI, and every subsequent authorization code for that product is
  delivered to the attacker's server. This is a full account-takeover
  primitive against every user of that product, which is why the manifest
  validation and this permission are the two things standing in front of it.
- ``integration.lifecycle`` - activate a draft integration (turning on
  authentication for a product nobody has reviewed) or suspend a live one
  (an ecosystem-wide denial of service for that product's customers).
- ``integration.credential`` - mint a credential that authenticates *as* the
  product, and therefore read any customer's entitlement state.
There is no ``integration.key`` permission. Signing-key rotation has no HTTP
surface at all - it is an operator action on a terminal, because the generate
step produces private key material that must never cross a network. See the
signing-keys note in ``services.py``.

They are additionally gated on ``require_platform_staff`` inside the service,
exactly as catalog administration is (commercial/catalog/services.py). Either
control alone is one misconfiguration away from a customer administrator
being able to register an application against their own platform.
"""

from __future__ import annotations

from typing import Final

from workeasy360.contracts.values.identifiers import PermissionKey
from workeasy360.platform.authz.registry import (
    PermissionDeclaration,
    permission_registry,
)


def _p(key: str, label: str, *, implies: tuple[str, ...] = ()) -> PermissionDeclaration:
    return PermissionDeclaration(
        key=PermissionKey(key),
        label=label,
        category="integrations",
        implies=implies,
        # Uniformly dangerous. There is no read-only entry in this set that
        # would be diluted by it - `integration.view` is separate and is not
        # dangerous, because listing registrations is how an operator finds
        # the one to fix.
        dangerous=True,
    )


ECOSYSTEM_PERMISSIONS: Final = (
    PermissionDeclaration(
        key=PermissionKey("platform.integration.view"),
        label="View product integrations",
        category="integrations",
        # Not dangerous: reading the registry discloses client ids and
        # redirect URIs, which are public by construction - a client id is
        # in every authorization URL and a redirect URI is where the browser
        # visibly goes. Gating a read behind step-up would push operators
        # toward keeping a copy somewhere worse.
        dangerous=False,
    ),
    _p(
        "platform.integration.register",
        "Register a product integration",
        implies=("platform.integration.view",),
    ),
    _p(
        "platform.integration.update",
        "Change a registered manifest, including redirect URIs",
        implies=("platform.integration.view",),
    ),
    _p(
        "platform.integration.lifecycle",
        "Activate, suspend or retire a product integration",
        implies=("platform.integration.view",),
    ),
    _p(
        "platform.integration.credential",
        "Create, rotate or revoke product credentials",
        implies=("platform.integration.view",),
    ),
)

permission_registry.register_all(ECOSYSTEM_PERMISSIONS)
