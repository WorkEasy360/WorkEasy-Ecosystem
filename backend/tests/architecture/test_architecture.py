"""Architecture fitness rules.

"Rules the CI does not check are rules that do not exist."

These run in the fast suite on every PR. import-linter covers the import
graph; this file covers the rules an import graph cannot see - ORM access
across boundaries, contracts purity under a bare interpreter, banned
filenames, money representation, and whether the enforcement itself is
actually enforcing anything.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
BACKEND = REPO_ROOT / "backend"
PACKAGE = BACKEND / "workeasy360"

TOP_LEVEL = (
    "contracts",
    "platform",
    "integration",
    "commercial",
    "products",
    "interfaces",
)


def _run_linter(*args: str) -> subprocess.CompletedProcess:
    """Invoke import-linter via its console script.

    Deliberately NOT ``python -m importlinter.cli``: that form exits 0 with no
    output when it cannot resolve the root package, which would make these
    tests pass while checking nothing. The console script reports the problem.
    """
    import os
    import shutil

    script = (
        shutil.which("lint-imports", path=str(pathlib.Path(sys.executable).parent))
        or "lint-imports"
    )
    return subprocess.run(
        [script, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(BACKEND)},
    )


def _source_files(*, include_tests: bool = False) -> list[pathlib.Path]:
    files = []
    for path in PACKAGE.rglob("*.py"):
        rel = path.relative_to(PACKAGE).as_posix()
        if "migrations/" in rel:
            continue
        if not include_tests and ("/tests/" in rel or rel.startswith("tests/")):
            continue
        files.append(path)
    return files


def _module_of(path: pathlib.Path) -> str:
    """``platform/authz/services.py`` -> ``platform.authz``."""
    parts = path.relative_to(PACKAGE).with_suffix("").parts
    return ".".join(parts[:2]) if len(parts) >= 2 else parts[0]


def _imports(path: pathlib.Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover
        return []

    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
        elif isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
    return found


# --- the linter itself ----------------------------------------------------


def test_import_linter_contracts_hold():
    """Run import-linter as part of the suite, not only in CI.

    A developer must find out locally, not in a pipeline twenty minutes
    later.
    """
    result = _run_linter()
    combined = result.stdout + result.stderr
    # `python -m importlinter.cli` exits 0 silently when it cannot find the
    # root package, which would make this test pass while checking nothing.
    assert "Contracts:" in combined, (
        f"import-linter did not analyse anything:\n{combined}"
    )
    assert "broken" not in combined or "0 broken" in combined, combined
    assert result.returncode == 0, combined


def test_import_linter_actually_detects_a_violation(tmp_path):
    """Prove the enforcement is not vacuous.

    A contract that never fails is indistinguishable from no contract. This
    writes a deliberate violation into a scratch copy of the config and
    asserts the linter reports it.
    """
    # A forbidden contract that is certainly violated: platform imports
    # contracts everywhere, so forbidding it must produce a failure.
    config = tmp_path / ".importlinter"
    config.write_text(
        "[importlinter]\n"
        "root_package = workeasy360\n\n"
        "[importlinter:contract:canary]\n"
        "name = Canary - platform must not import contracts (deliberately false)\n"
        "type = forbidden\n"
        "source_modules =\n"
        "    workeasy360.platform\n"
        "forbidden_modules =\n"
        "    workeasy360.contracts\n",
        encoding="utf-8",
    )

    result = _run_linter("--config", str(config))
    combined = result.stdout + result.stderr
    assert result.returncode != 0 or "BROKEN" in combined, (
        "import-linter did not flag a known violation, so the contracts in "
        f".importlinter prove nothing:\n{combined}"
    )


# --- contracts purity -----------------------------------------------------


def test_contracts_imports_nothing_internal():
    offenders = []
    for path in _source_files(include_tests=True):
        if not path.relative_to(PACKAGE).as_posix().startswith("contracts/"):
            continue
        for module in _imports(path):
            for forbidden in (
                "platform",
                "integration",
                "commercial",
                "products",
                "interfaces",
            ):
                if module.startswith(f"workeasy360.{forbidden}"):
                    offenders.append(f"{path.name}: {module}")
            if module == "django" or module.startswith("django."):
                offenders.append(f"{path.name}: {module}")
    assert not offenders, offenders


def test_contracts_imports_in_a_bare_interpreter():
    """contracts/ must import with Django entirely unconfigured.

    This is what makes "contracts depends on nothing" checkable rather than
    aspirational: a subprocess with no DJANGO_SETTINGS_MODULE.
    """
    code = (
        "import workeasy360.contracts.enums as e;"
        "import workeasy360.contracts.errors as err;"
        "import workeasy360.contracts.values.money as m;"
        "import workeasy360.contracts.values.tenant as t;"
        "import workeasy360.contracts.values.identifiers as i;"
        "import workeasy360.contracts.events.envelope as env;"
        "import workeasy360.contracts.events.registry as reg;"
        "import workeasy360.contracts.ports.entitlements as pe;"
        "import sys;"
        "assert 'django' not in sys.modules, sorted("
        "  k for k in sys.modules if k.startswith('django'));"
        "print('ok')"
    )
    import os

    # A real environment minus Django's settings pointer. Stripping the
    # environment entirely breaks the interpreter's own start-up on Windows,
    # which would make this test fail for a reason unrelated to the rule.
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    env["PYTHONPATH"] = str(BACKEND)

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout


# --- cross-module ORM access (F-6) ---------------------------------------


def test_no_cross_module_orm_access():
    """Even where the import is legal by layer, reading another module's
    tables is not.

    A queryset over another module's model welds you to its schema, and when
    that schema changes your code breaks without being in its test suite.
    Selectors are a contract; models are an implementation detail.
    """
    offenders = []
    for path in _source_files():
        owner = _module_of(path)
        rel = path.relative_to(PACKAGE).as_posix()

        # The tenancy base model references other modules' models by
        # *string*, which is not an import.
        if rel.startswith("platform/tenancy/"):
            continue

        for module in _imports(path):
            if not module.startswith("workeasy360."):
                continue
            if not module.endswith(".models") and ".models." not in module:
                continue
            target = ".".join(module.split(".")[1:3])
            if target == owner:
                continue
            # platform.tenancy supplies TenantScopedModel. Inheriting a base
            # class is not reading another module's tables - it is the
            # mechanism by which a model becomes tenant-scoped at all.
            if target == "platform.tenancy":
                continue
            # Organization, User and File are the frozen FK allowlist
            # (ADR-009), so declaring a relation to them is permitted.
            if target in {
                "platform.organizations",
                "platform.identity",
                "platform.files",
            }:
                continue
            offenders.append(f"{rel} imports {module} (owned by {target})")

    assert not offenders, "Cross-module ORM access:\n" + "\n".join(offenders)


def test_no_product_imports_another_products_models():
    """The defining violation, checked at the AST level as well as by the
    linter's independence contract."""
    offenders = []
    for path in _source_files(include_tests=True):
        rel = path.relative_to(PACKAGE).as_posix()
        if not rel.startswith("products/"):
            continue
        own_product = rel.split("/")[1]
        for module in _imports(path):
            if module.startswith("workeasy360.products."):
                other = module.split(".")[2]
                if other != own_product:
                    offenders.append(f"{rel} imports {module}")
    assert not offenders, offenders


