"""Phase 10 architecture: the reseller and white-label consoles reuse what exists.

The brief froze the architecture and forbade a second copy of any of it: no
partner *type* (Reseller, White-label and Affiliate are labels computed from
capability grants), no second identity, pricing, entitlement or commission
system, and no new brand or domain model. These tests read the source and the
model registry, so a later change that quietly adds one fails here rather than
in review.
"""

from __future__ import annotations

import ast
import itertools
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
PACKAGE = BACKEND / "workeasy360"
FRONTEND_SRC = BACKEND.parent / "frontend" / "src"
COMMERCIAL = PACKAGE / "commercial"


def _python_sources(root: Path = PACKAGE):
    for path in sorted(root.rglob("*.py")):
        if "migrations" in path.parts or "__pycache__" in path.parts:
            continue
        if "tests" in path.parts or path.name.startswith("test_"):
            continue
        yield path


def _frontend_sources():
    for path in sorted(FRONTEND_SRC.rglob("*")):
        if path.suffix not in {".ts", ".tsx"} or ".test." in path.name:
            continue
        yield path


def _rel(path: Path) -> str:
    return path.relative_to(BACKEND.parent).as_posix()


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _classes(root: Path):
    for path in _python_sources(root):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef):
                yield path, node


# --- no partner type ----------------------------------------------------------


FORBIDDEN_NAMES = re.compile(
    r"\b(partner_type|PartnerType|PARTNER_TYPE|ResellerPartner|WhiteLabelPartner|"
    r"AffiliatePartner|reseller_partner|white_label_partner|affiliate_partner|"
    r"is_reseller|is_white_label|is_affiliate)\b"
)


def test_no_partner_type_or_kind_of_partner_in_the_backend():
    hits = [
        f"{_rel(p)}: {m.group(0)}"
        for p in _python_sources()
        for m in FORBIDDEN_NAMES.finditer(p.read_text(encoding="utf-8"))
    ]
    assert not hits, hits


def test_no_partner_type_or_kind_of_partner_in_the_frontend():
    hits = [
        f"{_rel(p)}: {m.group(0)}"
        for p in _frontend_sources()
        for m in FORBIDDEN_NAMES.finditer(p.read_text(encoding="utf-8"))
    ]
    assert not hits, hits


def test_the_partner_account_has_no_column_that_classifies_it():
    from workeasy360.commercial.partners.models import PartnerAccount

    classifying = {"type", "kind", "tier", "category", "program", "channel", "model"}
    names = {f.name for f in PartnerAccount._meta.get_fields()}
    assert not names & classifying, names & classifying


def test_no_model_or_class_is_named_for_a_kind_of_partner():
    pattern = re.compile(r"^(Reseller|WhiteLabel|Affiliate)\w*$")
    hits = [
        f"{_rel(path)}: {node.name}"
        for path, node in _classes(PACKAGE)
        if pattern.match(node.name)
    ]
    assert not hits, hits


@pytest.mark.parametrize(
    "held",
    [
        combo
        for size in range(0, 4)
        for combo in itertools.combinations(
            ("reseller", "white_label", "affiliate"), size
        )
    ],
    ids=lambda combo: "+".join(combo) or "none",
)
def test_any_combination_of_labels_is_possible_on_one_account(held):
    """The labels are a pure function of the capabilities held, so every subset
    of them is reachable - there is no combination a partner 'type' forbids."""
    from workeasy360.commercial.partners import capabilities

    keys: set[str] = set()
    for label in held:
        keys.add(sorted(capabilities.experience_keys(label))[0])
    shown = {e["key"] for e in capabilities.experience_labels(keys)}
    assert shown == set(held)


def test_a_label_can_be_lost_without_touching_the_others():
    from workeasy360.commercial.partners import capabilities

    every = set()
    for label in (
        capabilities.RESELLER,
        capabilities.WHITE_LABEL,
        capabilities.AFFILIATE,
    ):
        every |= capabilities.experience_keys(label)
    without_white_label = every - capabilities.experience_keys(capabilities.WHITE_LABEL)
    shown = {e["key"] for e in capabilities.experience_labels(without_white_label)}
    assert shown == {"reseller", "affiliate"}


# --- the frozen models ---------------------------------------------------------------


def _model_names(module: str) -> set[str]:
    path = COMMERCIAL / module / "models.py"
    return {
        node.name
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef)
    }


