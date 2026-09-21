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
    """G6: mass assignment."""
    offenders = []
    for path in _source_files():
        if 'fields = "__all__"' in path.read_text(encoding="utf-8"):
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


def test_products_are_structural_placeholders_only():
    """Phase 1 implements no product features."""
    for product_dir in (PACKAGE / "products").iterdir():
        if not product_dir.is_dir() or product_dir.name.startswith("_"):
            continue
        py_files = [p.name for p in product_dir.rglob("*.py")]
        assert py_files == ["__init__.py"], (
            f"products/{product_dir.name} contains more than a placeholder: {py_files}"
        )