# --- banned patterns ------------------------------------------------------


def test_no_unowned_dumping_ground_modules():
    """G1: utils.py, helpers.py, common.py, misc.py.

    They accumulate business logic nobody can find or own. Name the concern
    instead: date_ranges.py, tax_ids.py.
    """
    banned = {"utils.py", "helpers.py", "common.py", "misc.py", "util.py"}
    offenders = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in PACKAGE.rglob("*.py")
        if p.name in banned
    ]
    assert not offenders, offenders


def test_no_products_shared_module():
    """G2: it becomes a second, unowned product everyone depends on, and it
    will contain CRM logic within a year."""
    assert not (PACKAGE / "products" / "shared").exists()
    assert not (PACKAGE / "products" / "common").exists()


def test_no_raw_sql_built_with_f_strings():
    """G7. Identifiers in the migration helpers are validated and quoted by
    ``_ident``; nothing else builds SQL by formatting."""
    offenders = []
    for path in _source_files():
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name != "execute" or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.JoinedStr):
                offenders.append(f"{path.relative_to(PACKAGE).as_posix()}:{first.lineno}")
    assert not offenders, "f-string SQL passed to execute():\n" + "\n".join(offenders)


def test_no_writable_serializer_uses_fields_all():
    """G6: mass assignment.

    Read as an assignment rather than as text. A docstring that explains the
    rule is documentation, not a violation, and a check that cannot tell the
    two apart teaches people to avoid mentioning it - which is the opposite
    of what a fitness rule is for.
    """
    offenders = []
    for path in _source_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Assign):
                continue
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if (
                "fields" in names
                and isinstance(node.value, ast.Constant)
                and node.value.value == "__all__"
            ):
                offenders.append(path.relative_to(PACKAGE).as_posix())
    assert not offenders, offenders