def test_the_partner_models_are_the_frozen_set():
    assert _model_names("partners") == {
        "PartnerAccount",
        "PartnerCapabilityGrant",
        "PartnerMember",
        "PartnerOrganizationLink",
        "PartnerProductGrant",
        "PartnerBrand",
        "PartnerDomain",
        "PartnerOwnerInvite",
        "PartnerLogoUpload",
        # The complete reseller + white-label setup (ADR-042): business facts and
        # contact records. tests/partner_setup pins what they may never become.
        "PartnerProfile",
        "PartnerContact",
    }


def test_no_new_brand_or_domain_model_exists_anywhere():
    """Branding and domains are PartnerBrand and PartnerDomain. The white-label
    console is a view of them, not a table of its own."""
    found = {
        node.name
        for path in PACKAGE.rglob("models.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef)
        and re.search(r"Brand|Domain|WhiteLabel", node.name)
    }
    assert found == {"PartnerBrand", "PartnerDomain"}, found


def test_the_commission_and_pricing_models_are_the_frozen_set():
    assert _model_names("commissions") == {
        "ReferralCode",
        "Referral",
        "CommissionRule",
        "CommissionAccrual",
        "Payout",
        "PayoutLine",
    }
    assert _model_names("pricing") == {
        "PriceBook",
        "PlanPrice",
        "PriceTier",
        "ContractPrice",
    }


def test_phase_10_added_no_table_only_columns_to_the_owner_invitation():
    """One additive migration, in one app, that adds columns, a constraint and an
    index - never a model, never a drop, never a data rewrite."""
    migration = (
        COMMERCIAL / "partners" / "migrations" / "0003_owner_invite_claim.py"
    ).read_text(encoding="utf-8")
    operations = [
        name
        for name in re.findall(r"migrations\.(\w+)\(", migration)
        if name != "swappable_dependency"  # a dependency, not an operation
    ]
    assert operations, "no operations found"
    assert set(operations) <= {"AddField", "AddConstraint", "AddIndex", "AlterField"}, (
        operations
    )
    for app in (
        "commissions",
        "pricing",
        "provisioning",
        "subscriptions",
        "billing",
        "catalog",
    ):
        newest = sorted((COMMERCIAL / app / "migrations").glob("0*.py"))[-1].name
        assert "owner" not in newest and "phase10" not in newest, (app, newest)


# --- no second identity, pricing, entitlement or commission system -----------------------


def test_no_second_identity_system():
    """People are ``identity.User`` and nothing else: no partner, customer or
    owner user model, no password field outside identity, and only identity
    writes a password (an emailed link *completes* an account through
    ``identity.lifecycle``)."""
    forbidden = re.compile(
        r"^(Partner|Customer|Owner|Reseller|Agency)(User|Login|Credential|Identity)$"
    )
    assert not [n.name for _p, n in _classes(COMMERCIAL) if forbidden.match(n.name)]

    for path in PACKAGE.rglob("models.py"):
        if "identity" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s+password\s*=\s*models\.", text, re.M), _rel(path)

    writers = {
        _rel(p)
        for p in _python_sources()
        if re.search(r"\.set_password\(|make_password\(", p.read_text(encoding="utf-8"))
    }
    outside = {w for w in writers if "/identity/" not in w and "self_service" not in w}
    assert not outside, outside
    assert not any("commercial" in w for w in writers), writers


def test_the_owner_activation_uses_identity_and_notifications_and_writes_no_hash():
    source = (COMMERCIAL / "partners" / "activation.py").read_text(encoding="utf-8")
    imported = _imports(COMMERCIAL / "partners" / "activation.py")
    assert "workeasy360.platform.identity.lifecycle" in imported or "lifecycle" in source
    assert "check_new_password" in source, "the platform's own password policy"
    assert "complete_with_password" in source, "identity's own writer"
    assert "notify_account" in source, "the existing notification path"
    assert not re.search(r"set_password|make_password|\.password\s*=", source)
    assert "User.objects.create" not in source


def test_there_is_one_price_function():
    definitions = [
        _rel(path)
        for path in _python_sources()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef) and node.name == "resolve_price"
    ]
    assert definitions == ["backend/workeasy360/commercial/pricing/resolution.py"], (
        definitions
    )


def test_partners_and_the_console_read_and_write_prices_only_through_pricing():
    for area in ("partners", "controlplane", "commissions"):
        for path in _python_sources(COMMERCIAL / area):
            imported = _imports(path)
            direct = [
                m
                for m in imported
                if m.startswith("workeasy360.commercial.pricing.models")
            ]
            assert not direct, f"{_rel(path)} opens pricing tables: {direct}"
    # The one place a partner's own price is written names the pricing service.
    source = (COMMERCIAL / "partners" / "services.py").read_text(encoding="utf-8")
    assert "pricing.publish_price(" in source


