"""OIDC signing-key rotation, exercised on the staging stack (Phase 12, Step 15).

    W360_STG_COMPOSE=<compose.sh> W360_ENV_FILE=<staging.env> W360_STG_CA=<ca> \
      python scripts/phase12_staging/key_rotation.py <results.json>

The documented operator procedure (docs/phase-12/05-secrets-and-key-rotation.md),
end to end: generate k1 -> store it in the "secret store" (the env file) ->
redeploy -> promote; mint a token with k1; generate k2 -> store -> redeploy;
both keys published; promote k2 (k1 retiring, still published, its token still
verifies); an early retirement is refused; the compromise path (--force)
unpublishes k1 at once and k1's token stops verifying while k2's verifies.
The private keys are written only to the (scratch, git-ignored) env file.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from stgclient import Browser  # noqa: E402

COMPOSE = os.environ["W360_STG_COMPOSE"]
ENV_FILE = os.environ["W360_ENV_FILE"]
RESULTS: list[dict] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {'' if ok else detail}", flush=True)


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", COMPOSE, *args], capture_output=True, text=True, timeout=600)


def manage(*args: str) -> subprocess.CompletedProcess:
    return run("exec", "-T", "api", "python", "manage.py", *args)


def shell(code: str) -> str:
    out = run("exec", "-T", "api", "python", "manage.py", "shell", "-c", code)
    return "\n".join(l for l in out.stdout.splitlines() if not l.startswith("{"))


def generate(ref: str) -> str:
    out = manage("w360_signing_key", "generate", "--ref", ref)
    kid = re.search(r"Created pending key (\S+)", out.stdout).group(1)
    pem = re.search(r"(-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----)", out.stdout, re.S).group(1)
    with open(ENV_FILE, "a", encoding="utf-8") as fh:
        fh.write(f"W360_OIDC_SIGNING_KEY_{ref.upper()}=" + pem.replace("\r", "").replace("\n", "\\n") + "\n")
    return kid


def redeploy_api() -> None:
    run("up", "-d", "--force-recreate", "api")
    for _ in range(60):
        if Browser().get("/api/v1/auth/session").status == 200:
            return
        import time

        time.sleep(2)


def jwks_kids() -> list[str]:
    return sorted(k["kid"] for k in Browser().get("/.well-known/jwks.json").body.get("keys", []))


def mint() -> str:
    return shell(
        "import uuid\nfrom workeasy360.platform.ecosystem import tokens\n"
        "t = tokens.issue_access_token(tokens.TokenSubject(user_id=uuid.uuid4(), organization_id=uuid.uuid4(), "
        "product_code='easy_crm', client_id='rotation-check', scope='openid', aal=1))\n"
        "print('TOKEN', t.value)"
    ).split("TOKEN ", 1)[1].strip().splitlines()[0]


def verifies(token: str) -> bool:
    out = shell(
        "from workeasy360.platform.ecosystem import tokens\n"
        f"try:\n tokens.verify('{token}', audience='rotation-check', expected_type=tokens.TYPE_ACCESS_TOKEN); print('VERIFIED')\n"
        "except Exception as e:\n print('REFUSED', type(e).__name__)"
    )
    return "VERIFIED" in out


def header_kid(token: str) -> str:
    import base64

    head = token.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(head + "=" * (-len(head) % 4)))["kid"]


def main() -> None:
    existing = os.environ.get("ROTATION_EXISTING_K1")
    if existing:
        # Resuming: k1 was generated, stored and promoted by an earlier run.
        k1 = existing
        check("k1 (from the earlier run) is active and published", k1 in jwks_kids(), jwks_kids())
    else:
        k1 = generate("rot1")
        redeploy_api()
        check("k1 generated as pending and published", k1 in jwks_kids(), jwks_kids())
        promoted = manage("w360_signing_key", "promote", "--kid", k1)
        check("k1 promoted (active)", promoted.returncode == 0, promoted.stderr[-200:])
    t1 = mint()
    check("a token minted now is signed by k1", header_kid(t1) == k1)

    k2 = generate("rot2")
    redeploy_api()
    both = jwks_kids()
    check("during the overlap both keys are published", k1 in both and k2 in both, both)
    promoted = manage("w360_signing_key", "promote", "--kid", k2)
    check("k2 promoted; k1 now retiring", promoted.returncode == 0, promoted.stderr[-200:])
    check("k1 (retiring) is still published", k1 in jwks_kids())
    check("a token signed by k1 before the rotation still verifies", verifies(t1))
    t2 = mint()
    check("new tokens are signed by k2", header_kid(t2) == k2)

    early = manage("w360_signing_key", "retire", "--kid", k1)
    check("retiring k1 before every token it signed has expired is refused", early.returncode != 0, (early.stdout + early.stderr)[-200:])
    check("k1 still published after the refused retirement", k1 in jwks_kids())

    forced = manage("w360_signing_key", "retire", "--kid", k1, "--force")
    check("the compromise path (--force) retires k1 at once", forced.returncode == 0, forced.stderr[-200:])
    check("k1 is no longer published", k1 not in jwks_kids(), jwks_kids())
    check("k1's token no longer verifies (compromise invalidation)", not verifies(t1))
    check("k2's token verifies", verifies(t2))

    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        json.dump({"k1": k1, "k2": k2, "results": RESULTS}, fh, indent=1)
    print(f"\n{sum(r['ok'] for r in RESULTS)}/{len(RESULTS)} checks passed")


if __name__ == "__main__":
    main()