def test_no_star_imports():
    """G: breaks static analysis and the import linter."""
    offenders = []
    for path in _source_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "*" for alias in node.names
            ):
                offenders.append(f"{path.relative_to(PACKAGE).as_posix()}:{node.lineno}")
    assert not offenders, offenders


def test_no_pickle_anywhere():
    """12-security.md section 7: banned, including as a Celery serializer."""
    offenders = []
    for path in _source_files():
        for module in _imports(path):
            if module in {"pickle", "cPickle", "dill"}:
                offenders.append(path.relative_to(PACKAGE).as_posix())
    assert not offenders, offenders


# --- money (R-08) ---------------------------------------------------------


def test_no_float_fields_on_models():
    """A float amount anywhere in pricing, invoicing, commission or ledger
    code produces rounding errors that compound."""
    from django.apps import apps
    from django.db import models

    offenders = []
    for model in apps.get_models():
        if not (model.__module__ or "").startswith("workeasy360."):
            continue
        for field in model._meta.get_fields():
            if isinstance(field, models.FloatField):
                offenders.append(f"{model.__name__}.{field.name}")
    assert not offenders, f"FloatField on a model: {offenders}"


def test_money_refuses_float():
    from workeasy360.contracts.values.money import CurrencyCode, Money

    with pytest.raises(TypeError):
        Money(10.99, CurrencyCode.GBP)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Money.from_major(10.99, CurrencyCode.GBP)  # type: ignore[arg-type]


def test_money_refuses_cross_currency_arithmetic():
    from workeasy360.contracts.values.money import (
        CurrencyCode,
        CurrencyMismatch,
        Money,
    )

    with pytest.raises(CurrencyMismatch):
        Money(100, CurrencyCode.GBP) + Money(100, CurrencyCode.USD)


def test_money_allocation_loses_nothing():
    """5p split three ways is 2p/2p/1p. Naive division silently loses or
    invents money."""
    from workeasy360.contracts.values.money import CurrencyCode, Money

    total = Money(5, CurrencyCode.GBP)
    parts = total.allocate([1, 1, 1])
    assert sum(p.minor_units for p in parts) == 5
    assert sorted(p.minor_units for p in parts) == [1, 2, 2]


def test_money_respects_currency_exponent():
    """JPY has no minor unit. Assuming two decimal places everywhere is a
    common and expensive multi-currency bug."""
    from decimal import Decimal

    from workeasy360.contracts.values.money import CurrencyCode, Money

    assert Money.from_major(Decimal("100"), CurrencyCode.JPY).minor_units == 100
    assert Money.from_major(Decimal("1.00"), CurrencyCode.GBP).minor_units == 100
    with pytest.raises(ValueError):
        Money.from_major(Decimal("1.5"), CurrencyCode.JPY)


# --- tenancy fitness ------------------------------------------------------