def test_no_second_entitlement_writer():
    """Only ``commercial/provisioning`` writes entitlements (import-linter
    contract A10). Asserted again for the modules this phase added or grew."""
    phase_10 = [
        *_python_sources(COMMERCIAL / "partners"),
        *_python_sources(COMMERCIAL / "commissions"),
        *_python_sources(COMMERCIAL / "pricing"),
        *(
            COMMERCIAL / "controlplane" / n
            for n in ("partner_console.py", "partner_detail.py", "reseller_setup.py")
        ),
    ]
    for path in phase_10:
        hits = [
            m
            for m in _imports(path)
            if m.startswith("workeasy360.platform.entitlements")
            and (".services" in m or ".models" in m or ".writers" in m)
        ]
        assert not hits, f"{_rel(path)} writes entitlements: {hits}"


def test_the_commission_ledger_is_written_only_by_the_ledger_modules():
    writers = {
        p.name
        for p in _python_sources(COMMERCIAL)
        if re.search(
            r"CommissionAccrual\.objects\.(create|bulk_create)|CommissionAccrual\(",
            p.read_text(encoding="utf-8"),
        )
        and p.name != "models.py"
    }
    assert writers == {"accrual.py", "ledger.py"}, writers
    # Assigning a partner's commission appends a *rule* version; it never touches the ledger.
    rules = (COMMERCIAL / "commissions" / "rules.py").read_text(encoding="utf-8")
    assert "CommissionAccrual" not in rules


def test_the_partner_commission_assignment_delegates_to_the_one_rule_engine():
    rules = (COMMERCIAL / "commissions" / "rules.py").read_text(encoding="utf-8")
    body = rules[rules.index("def assign_partner_commission") :]
    assert "create_rule_version(" in body
    domain = (COMMERCIAL / "commissions" / "domain.py").read_text(encoding="utf-8")
    assert domain.count("def select_rule") == 1


def test_the_consoles_do_not_open_another_modules_tables():
    """The F-6 rule, asserted for exactly the modules this phase added or grew."""
    for name in ("partner_console.py", "partner_detail.py", "reseller_setup.py"):
        imported = _imports(COMMERCIAL / "controlplane" / name)
        models = [m for m in imported if ".models" in m and "workeasy360" in m]
        assert not models, f"{name}: {models}"


# --- no product-domain functionality -------------------------------------------------------


def test_commercial_code_imports_no_product():
    hits = [
        f"{_rel(p)}: {m}"
        for p in _python_sources(COMMERCIAL)
        for m in _imports(p)
        if m.startswith("workeasy360.products")
    ]
    assert not hits, hits


def test_the_frontend_phase_10_screens_import_no_product_code():
    hits = []
    for path in _frontend_sources():
        text = path.read_text(encoding="utf-8")
        if not re.search(
            r"controlplane/(PartnerConsole|AddReseller|ResellerTabs|WhiteLabel|BrandPreview|PartnerCustomers)|AcceptOwnerInvitation",
            _rel(path),
        ):
            continue
        hits += [
            f"{_rel(path)}: {m}"
            for m in re.findall(r"from \"@/features/(?:products|crm|books)\S*\"", text)
        ]
    assert not hits, hits


def test_phase_10_leaves_the_legacy_catalog_alone():
    """D-022 stays deferred. This phase reads the catalog through its selectors
    and adds no catalog migration itself.

    The migration list below is no longer Phase 10's own boundary alone: Phase
    11 legitimately added ``0004`` (per-product presentation and trial
    configuration - category, icon, ``ProductTrialConfig``), through the
    catalog's own services, not through anything in ``controlplane``. The
    import check above is what actually protects D-022; the list here is kept
    as a second, cheap tripwire and updated deliberately each time a phase
    that owns the catalog extends it.
    """
    for name in ("partner_console.py", "partner_detail.py", "reseller_setup.py"):
        touched = {
            m
            for m in _imports(COMMERCIAL / "controlplane" / name)
            if m.startswith("workeasy360.commercial.catalog")
        }
        opened = {
            m
            for m in touched
            if m != "workeasy360.commercial.catalog" and not m.endswith(".selectors")
        }
        assert not opened, (name, opened)
    migrations = sorted(
        p.name for p in (COMMERCIAL / "catalog" / "migrations").glob("0*.py")
    )
    assert migrations == [
        "0001_initial.py",
        "0002_plan_builder.py",
        "0003_product_presentation.py",
        "0004_product_category_icon_trial_config.py",
    ]
