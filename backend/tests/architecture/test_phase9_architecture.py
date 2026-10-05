"""Phase 9 fitness rules: the customer journey.

Each rule is one sentence of the Phase 9 decision record made executable
(ADR-035, ADR-036), so it cannot be weakened by a refactor nobody reviews.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.architecture]

PACKAGE = Path(__file__).resolve().parents[2] / "workeasy360"
IDENTITY = PACKAGE / "platform" / "identity"

#: Queryset and manager methods that write (``select_for_update`` is the
#: prelude to a write: a module that locks a ``User`` is about to change it).
_WRITES = {
    "bulk_create",
    "bulk_update",
    "create",
    "create_superuser",
    "create_user",
    "delete",
    "get_or_create",
    "select_for_update",
    "update",
    "update_or_create",
}
#: The identity tables a flow in another module is most tempted to write.
_IDENTITY_MODELS = {"User", "UserSession", "AccountToken"}


def _sources(root: Path):
    for path in sorted(root.rglob("*.py")):
        if "migrations" in path.parts or "tests" in path.parts:
            continue
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def _root_and_chain(node: ast.AST) -> tuple[str | None, list[str]]:
    """``User.objects.filter(x).delete`` -> ``("User", ["objects", "filter",
    "delete"])``."""
    chain: list[str] = []
    while True:
        if isinstance(node, ast.Attribute):
            chain.append(node.attr)
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.Name):
            return node.id, list(reversed(chain))
        else:
            return None, list(reversed(chain))


def test_only_identity_writes_the_user_table():
    """08-data-ownership.md: ``User`` is written by ``platform/identity`` only.

    Signup, invitations, the staff user actions, the operator commands and the
    control-plane anchor decide *whether*; ``identity.lifecycle`` is the one
    place that creates, activates, re-credentials, disables or deletes an
    account, so its invariants cannot drift apart between flows.
    """
    offenders = []
    for path, tree in _sources(PACKAGE):
        if IDENTITY in path.parents:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            method = node.func.attr
            where = f"{path.relative_to(PACKAGE)}:{node.lineno}"
            if method == "set_password":
                offenders.append(f"{where} sets a password")
                continue
            if method not in _WRITES:
                continue
            root, chain = _root_and_chain(node.func)
            if root in _IDENTITY_MODELS and "objects" in chain:
                offenders.append(f"{where} {root}.{'.'.join(chain)}")
    assert offenders == [], (
        "Write identity tables through workeasy360.platform.identity "
        "(lifecycle, tokens, account):\n" + "\n".join(offenders)
    )


ORGANIZATIONS = PACKAGE / "platform" / "organizations"
_ORG_MODELS_MODULE = "workeasy360.platform.organizations.models"

#: The one file each organizations table is written from (D-032 closed).
_ORG_WRITERS = {
    "Organization": {"platform/organizations/services.py"},
    "Membership": {"platform/organizations/services.py"},
    "OrganizationCreationRequest": {"platform/organizations/services.py"},
    "MemberSignIn": {"platform/organizations/services.py"},
    "UserOrganizationAccess": {"platform/organizations/projection.py"},
    # The invitation system (Phase 8 extension) is the one writer of
    # invitations; joining the organization goes through services.join_member.
    "Invitation": {"platform/administration/invitations.py"},
}
#: Outside the organizations package, a model class may be imported only here.
_ORG_IMPORTS_OUTSIDE = {
    "Invitation": {"platform/administration/invitations.py"},
    # Read-only: the staff user segments filter on the chooser projection.
    "UserOrganizationAccess": {"platform/administration/staff_users.py"},
}
#: Relations through which another module could reach a member or an
#: organization row it did not load itself.
_ORG_RELATIONS = {"membership", "memberships", "organization", "owner_membership"}


def _org_model_aliases(tree: ast.AST, rel: str) -> tuple[dict[str, str], list[str]]:
    """Local names bound to organizations models in this file, and any way of
    reaching the models module that the name map cannot follow."""
    aliases: dict[str, str] = {}
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _ORG_MODELS_MODULE:
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module == (
            "workeasy360.platform.organizations"
        ):
            if any(alias.name == "models" for alias in node.names):
                problems.append(f"{rel}:{node.lineno} imports the models module")
        elif isinstance(node, ast.Import):
            if any(alias.name == _ORG_MODELS_MODULE for alias in node.names):
                problems.append(f"{rel}:{node.lineno} imports the models module")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get_model"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "organizations"
        ):
            problems.append(f"{rel}:{node.lineno} apps.get_model('organizations', ...)")
    return aliases, problems


_RAW_WRITE = re.compile(
    r"\b(insert\s+into|update|delete\s+from)\s+organizations_", re.IGNORECASE
)
_SAVERS_INSIDE = {
    "platform/organizations/services.py",
    "platform/organizations/projection.py",
}


def _is_super(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "super"
    )


def _ownership_offenders(rel: str, tree: ast.AST, *, inside: bool) -> list[str]:
    """Every way ``rel`` (a production file) writes an organizations table it
    does not own, or reaches the models in a way the scan cannot follow."""
    aliases, offenders = _org_model_aliases(tree, rel)
    if not inside:
        for local, model in aliases.items():
            if rel not in _ORG_IMPORTS_OUTSIDE.get(model, set()):
                offenders.append(f"{rel} imports {model} (as {local})")
    # Inside the package a lock can serve a consistent read (the projection
    # rebuild locks what it mirrors); outside it, a lock is a write's prelude.
    writes = _WRITES - {"select_for_update"} if inside else _WRITES
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _RAW_WRITE.search(node.value):
                offenders.append(
                    f"{rel}:{node.lineno} writes an organizations table in SQL"
                )
        if not isinstance(node, ast.Call):
            continue
        where = f"{rel}:{node.lineno}"
        # Constructing a row is the first half of inserting one.
        if isinstance(node.func, ast.Name) and node.func.id in aliases:
            model = aliases[node.func.id]
            if rel not in _ORG_WRITERS[model]:
                offenders.append(f"{where} constructs {model}")
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        method = node.func.attr
        root, chain = _root_and_chain(node.func)
        if method in writes and root in aliases and {"objects", "unscoped"} & set(chain):
            model = aliases[root]
            if rel not in _ORG_WRITERS[model]:
                offenders.append(f"{where} {model}.{'.'.join(chain)}")
        elif (
            not inside
            and method in (_WRITES | {"save"})
            and len(chain) >= 2
            and chain[-2] in _ORG_RELATIONS
        ):
            offenders.append(f"{where} writes through .{chain[-2]}.{method}()")
        elif (
            inside
            and method in {"save", "delete"}
            and not _is_super(node.func.value)
            and rel not in _SAVERS_INSIDE
        ):
            offenders.append(f"{where} .{method}() outside the write boundary")
    return offenders


def test_only_the_organizations_boundary_writes_its_tables():
    """D-032 (closed): ``Organization`` and ``Membership`` have one writer,
    ``platform/organizations/services.py``; so do the creation ledger and
    sign-in activity. The chooser projection is written by ``projection.py``,
    invitations by the invitation system. Every other module decides and
    calls - onboarding, the member console, security settings, staff
    lifecycle actions, partner provisioning and the control-plane anchor.

    Static half of the proof; the runtime half is the suite-wide guard
    (``tests/write_ownership.py``), which fails the run on any executed write
    from another module - including instance saves this scan cannot follow.
    """
    offenders: list[str] = []
    scanned = 0
    for path, tree in _sources(PACKAGE):
        scanned += 1
        offenders += _ownership_offenders(
            path.relative_to(PACKAGE).as_posix(),
            tree,
            inside=ORGANIZATIONS in path.parents,
        )
    assert scanned > 300
    assert offenders == [], (
        "Write organizations tables through workeasy360.platform.organizations.services "
        "(projection rows through .projection):\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize(
    "source",
    [
        "from workeasy360.platform.organizations.models import Membership\n"
        "Membership.objects.filter(id=1).update(status='removed')",
        "from workeasy360.platform.organizations.models import Organization as O\n"
        "O.objects.create(slug='x')",
        "from workeasy360.platform.organizations.models import Membership\n"
        "Membership(organization_id=1, user_id=2).save()",
        "from workeasy360.platform.organizations import models\n"
        "models.Organization.objects.get(id=1)",
        "import workeasy360.platform.organizations.models",
        "from django.apps import apps\napps.get_model('organizations', 'Membership')",
        "def f(assignment):\n    assignment.membership.save()",
        "def f(user):\n    user.memberships.update(status='removed')",
        "from django.db import connection\n"
        "connection.cursor().execute('UPDATE organizations_membership SET status = %s', ['x'])",
        "from workeasy360.platform.organizations.models import Invitation\n"
        "Invitation.objects.create(email='x@example.com')",
    ],
)
def test_the_ownership_scan_catches_every_way_round_the_boundary(source):
    """The scan is not vacuous: each bypass, in a module outside the owner,
    is reported."""
    assert _ownership_offenders("commercial/rogue.py", ast.parse(source), inside=False)


def test_inside_the_package_only_the_boundary_saves():
    rogue = "def rename(organization):\n    organization.save()"
    assert _ownership_offenders(
        "platform/organizations/selectors.py", ast.parse(rogue), inside=True
    )
    assert not _ownership_offenders(
        "platform/organizations/services.py", ast.parse(rogue), inside=True
    )


def test_the_organizations_boundary_hands_out_records_not_rows():
    """A caller holding a model instance could change it and save it. The
    boundary's membership functions return ``MemberRecord`` (frozen), and the
    organization functions return the values a caller audits."""
    from typing import get_type_hints

    from workeasy360.platform.organizations import services

    frozen = services.MemberRecord.__dataclass_params__.frozen  # type: ignore[attr-defined]
    assert frozen
    for name in (
        "lock_member",
        "set_member_status",
        "cut_off_member_sessions",
        "mark_membership_removed",
    ):
        assert get_type_hints(getattr(services, name))["return"] is services.MemberRecord
    for name in ("create_organization_and_owner_membership",):
        hint = get_type_hints(getattr(services, name))["return"]
        assert hint is services.CreateOrganizationResult


def test_a_cross_module_model_signal_is_reported():
    """W360.E009 read each receiver as a 2-tuple; Django 5 stores 3. It passed
    only while no model signal had a receiver - the first one (the write-
    ownership guard) crashed ``manage.py check``. It must report, not crash."""
    from django.db.models.signals import post_save

    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.checks import check_no_cross_module_signals

    namespace = {"__name__": "workeasy360.commercial.rogue_listener"}
    exec("def on_member_saved(sender, **kwargs):\n    return None\n", namespace)
    listener = namespace["on_member_saved"]
    post_save.connect(listener, sender=Membership, dispatch_uid="w360-e009-probe")
    try:
        errors = check_no_cross_module_signals(None)
    finally:
        post_save.disconnect(sender=Membership, dispatch_uid="w360-e009-probe")
    assert [error.id for error in errors] == ["W360.E009"]
    assert check_no_cross_module_signals(None) == []


def test_every_console_link_in_the_frontend_resolves_to_a_page():
    """Browser-check finding: three links still pointed at ``/marketplace``
    after it became ``/explore`` - a dead page, and a prefetch that never
    settled. Every static ``/o/<slug>/...`` and ``/admin/...`` target in the
    shell must be a route that exists."""
    import re

    frontend = PACKAGE.parents[1] / "frontend" / "src"
    app = frontend / "app"
    pattern = re.compile(r"[`\"'](/o/\$\{[^}]+\}|/admin)((?:/[a-z0-9-]+)*)")
    missing = set()
    sources = [*frontend.rglob("*.tsx"), *frontend.rglob("*.ts")]
    for path in sources:
        if ".test." in path.name:
            continue
        for root, tail in pattern.findall(path.read_text(encoding="utf-8")):
            base = app / "o" / "[orgSlug]" if root.startswith("/o/") else app / "admin"
            target = base.joinpath(*[p for p in tail.split("/") if p])
            if not (target / "page.tsx").is_file():
                missing.add(f"{path.relative_to(frontend)}: {root}{tail}")
    assert len(sources) > 50
    assert missing == set(), sorted(missing)


def test_the_customer_purchase_routes_are_the_order_routes():
    """ADR-035: a customer buys only through a pay-first order. The Phase 2
    routes that granted a subscription line before payment are gone, and must
    not come back under another name."""
    from django.urls import NoReverseMatch, reverse

    for name in ("subscription-purchase", "subscription-activate"):
        with pytest.raises(NoReverseMatch):
            reverse(name, kwargs={"org_slug": "x"})
    assert reverse("orders", kwargs={"org_slug": "x"}).endswith("/orgs/x/orders")


def test_no_administrator_credential_is_committed():
    """ADR-036 / brief section 22: the Super Admin comes from the deployment's
    secret store. Neither variable is ever assigned a value in a tracked
    configuration file, and the bootstrap reads them from the environment."""
    repo = PACKAGE.parents[1]
    tracked = [
        repo / ".env.example",
        repo / "Makefile",
        repo / ".github" / "workflows" / "ci.yml",
        *repo.glob("docker*"),
        *repo.glob("Dockerfile*"),
        *(repo / "backend" / "config").rglob("*.py"),
    ]
    for path in tracked:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            for variable in ("PLATFORM_SUPERADMIN_EMAIL", "PLATFORM_SUPERADMIN_PASSWORD"):
                if variable in line and "=" in line:
                    value = line.split("=", 1)[1].strip().strip("\"'")
                    assert value == "", f"{path.name} assigns {variable}"
    command = (
        PACKAGE
        / "commercial"
        / "controlplane"
        / "management"
        / "commands"
        / "bootstrap_superadmin.py"
    ).read_text(encoding="utf-8")
    assert "os.environ" in command
