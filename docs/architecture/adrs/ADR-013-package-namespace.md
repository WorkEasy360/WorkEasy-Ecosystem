# ADR-013 — Package Namespace for the Top-Level Modules

**Status:** Accepted
**Date:** 2026-09-21
**Deciders:** Architecture
**Amends:** [ADR-001](ADR-001-modular-monolith-first.md), [14-repository-structure.md](../14-repository-structure.md)

---

## Context

Phase 0 specifies six top-level modules at the backend root:

```
backend/
├── contracts/
├── platform/
├── integration/
├── commercial/
├── products/
└── interfaces/
```

`platform` is a **Python standard library module**. A top-level package with
that name shadows it for the entire process, because `manage.py` puts the
backend directory first on `sys.path`.

This was verified rather than assumed:

```
$ python manage.py            # with a top-level platform/ package
platform module resolved to:  .../backend/platform/__init__.py
ERROR calling platform.system(): AttributeError:
    module 'platform' has no attribute 'system'
```

`platform.system()`, `platform.python_version()` and
`platform.python_implementation()` are called by Django (system information in
the debug view and several management commands), by psycopg, by pip, by
setuptools and by large parts of the packaging ecosystem. Every one of those
call sites breaks, and the failure is an `AttributeError` far from its cause.

There is no configuration that fixes this. `sys.path` ordering is what makes
`manage.py` work at all, and absolute imports resolve to the first match.

## Decision

**The six documented modules live inside a `workeasy360/` package. Their
directory names and every layer rule are unchanged; only the import prefix
differs.**

```
backend/
└── workeasy360/
    ├── contracts/
    ├── platform/          # directory name preserved
    ├── integration/
    ├── commercial/
    ├── products/
    └── interfaces/
```

Imports become `workeasy360.platform.tenancy.models` rather than
`platform.tenancy.models`.

## Alternatives considered

### A. Rename the module to `platform_` or `w360_platform`

- **Pro:** keeps modules at the backend root.
- **Con:** the name appears in ARCHITECTURE.md, all sixteen architecture
  documents and twelve ADRs. Renaming it makes every one of those documents
  subtly wrong, and "platform" is the word the team will keep using in
  conversation regardless. A directory whose name disagrees with how everyone
  refers to it is a permanent source of friction.
- **Rejected:** it changes the *vocabulary*, which is more expensive than
  changing the import prefix.

### B. Keep `platform/` and remove the backend directory from `sys.path`

- **Con:** then nothing imports at all. This is not an option, it is a
  restatement of the problem.

### C. Vendor a shim that re-exports the standard library `platform`

- **Con:** a package that is sometimes our kernel and sometimes the standard
  library, depending on the attribute requested. Catastrophically confusing,
  and it would break the moment the standard library gained a name we also
  used.
- **Rejected** without hesitation.

### D. Nest under a distribution package (selected)

## Why selected

1. **Nothing architectural changes.** The module map, the layer order, the
   eight forbidden edges and the fitness rules are all expressed in terms of
   directory names, and every directory name is preserved.
2. **The import-linter contracts translate mechanically** — each module
   gains a `workeasy360.` prefix and the contract types are unchanged. All
   nine contracts pass.
3. **It is conventional.** A single distribution package containing the
   application is the normal shape for a Python project, and it makes
   `pip install -e .` work later without another move.
4. **The documents stay correct.** A reader following
   14-repository-structure.md finds exactly the directories it describes.

## Consequences

**Positive**

- No shadowing of any standard library module, now or later. `products`,
  `contracts` and `interfaces` are not stdlib names today, but a single
  namespace removes the whole class of collision.
- One place to add package metadata when the backend becomes installable.

**Negative, accepted**

- Import statements are eleven characters longer.
- The `.importlinter` contracts carry a prefix, which is slightly noisier to
  read.
- Documentation written before this ADR shows unprefixed imports in
  illustrative code. The directory paths in those documents remain accurate;
  only the `import` lines differ.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Someone re-creates a top-level `platform/` | Low | It would not be found by any import; the failure is immediate and local |
| Documentation drift between prefix and reality | Low | 14-repository-structure.md updated; this ADR is linked from it |

## Verification

- `tests/architecture/test_architecture.py::test_contracts_imports_in_a_bare_interpreter`
  imports `contracts` in a subprocess with Django unconfigured and asserts no
  Django module is loaded.
- `lint-imports` reports **9 contracts kept, 0 broken** over 163 files.
- The full suite (372 tests) passes, exercising Django, psycopg, Celery and
  pip-installed dependencies that all call `platform.*`.

## Related

- [14-repository-structure.md](../14-repository-structure.md)
- [02-dependency-rules.md](../02-dependency-rules.md) §6.1