def test_every_tenant_model_has_an_isolation_test():
    """B8. A tenant model whose isolation is untested is a hypothesis.

    Coverage here is *structural*, not a grep for the model name:
    tests/isolation/test_every_tenant_model.py is parametrised over the live
    model registry, so a tenant model added tomorrow is exercised the moment
    it exists. A corpus grep would pass for a model merely mentioned
    somewhere, which is not the same as covered.

    This test asserts the parametrised suite really enumerates every model.
    """
    from django.apps import apps

    from tests.isolation import test_every_tenant_model as suite
    from workeasy360.platform.tenancy.models import TenantScopedModel

    expected = {
        m._meta.label
        for m in apps.get_models()
        if issubclass(m, TenantScopedModel)
        and not m._meta.abstract
        and (m.__module__ or "").startswith("workeasy360.")
    }
    covered = {m._meta.label for m in suite.ALL_TENANT_MODELS}

    assert expected, "No tenant models found - the check would be vacuous."
    assert covered == expected, (
        "The parametrised isolation suite does not enumerate every tenant "
        f"model. Missing: {sorted(expected - covered)}"
    )


def test_unscoped_manager_use_is_allowlisted():
    """B7. Bypassing tenant scoping is permitted, never silent.

    The allowlist is what keeps `.unscoped` from spreading: adding a module
    here is a deliberate, reviewable act.
    """
    allowed_prefixes = (
        "platform/tenancy/",
        "platform/entitlements/services/write.py",
        # Reads grants for provisioning and enumerates organizations for
        # reconciliation, both of which are legitimately cross-tenant and
        # both of which enter per-organization scope before writing.
        "platform/entitlements/selectors.py",
        "platform/authz/services.py",
        "platform/organizations/resolver.py",
        "platform/notifications/tasks.py",
        "commercial/provisioning/",
        # --- Phase 2 additions, each mirroring an existing precedent ---
        #
        # record_system() audits clock-driven commercial transitions, which
        # have no actor and therefore no tenant context. Same shape as
        # platform/entitlements/selectors.py: the caller has entered the
        # organization's database scope, so RLS still confines the write to
        # one tenant.
        "platform/audit/services.py",
        # Provisioning reads commercial state outside a request context,
        # exactly as it reads entitlement grants.
        "commercial/subscriptions/selectors.py",
        # The single system write pathway: trials ending, cancellations
        # taking effect, periods rolling. It exists because those operations
        # must work for a *suspended* organization, whose writes R-10 blocks
        # under any tenant context. Narrowed to one file so grep gives the
        # complete list of system writers.
        "commercial/subscriptions/system.py",
    )

    offenders = []
    for path in _source_files():
        rel = path.relative_to(PACKAGE).as_posix()
        if rel.startswith(allowed_prefixes):
            continue
        if ".unscoped" in path.read_text(encoding="utf-8"):
            offenders.append(rel)

    assert not offenders, "Unscoped manager used outside the allowlist:\n" + "\n".join(
        offenders
    )


def test_system_checks_pass():
    """W360.E001-E009 run in CI and at container start."""
    from django.core.management import call_command

    call_command("check", verbosity=0)


# --- the product boundary (ADR-017) ---------------------------------------
#
# This repository is the ecosystem platform. Every business product - CRM,
# Books, Inventory, Projects, ... - is a separate application in its own
# repository, integrating through the contracts in
# 16-ecosystem-product-boundary.md. The five tests below are what stops
# product-domain code drifting back in, which is exactly how the boundary
# was lost the first time: one product, built here "just to prove the
# platform works".


def test_products_are_structural_placeholders_only():
    """``products/`` is a reserved, permanently empty namespace.

    Unconditional on purpose. Phase 3 built CRM here and carried an
    ``IMPLEMENTED_PRODUCTS`` exemption to let this test keep passing; that
    exemption is gone, and no replacement should be added. The packages
    exist only so the import-linter independence contracts have modules to
    bind to - if a product needs code, it needs a repository.
    """
    for product_dir in (PACKAGE / "products").iterdir():
        if not product_dir.is_dir() or product_dir.name.startswith("_"):
            continue
        py_files = [p.name for p in product_dir.rglob("*.py")]
        assert py_files == ["__init__.py"], (
            f"products/{product_dir.name} contains more than a placeholder: "
            f"{py_files}. Products are separate applications (ADR-017)."
        )


def test_no_product_apps_are_installed():
    """No product package is a Django app in this project.

    An installed product app means models, migrations and tables - the
    concrete form of the boundary being crossed.
    """
    from django.conf import settings

    assert settings.PRODUCT_APPS == [], (
        f"PRODUCT_APPS must stay empty; found {settings.PRODUCT_APPS}."
    )
    offenders = [a for a in settings.INSTALLED_APPS if ".products." in a]
    assert not offenders, f"Product packages installed as apps: {offenders}"


