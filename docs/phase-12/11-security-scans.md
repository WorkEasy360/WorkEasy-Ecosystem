# Go-live security scans (Phase 12, Step 59)

Every scanner was run on the final tree. Nothing is waived silently: each finding that is not a defect is triaged below, with the reason recorded next to the code or in the scanner's configuration so that CI keeps enforcing the rule for everything else.

| Scanner | Scope | Result |
|---|---|---|
| **ruff check / ruff format --check** | `backend/` | pass / pass |
| **mypy** (strict, `contracts/`) | 29 files | no issues |
| **lint-imports** | 18 architecture contracts | 18 kept, 0 broken |
| **bandit -ll** | `backend/workeasy360`, `backend/config` | no issues |
| **pip-audit --strict** | `requirements.txt` + `requirements-dev.txt` (including the new `gunicorn==26.2.0`, `boto3==1.43.105`) | no known vulnerabilities |
| **npm audit** | `frontend` (clean `npm ci`) | 0 vulnerabilities |
| **semgrep 1.177** | `p/python p/django p/typescript p/react p/secrets p/dockerfile`; 269 rules over the final tree's 1,055 scannable files | **0 findings** once the 2 triaged false positives (below) were annotated on the line semgrep anchors them to. Semgrep only partially parsed 3 files: both Dockerfiles (multi-line `ENV`/`RUN`) and one TSX line (the JSX text "Subscription & billing"). These are parser limits, not code defects; the Dockerfiles are also covered by trivy and review. |
| **gitleaks 8.28** | full git history + the working tree (most of the code is uncommitted) | **all false positives**, triaged in `.gitleaks.toml`; clean with it. The final audit found the allowlist excluded whole directories (`tests/`, `scripts/`, which also covered `deploy/scripts/`). It now names the 13 triaged files one by one, so a new secret-looking value anywhere fails CI until it is triaged (G-61). History and final tree: clean. |
| **trivy 0.70** (vuln + secret) | both images, release candidates 2 to 5 (final) | **0 CRITICAL, 0 fixable HIGH, 0 secrets** (below) |
| **CodeQL** | Python + JavaScript/TypeScript | **configured** (`.github/workflows/codeql.yml`, `security-extended`); runs on GitHub. **NOT EXECUTED** locally: there is no local CodeQL CLI, and this repository has not been pushed. |

## Triage

**semgrep `python.jwt.security.unverified-jwt-decode`** at `interfaces/api/v1/ecosystem_views.py` (userinfo) and `platform/ecosystem/clientauth.py` (private_key_jwt). Both are a *peek* to choose which key or audience to verify against, followed on the next statements by full verification: the signature uses the algorithm from the platform's own credential record, never the token header, and issuer, audience, expiry and type are checked. Annotated `# nosemgrep` on those two lines only, with the reason in the comment above.

The first annotation sat on the call's closing line, which semgrep ignores (it anchors the finding on the `options=` argument). Moved there in the final pass; semgrep now reports 0.

**gitleaks.** Every match is one of:
- the local development database passwords (`w360_app_pw`, `w360_migrator_pw`), which production refuses to boot with;
- an environment variable *name* such as `W360_OIDC_SIGNING_KEY_`;
- a `<secret>` placeholder in `deploy/env/production.env.example`;
- a deliberately synthetic fixture in a test of the platform's own secret filter (`tests/phase8/test_units.py`);
- a migration dependency tuple;
- Next.js's gitignored local dev cache.

The user's local `.env` holds only those development defaults (checked without printing values).

**trivy.**
- **Release candidate 1 (Debian 12 bookworm):** 62/63 HIGH/CRITICAL findings (5 CRITICAL).
- **Fixed for release candidate 2:**
  - both images moved to Debian 13 (trixie), same Python 3.12.14 and Node 24.21.0;
  - the npm and corepack CLIs removed from the frontend *runtime* image. They carried every Node.js finding (brace-expansion, undici, ip-address, tar) and never run in production.
- **Remaining:** 47 (backend) and 45 (frontend) HIGH, all Debian 13 OS packages **with no fixed version published** (util-linux, ncurses, libsystemd/libudev, libacl). The containers never execute the affected tools (`login`, `mount`, no setuid binaries in use). CI fails the build on any *fixable* HIGH/CRITICAL (`--ignore-unfixed --exit-code 1`), so a published fix becomes a failing build that forces a rebuild.
- **Recommended hardening (P3):** a distroless or minimal runtime image, which removes these packages entirely.

## CI enforcement added in Phase 12

`.github/workflows/ci.yml`:
- gitleaks (history and tree) with the triaged config;
- `check --deploy --fail-level ERROR` as the runtime role (RLS, ownership, timeouts);
- image build plus trivy;
- Caddyfile and alert-rule validation;
- Node 24.

`.github/workflows/codeql.yml` adds CodeQL.
