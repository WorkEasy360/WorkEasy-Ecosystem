"""ADR-078 automatic activation: the real-browser check, on the local SSO harness.

1. Runs ``Accounts/e2e/run_universal_acceptance.py`` (the Universal Login acceptance, Tests A-G) against THIS
   platform checkout and its production frontend build, with ``--keep``. That is the regression half: existing
   customers, sign-in, the real checkout purchase of Easy CRM and product entry, unchanged.
2. On the stack it leaves up (platform, Celery worker, production edge over https, Easy CRM's real backend and
   Next.js frontend, two template products, Chrome), it switches each product's 14-day trial on through the Super
   Admin API, adds three people who have just signed up (no organization), and runs ``journey.js``: trial, a second
   product's trial, a paid purchase through the checkout and the provider's page, two simultaneous clicks, existing
   customers, and the Super Admin screens.
3. Verifies on the server what the browser cannot see (one workspace each, the seat, the one trial), writes
   ``report.json``, and stops what it started.

    <platform venv>/Scripts/python.exe scripts/auto_activation_check/run.py

Environment: ``AUTO_WORK`` (default ``~/w360wt/auto-trial/work``); the harness's own ``W360_ULC_*`` variables
default to this checkout, its ``frontend`` build and ``~/w360wt/venvs/auto-activation``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
HARNESS = Path(os.environ.get("AUTO_HARNESS", r"C:\Users\aqw03\OneDrive\Desktop\Accounts\e2e"))
WORK = Path(os.environ.get("AUTO_WORK", str(Path.home() / "w360wt" / "auto-trial" / "work")))
os.environ.setdefault("W360_ULC_PLATFORM", str(REPO))
os.environ.setdefault("W360_ULC_FE", str(REPO / "frontend"))
os.environ.setdefault("W360_ULC_WORK", str(WORK / "ulc"))
os.environ.setdefault("W360_ULC_EVIDENCE", str(WORK / "evidence-ulc"))
os.environ.setdefault(
    "W360_PLATFORM_PY", str(Path.home() / "w360wt" / "venvs" / "auto-activation" / "Scripts" / "python.exe")
)
os.environ.setdefault("W360_BROWSER_SHARED_MACHINE", "1")
sys.path.insert(0, str(HARNESS))

import run_universal_acceptance as ua  # noqa: E402

rb, base = ua.rb, ua.base
CHECKS: list[dict] = []
TRIAL_DAYS = 14


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append({"step": name, "ok": bool(ok), "detail": str(detail)[:1500]})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {str(detail)[:300]}" if detail else ""), flush=True)
    return bool(ok)


def node(phase: str, context_file: Path) -> None:
    run = subprocess.run(  # noqa: S603 - node and this folder's own script
        ["node", str(HERE / "journey.js"), str(context_file), phase],  # noqa: S607
        env={**os.environ, "NODE_PATH": str(ua.PPTR)}, cwd=HERE, text=True, encoding="utf-8",
        capture_output=True, timeout=2400,
    )
    (WORK / f"journey-{phase}.log").write_text(run.stdout + "\n" + run.stderr, encoding="utf-8")
    result_file = WORK / f"journey-{phase}.json"
    if not result_file.exists():
        check(f"journey {phase} wrote its result", False, run.stderr[-800:])
        return
    for item in json.loads(result_file.read_text(encoding="utf-8"))["checks"]:
        check(f"[{phase}] {item['step']}", item["ok"], item["detail"])


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    shots = WORK / "shots"
    shots.mkdir(exist_ok=True)
    for old in WORK.glob("journey-*.json"):
        old.unlink()
    sys.argv = ["run_universal_acceptance.py", "--keep"]
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    regression_rc = ua.main()
    regression = {"passed": sum(1 for c in ua.CHECKS if c["ok"]), "total": len(ua.CHECKS),
                  "failed": [c["step"] for c in ua.CHECKS if not c["ok"]]}
    check(f"regression: the Universal Login acceptance on this checkout ({regression['passed']}/{regression['total']})",
          regression_rc == 0, json.dumps(regression["failed"])[:1200])
    try:
        context = json.loads((ua.WORK / "context.json").read_text(encoding="utf-8"))
        staff = rb.staff_session({"staff_email": context["staff"], "password": context["password"]})

        # Each product's trial, switched on by Super Admin (the only configuration a trial needs).
        store_cards = {}
        for key in ("crm", "accounts"):
            listing = ua.LISTINGS[key]
            put = staff.platform_put(f"/api/v1/admin/catalog/products/{listing['catalog_id']}/trial", {
                "enabled": True, "duration_days": TRIAL_DAYS, "eligible_plan_codes": [listing["plan_code"]]})
            check(f"setup: {listing['product'].name}'s {TRIAL_DAYS}-day trial switched on in Super Admin",
                  put.status_code == 200, f"{put.status_code} {put.text[:300]}")
        import httpx

        with httpx.Client(transport=rb.EdgeTransport(), timeout=30) as client:
            for _ in range(10):  # the public catalog's cache moves on commit; give it a moment
                cards = client.get(f"{rb.ORIGINS['app']}/api/v1/public/store").json()["products"]
                by_code = {c["code"]: c for c in cards}
                if all(by_code.get(ua.LISTINGS[k]["product"].code, {}).get("trial", {}).get("available")
                       for k in ("crm", "accounts")):
                    break
                time.sleep(3)
        for key in ("crm", "accounts"):
            listing = ua.LISTINGS[key]
            card = by_code[listing["product"].code]
            store_cards[key] = {"code": card["code"], "slug": card["slug"], "title": card["title"],
                                "origin": listing["product"].origin, "trial_days": TRIAL_DAYS,
                                "catalog_id": listing["catalog_id"]}

        # People who have just signed up: an account, no organization.
        seeded = json.loads(base.run(
            [str(base.PLATFORM_PY), str(HERE / "seed_people.py"), context["password"],
             "neha@e2e.test:Neha Rao", "omar@e2e.test:Omar Ali", "pia@e2e.test:Pia Das"],
            env=rb.ops_env(), cwd=base.PLATFORM).strip().splitlines()[-1])
        people = {**context["people"], **seeded}
        auto_context = {**context, "store": store_cards, "people": people, "out": str(shots),
                        "results": str(WORK / "journey.json"),
                        "axe": str(REPO / "frontend" / "node_modules" / "axe-core" / "axe.min.js")}
        auto_file = WORK / "auto-context.json"
        auto_file.write_text(json.dumps(auto_context), encoding="utf-8")

        for phase in ("trial", "trial-second", "paid", "concurrent", "existing", "admin"):
            ua.crm_frontend_alive()
            node(phase, auto_file)

        # What the server says happened.
        app = rb.ORIGINS["app"]
        crm_code = store_cards["crm"]["code"]
        for who, expect in (("neha", {crm_code, store_cards["accounts"]["code"]}), ("omar", {crm_code}),
                            ("pia", {crm_code})):
            person = ua.Person(people[who]["email"], context["password"])
            person.user_admin()
            orgs = person.call("GET", app, "/api/v1/organizations").json().get("organizations", [])
            check(f"server: {who} has exactly one organization, as its owner",
                  len(orgs) == 1 and orgs[0].get("is_owner") is True, json.dumps(orgs)[:400])
            if not orgs:
                continue
            slug = orgs[0]["slug"]
            access = person.call("GET", app, f"/api/v1/orgs/{slug}/product-access").json().get("products", {})
            opened = {code for code, a in access.items() if a.get("action") == "open"}
            check(f"server: {who}'s products are ready to open ({', '.join(sorted(expect))})", expect <= opened,
                  json.dumps({c: access.get(c, {}).get("action") for c in expect}))
            seats = person.call("GET", app, f"/api/v1/orgs/{slug}/apps/{crm_code}/seats").json()
            held = [m["email"] for m in seats.get("members", []) if m.get("held")]
            check(f"server: {who} holds an Easy CRM seat nobody assigned by hand", held == [people[who]["email"]],
                  json.dumps(held))
            if who == "pia":
                trials = person.call("GET", app, f"/api/v1/orgs/{slug}/trials").json().get("results", [])
                check("server: two simultaneous clicks made one trial", len(trials) == 1, json.dumps(trials)[:300])
    except (Exception, SystemExit) as exc:
        check("run completed without an exception", False, f"{type(exc).__name__}: {exc}")
    finally:
        rb.stop_onboarded()
        for process in base.PROCESSES:
            process.terminate()
        for process in base.PROCESSES:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        rb.docker("rm", "-f", rb.EDGE_CONTAINER, check=False)
    passed = sum(1 for c in CHECKS if c["ok"])
    report = {"started": started, "finished": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "platform": str(REPO), "regression": regression, "checks": CHECKS, "passed": passed,
              "total": len(CHECKS)}
    (WORK / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n{passed}/{len(CHECKS)} checks passed (regression {regression['passed']}/{regression['total']}); "
          f"report: {WORK / 'report.json'}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