#: Product codes that must never appear as a *branch condition* in platform
#: source. They may appear as catalog data, in fixtures and in docstrings -
#: ``Product(code="crm")`` is a row, which is what the catalog is for.
PRODUCT_CODES = (
    "crm",
    "books",
    "inventory",
    "projects",
    "marketing",
    "support",
    "hr",
    "payroll",
    "commerce",
)


def test_no_platform_module_branches_on_a_product_code():
    """Adding a product must never require editing the platform.

    ``if product_code == "crm"`` is the failure this catches: the moment one
    exists, every new product means finding and editing every such branch.
    Generic handling - catalog, registry, manifest - is the alternative, and
    it already exists for every case the platform has.

    Comparisons are found in the AST, so a docstring or a comment naming a
    product cannot trip it, and a fixture *creating* ``Product(code="crm")``
    cannot either - only a comparison against a literal.
    """
    offenders = []
    for path in _source_files():
        rel = path.relative_to(PACKAGE).as_posix()
        if rel.startswith("products/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
            elif isinstance(node, ast.Match):
                operands = [
                    c.pattern.value
                    for c in node.cases
                    if isinstance(c.pattern, ast.MatchValue)
                ]
            else:
                continue
            for operand in operands:
                if (
                    isinstance(operand, ast.Constant)
                    and isinstance(operand.value, str)
                    and operand.value in PRODUCT_CODES
                ):
                    offenders.append(f"{rel}:{node.lineno} compares to {operand.value!r}")

    assert not offenders, (
        "Platform code branches on a product code; use catalog/registry data "
        "instead (ADR-017):\n" + "\n".join(offenders)
    )


def test_api_exposes_no_product_domain_routes():
    """The platform routes platform capabilities only.

    A product serves its own domain API from its own application. A route
    here named after a product is the boundary crossing in its most visible
    form.
    """
    from django.urls import get_resolver

    def walk(resolver, prefix=""):
        for pattern in resolver.url_patterns:
            route = prefix + str(getattr(pattern.pattern, "_route", pattern.pattern))
            if hasattr(pattern, "url_patterns"):
                yield from walk(pattern, route)
            else:
                yield route

    offenders = [
        route
        for route in walk(get_resolver())
        if any(f"/{code}/" in f"/{route}" for code in PRODUCT_CODES)
    ]
    assert not offenders, f"Product-domain routes served by the platform: {offenders}"


def test_contracts_declares_no_product_domain_events():
    """Event schemas in ``contracts/`` describe platform and commercial facts.

    A product's events are the product's to define and version. Phase 3 put
    eight ``crm.*`` schemas here; nothing consumed them, and they described
    one product's internals in the layer with the strictest purity rules in
    the system.
    """
    from workeasy360.contracts.events.registry import registry

    offenders = [
        ref for ref in registry.all_refs() if str(ref).split(".", 1)[0] in PRODUCT_CODES
    ]
    assert not offenders, (
        f"Product-domain event schemas registered by the platform: {offenders}"
    )


# --- the remote product integration boundary (Phase 3) --------------------
#
# ADR-018 puts the ecosystem front door inside platform/ at P3. These guards
# hold the three properties that placement is only safe because of: it never
# reaches commercial, it never puts a product on the platform's critical
# path, and it never fetches a URL a product gave it.


def test_product_health_never_affects_platform_readiness():
    """A product's problem must never de-pool a healthy platform instance.

    Product health is Tier 3 *by definition*, because the platform does not
    need a product to function (16-ecosystem-product-boundary.md section 10
    rule 1). If an ecosystem probe could fail readiness, then an operator
    forgetting to promote a signing key - or a product being down - would
    pull every instance out of the load balancer while login, billing and
    org management continued to work perfectly.

    Asserted against the live probe registry rather than by reading the
    source, so registering a probe into the wrong group fails here.
    """
    from workeasy360.platform.observability import health

    for group in ("startup", "registries"):
        assert "ecosystem" not in health.registered_probe_names(group), (
            f"An ecosystem probe is registered in the {group!r} group, which "
            f"gates whether an instance serves traffic. It belongs in 'deep'."
        )
    # And it *is* registered somewhere, so this is not vacuous.
    assert "ecosystem" in health.registered_probe_names("deep")


def test_readiness_does_not_touch_the_integration_registry(db):
    """The behavioural half of the rule above.

    A registry with no signing key and no integrations at all - the state a
    fresh deployment is in - must still be ready.
    """
    from django.test import Client

    from workeasy360.platform.ecosystem.models import ProductIntegration, SigningKey

    assert not SigningKey.objects.exists()
    assert not ProductIntegration.objects.exists()

    response = Client().get("/healthz/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert "ecosystem" not in {c["name"] for c in response.json()["checks"]}


def test_the_ecosystem_module_makes_no_outbound_http_request():
    """The SSRF control, expressed structurally.

    The registry stores three product-supplied URLs - launch, redirect and
    health. A stored URL is not a fetched URL, and the reason this phase has
    no SSRF surface is that **nothing fetches them**: there is no egress
    allowlist to get wrong because there is no egress.

    Checked by import rather than by grep for a function name, so adding a
    client library to this module fails the build rather than quietly
    reopening the question (docs/phase-3/11-security-threat-model.md T-11).
    """
    # Named precisely rather than by package root. ``urllib.parse`` is string
    # parsing and is exactly what the manifest validator needs; ``urllib.request``
    # opens sockets. Matching on the root would forbid the safe one and teach
    # the next reader that the rule is arbitrary.
    clients = {
        "requests",
        "httpx",
        "aiohttp",
        "urllib3",
        "urllib.request",
        "urllib.error",
        "http.client",
        "socket",
        "ftplib",
        "telnetlib",
        "smtplib",
        "xmlrpc.client",
    }
    offenders = []
    for path in _source_files():
        rel = path.relative_to(PACKAGE).as_posix()
        if not rel.startswith("platform/ecosystem/"):
            continue
        for module in _imports(path):
            if module in clients or any(module.startswith(f"{c}.") for c in clients):
                offenders.append(f"{rel} imports {module}")

    assert not offenders, (
        "platform/ecosystem performs outbound I/O. Every URL it holds came "
        "from a product, so fetching one is SSRF against the platform's own "
        "network:\n" + "\n".join(offenders)
    )


def test_every_integration_write_permission_requires_step_up():
    """The carried AAL2 trigger, reached and wired.

    Registering an integration or changing a redirect URI is a full
    account-takeover primitive against every user of that product; minting a
    credential reads every organization's entitlement state; rotating the
    signing key mints tokens for anyone. All of them are `dangerous`, which
    the decision function turns into "AAL 2, and never under impersonation".
    """
    from workeasy360.platform.authz.registry import permission_registry

    declarations = [
        d
        for d in permission_registry.all()
        if str(d.key).startswith("platform.integration.")
    ]
    assert declarations, "No integration permissions are registered."

    writes = [d for d in declarations if not str(d.key).endswith(".view")]
    assert writes, "The check would be vacuous with no write permissions."
    assert all(d.dangerous for d in writes), [
        str(d.key) for d in writes if not d.dangerous
    ]
    # The read is deliberately *not* dangerous - see permissions.py.
    assert not permission_registry.get("platform.integration.view").dangerous


def test_the_token_claim_set_is_exactly_what_is_documented():
    """A claim added without a decision is a claim products start depending
    on, and removing it then breaks them.

    Pinned against the discovery document's advertised list, which is the
    external contract, so adding a claim means updating what the platform
    tells the world it issues.
    """
    from workeasy360.platform.ecosystem import discovery, tokens

    advertised = set(discovery.document()["claims_supported"])
    assert set(tokens.BASE_CLAIMS) <= advertised

    # The ecosystem's own claims, and no others. `membership_id` is the one
    # to watch: it was considered and deliberately not issued, because a
    # product keys its data on (sub, organization_id) and has no use for the
    # platform's join row.
    assert {"organization_id", "product_code", "aal"} <= advertised
    assert "membership_id" not in advertised
    assert "password" not in str(advertised)


def test_no_product_code_is_hardcoded_in_the_ecosystem_module():
    """Adding a product must never mean editing the front door.

    Already covered generically by
    test_no_platform_module_branches_on_a_product_code; asserted separately
    here because this is the module where a special case would be most
    tempting - "CRM needs a longer token", "Books skips the entitlement
    check" - and most damaging.
    """
    offenders = []
    for path in _source_files():
        rel = path.relative_to(PACKAGE).as_posix()
        if not rel.startswith("platform/ecosystem/"):
            continue
        source = path.read_text(encoding="utf-8")
        for code in PRODUCT_CODES:
            if f'"{code}"' in source or f"'{code}'" in source:
                offenders.append(f"{rel} contains the literal {code}")
    assert not offenders, offenders


def test_the_manifest_contract_lives_in_contracts_and_stays_pure():
    """The manifest is the boundary contract an external product submits, so
    it must be usable by a documentation generator or an external conformance
    harness with no Django and no platform present."""
    import os

    code = (
        "import workeasy360.contracts.values.manifest as m;"
        "import workeasy360.contracts.ports.catalog as c;"
        "import workeasy360.contracts.ports.signing as s;"
        "import sys;"
        "assert 'django' not in sys.modules;"
        "parsed = m.parse_manifest({"
        "  'manifest_version': m.MANIFEST_VERSION,"
        "  'product_code': 'crm',"
        "  'launch_url': 'https://x.example/launch',"
        "  'redirect_uris': ['https://x.example/cb']});"
        "assert parsed.permits_redirect('https://x.example/cb');"
        "assert not parsed.permits_redirect('https://x.example/cb/evil');"
        "print('ok')"
    )
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    env["PYTHONPATH"] = str(BACKEND)

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout


def test_every_published_error_code_is_one_the_platform_can_emit():
    """The discovery document's error list must be neither short nor long.

    ``workeasy360_error_codes`` is published so that an integrator can write
    a switch and know it is exhaustive. Both directions of drift hurt, and
    they hurt differently:

    - a code the server emits but does not publish is a branch the
      integrator never wrote, hit in production;
    - a code published but never emitted is a branch they *did* write, which
      never runs and which they will maintain forever.

    The second is the one that had actually happened: ``access_denied`` and
    ``unauthorized_client`` were declared, published and raised by nothing.

    Derived from the AST rather than from a hand-kept list, because a
    hand-kept list is the thing being checked.
    """
    from workeasy360.platform.ecosystem import discovery, protocol

    protocol_path = PACKAGE / "platform" / "ecosystem" / "protocol.py"
    sources = [
        *sorted((PACKAGE / "platform" / "ecosystem").glob("*.py")),
        PACKAGE / "interfaces" / "api" / "v1" / "ecosystem_views.py",
    ]

    # The constant *names* that carry a wire error code, so the scan can look
    # for identifiers rather than for string literals - nothing raises a bare
    # "invalid_grant", and it should stay that way.
    by_name = {
        name: value
        for name, value in vars(protocol).items()
        if name.isupper() and isinstance(value, str) and value in protocol.ERROR_CODES
    }
    assert len(by_name) == len(protocol.ERROR_CODES), (
        "A code in ERROR_CODES has no constant naming it, so no raise site "
        "could reference it by name."
    )

    emitted: set[str] = set()
    for path in sources:
        if path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if path == protocol_path:
            # The ERROR_CODES literal names every constant by construction.
            # Without this the test would assert that the list matches
            # itself, which is the most comfortable kind of nothing.
            tree.body = [
                node
                for node in tree.body
                if not (
                    isinstance(node, ast.AnnAssign | ast.Assign)
                    and "ERROR_CODES"
                    in {
                        t.id
                        for t in (
                            [node.target]
                            if isinstance(node, ast.AnnAssign)
                            else node.targets
                        )
                        if isinstance(t, ast.Name)
                    }
                )
            ]
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in by_name:
                emitted.add(by_name[node.attr])
            elif (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in by_name
            ):
                emitted.add(by_name[node.id])

    published = set(discovery.document()["workeasy360_error_codes"])
    assert published == set(protocol.ERROR_CODES)
    assert emitted == published, {
        "published but never raised": sorted(published - emitted),
        "raised but not published": sorted(emitted - published),
    }
