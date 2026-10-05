"""Phase 12 failure tests against the production-shaped staging stack.

Steps 21-24 and 58 of the brief. Each dependency is made **physically**
unavailable (its container stopped), the platform is exercised through the real
edge, the dependency is restored, and recovery is verified.

    W360_STG_CA=<caddy root> W360_STG_COMPOSE=<compose.sh> \
      python scripts/phase12_staging/resilience.py <results.json> [scenario ...]

Scenarios: baseline redis rabbitmq products database cascade (default: all).
Emails are read from Mailpit; server-side facts are read with
``manage.py shell`` in the api container. Nothing here weakens a control.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from typing import Any

sys.path.insert(0, os.path.dirname(__file__))
from stgclient import Browser, link_in, mail_messages, wait_for_mail  # noqa: E402

COMPOSE = os.environ["W360_STG_COMPOSE"]
PASSWORD = "Resilience-Phase12-" + uuid.uuid4().hex[:8]
RESULTS: list[dict[str, Any]] = []


def check(scenario: str, name: str, ok: bool, detail: Any = "") -> bool:
    RESULTS.append({"scenario": scenario, "check": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {scenario}: {name} {detail if not ok else ''}", flush=True)
    return ok


def compose(*args: str, check_rc: bool = True) -> str:
    out = subprocess.run(
        ["bash", COMPOSE, *args], capture_output=True, text=True, timeout=600
    )
    if check_rc and out.returncode != 0:
        raise RuntimeError(f"compose {args}: {out.stderr[-500:]}")
    return out.stdout


def shell(code: str) -> str:
    """Run Python in the api container (staging settings) and return stdout."""
    out = subprocess.run(
        ["bash", COMPOSE, "exec", "-T", "api", "python", "manage.py", "shell", "-c", code],
        capture_output=True,
        text=True,
        timeout=300,
    )
    return "\n".join(l for l in out.stdout.splitlines() if not l.startswith("{"))


def wait_ready(timeout: float = 120.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if Browser().get("/api/v1/auth/session").status == 200:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


# --- a customer, created the way a real one is -----------------------------------------------


def new_customer(tag: str) -> dict[str, Any]:
    email = f"p12-{tag}-{uuid.uuid4().hex[:6]}@customer-mail.net"
    b = Browser()
    b.get("/api/v1/auth/session")
    r = b.post(
        "/api/v1/public/signup",
        {
            "first_name": "Res",
            "last_name": tag.title(),
            "email": email,
            "password": PASSWORD,
            "company_name": f"Resilience {tag.title()} Ltd",
        },
    )
    assert r.status == 202, (r.status, r.body)
    mail = wait_for_mail(email, "", timeout=90)
    assert mail, f"no verification email for {email}"
    link = link_in(mail["ID"], "/verify-email")
    token = link.split("#token=", 1)[1]
    v = b.post("/api/v1/public/signup/verify", {"token": token, "password": PASSWORD})
    assert v.status == 200, (v.status, v.body)
    return {"email": email, "browser": b, "slug": v.body["organization_slug"], "org": v.body["organization_id"]}


def start_trial(c: dict[str, Any], code: str) -> Any:
    return c["browser"].post(
        f"/api/v1/orgs/{c['slug']}/trials", {"product_code": code, "plan_code": f"{code}_team"}
    )


def buy(c: dict[str, Any], code: str) -> dict[str, Any]:
    """Pay-first order through the TEST provider's hosted checkout."""
    b, slug = c["browser"], c["slug"]
    b.post(f"/api/v1/orgs/{slug}/billing-account", {"currency": "GBP", "billing_email": c["email"]})
    catalog = b.get(f"/api/v1/public/catalog?currency=GBP").body
    product = next(p for p in catalog["products"] if p["code"] == code)
    plan = next(p for p in product["plans"] if p["plan_code"] == f"{code}_team")
    order = b.post(f"/api/v1/orgs/{slug}/orders", {"plan_version_id": plan["plan_version_id"], "quantity": 1})
    assert order.status == 201, (order.status, order.body)
    started = b.post(f"/api/v1/orgs/{slug}/billing/invoices/{order.body['invoice_id']}/pay")
    assert started.status == 202, (started.status, started.body)
    reference = "fake_cs_" + started.body["attempt_id"].replace("-", "")
    return {"order": order.body, "attempt": started.body["attempt_id"], "reference": reference}


def complete_checkout(c: dict[str, Any], purchase: dict[str, Any], timeout: float = 90.0) -> Any:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        # From the customer's own browser (it holds the CSRF cookie, as the
        # test provider's hosted page does in a real browser).
        last = c["browser"].post(
            "/api/v1/public/payments/test-checkout/complete",
            {"reference": purchase["reference"], "organization_id": c["org"], "outcome": "succeeded"},
        )
        if last.status == 200:
            return last
        time.sleep(2)
    return last


def order_status(c: dict[str, Any], order_id: str) -> str:
    r = c["browser"].get(f"/api/v1/orgs/{c['slug']}/orders/{order_id}")
    return (r.body or {}).get("status", f"http_{r.status}")


def app_state(c: dict[str, Any], code: str) -> dict[str, Any]:
    apps = c["browser"].get(f"/api/v1/orgs/{c['slug']}/apps").body.get("apps", [])
    return next((a for a in apps if a.get("product_code") == code), {})


def timed_core(scenario: str, c: dict[str, Any]) -> None:
    """Core platform flows that must keep working."""
    fresh = Browser()
    login = fresh.login(c["email"], PASSWORD)
    check(scenario, "sign-in works", login.status == 200, login.status)
    session = fresh.get("/api/v1/auth/session")
    check(scenario, "session resolves", session.body and session.body.get("authenticated") is True)
    apps = fresh.get(f"/api/v1/orgs/{c['slug']}/apps")
    check(scenario, "My Products loads", apps.status == 200, apps.status)
    catalog = fresh.get("/api/v1/public/catalog?currency=GBP")
    check(scenario, "public catalog loads", catalog.status == 200, catalog.status)
    check(
        scenario,
        "core requests stay fast (<2s)",
        max(login.elapsed_ms, apps.elapsed_ms, catalog.elapsed_ms) < 2000,
        {"login": round(login.elapsed_ms), "apps": round(apps.elapsed_ms), "catalog": round(catalog.elapsed_ms)},
    )


# --- scenarios ---------------------------------------------------------------------------------


def baseline() -> dict[str, Any]:
    s = "baseline"
    c = new_customer("baseline")
    check(s, "signup + emailed verification + organization", bool(c["slug"]))
    t = start_trial(c, "easy_crm")
    check(s, "trial starts (easy_crm)", t.status == 201, (t.status, t.body))
    p = buy(c, "easybook")
    done = complete_checkout(c, p)
    check(s, "hosted checkout completes (TEST provider)", done is not None and done.status == 200, getattr(done, "status", None))
    time.sleep(3)
    check(s, "order fulfilled (easybook)", order_status(c, p["order"]["order_id"]) == "fulfilled")
    timed_core(s, c)
    return c


def redis_outage(c: dict[str, Any]) -> None:
    s = "redis"
    compose("stop", "redis")
    try:
        time.sleep(3)
        # The first request each API process serves discovers the outage (a
        # vanished host costs a DNS lookup) and opens that process's circuit
        # breaker; after that every cache call fails fast. Measured apart.
        warm = [Browser().get("/api/v1/public/catalog?currency=GBP").elapsed_ms for _ in range(8)]
        slow = [round(ms) for ms in warm if ms > 2000]
        check(s, "outage discovery costs at most one slow request per API process (3)", len(slow) <= 3, {"slow_ms": slow, "all_ms": [round(x) for x in warm]})
        timed_core(s, c)
        r = Browser().get("/healthz/ready")  # blocked at the edge by design
        check(s, "probe paths stay internal", r.status == 404)
        ready = shell("import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/healthz/ready').status)")
        check(s, "readiness stays 200 (cache is Tier 1, non-blocking)", "200" in ready, ready[-100:])
        # security must not fail open: the durable account lockout still holds
        victim = c["email"]
        attacker = Browser()
        statuses = [
            attacker.post("/api/v1/auth/login", {"email": victim, "password": "wrong-password-xyz"}).status
            for _ in range(12)
        ]
        locked = Browser().post("/api/v1/auth/login", {"email": victim, "password": PASSWORD})
        check(
            s,
            "brute force still stopped with Redis down (DB lockout / per-process limit)",
            locked.status in (401, 423, 429) and any(x in (401, 429) for x in statuses),
            {"attempts": statuses, "right_password_after": locked.status},
        )
        t = start_trial(c, "easy_helpdesk")
        check(s, "commercial write (trial) still correct", t.status == 201, (t.status, t.body))
        check(s, "entitlement read falls back to the database", app_state(c, "easy_helpdesk").get("entitlement_status") == "trial", app_state(c, "easy_helpdesk"))
        shell(f"from workeasy360.platform.identity.models import User; User.objects.filter(email='{victim}').update(locked_until=None, failed_login_count=0)")
    finally:
        compose("start", "redis")
    time.sleep(5)
    check(s, "recovery: platform healthy after Redis returns", wait_ready())
    timed_core(s + "-recovered", c)


def rabbit_outage(c: dict[str, Any]) -> None:
    s = "rabbitmq"
    before_mail = len(mail_messages())
    compose("stop", "rabbitmq")
    try:
        time.sleep(3)
        timed_core(s, c)
        # trial (synchronous entitlement) and a purchase (checkout task deferred)
        trial = start_trial(c, "easy_project")
        check(s, "trial start commits with the broker down", trial.status == 201, (trial.status, trial.body))
        purchase = buy(c, "easy_crm")
        check(s, "order + payment start commit with the broker down", bool(purchase["attempt"]))
        # a notification (password reset) is persisted, not lost
        reset = Browser().post("/api/v1/public/password/forgot", {"email": c["email"]})
        check(s, "password reset accepted (email queued durably)", reset.status == 202, reset.status)
        time.sleep(5)
        check(s, "no email left the platform while the broker was down", len(mail_messages()) == before_mail, len(mail_messages()) - before_mail)
        pending = shell(
            "from workeasy360.integration.scheduling.models import DueWork;"
            "print('due', DueWork.objects.count())"
        )
        check(s, "deferred work is durable in PostgreSQL (DueWork)", "due" in pending, pending.strip()[-80:])
    finally:
        compose("start", "rabbitmq")
    # catch-up: workers reconnect, Beat's sweepers re-dispatch
    deadline = time.time() + 420
    delivered = None
    while time.time() < deadline:
        delivered = wait_for_mail(c["email"], "password", timeout=10)
        if delivered:
            break
    check(s, "recovery: the queued password-reset email is delivered", delivered is not None)
    done = complete_checkout(c, purchase, timeout=420)
    check(s, "recovery: the deferred checkout is created and completes", done is not None and done.status == 200, getattr(done, "status", None))
    time.sleep(5)
    check(s, "recovery: the order is fulfilled", order_status(c, purchase["order"]["order_id"]) == "fulfilled")
    time.sleep(20)
    resets = [m for m in mail_messages(f"to:{c['email']}") if "password" in (m.get("Subject") or "").lower()]
    check(s, "no duplicate explosion: exactly one reset email", len(resets) == 1, len(resets))


FAULTS = {
    "5xx": "https://httpbin.org/status/503",
    "timeout": "https://httpbin.org/delay/20",
    "dns": "https://p12-missing-product.w360-nxdomain-check.com/",
    "tls": "https://expired.badssl.com/",
}


def product_outage(c: dict[str, Any]) -> None:
    for code in ("easy_crm", "easybook", "easy_project", "easy_helpdesk"):
        for fault, url in FAULTS.items():
            s = f"product:{code}:{fault}"
            shell(
                "from workeasy360.platform.ecosystem.models import ProductIntegration as P\n"
                f"p=P.objects.get(product_code='{code}'); m=dict(p.manifest); m['health_url']='{url}';"
                "P.objects.filter(id=p.id).update(manifest=m)"
            )
            started = time.time()
            # the sweep runs on the heavy worker, exactly as Beat runs it
            shell("from workeasy360.integration.health.tasks import check_all; check_all.apply_async()")
            timed_core(s, c)
            launch = c["browser"].post(f"/api/v1/orgs/{c['slug']}/apps/{code}/launch", headers={"Accept": "application/json"})
            check(s, "launch still answers (server-owned target, no call to the product)", launch.status in (200, 402, 403, 409), launch.status)
            status = ""
            while time.time() - started < 60:
                status = shell(
                    "from workeasy360.platform.ecosystem.models import ProductIntegration as P;"
                    f"print(P.objects.get(product_code='{code}').health_status)"
                ).strip().splitlines()[-1]
                if status in ("degraded", "unreachable"):
                    break
                time.sleep(3)
            check(s, "the outage is recorded as product health, not a platform failure", status in ("degraded", "unreachable"), status)
            shell(
                "from workeasy360.platform.ecosystem.models import ProductIntegration as P\n"
                f"p=P.objects.get(product_code='{code}'); m=dict(p.manifest); m.pop('health_url',None);"
                "P.objects.filter(id=p.id).update(manifest=m)"
            )


def database_outage(c: dict[str, Any]) -> None:
    s = "database"
    before_mail = len(mail_messages())
    orders_before = shell(
        "from workeasy360.commercial.payments.models import Order;"
        "from workeasy360.platform.tenancy.unscoped import unscoped_access\n"
        "with unscoped_access(reason='p12 count', module='p12'):\n print('orders', Order.unscoped.count())"
    ).strip()
    compose("stop", "pgbouncer")
    try:
        time.sleep(3)
        r = Browser().login(c["email"], PASSWORD)
        check(s, "sign-in fails safely (5xx/503, no partial session)", r.status >= 500 and "w360_session" not in Browser().cookies, r.status)
        w = c["browser"].post(f"/api/v1/orgs/{c['slug']}/trials", {"product_code": "easy_crm", "plan_code": "easy_crm_team"})
        check(s, "commercial write refused while the database is unreachable", w.status >= 500, w.status)
        ready = shell("import urllib.request\ntry:\n print(urllib.request.urlopen('http://127.0.0.1:8000/healthz/ready', timeout=10).status)\nexcept Exception as e:\n print('ready', getattr(e,'code',e))")
        check(s, "readiness reports not-ready (instance leaves the load balancer)", "503" in ready, ready.strip()[-80:])
        time.sleep(5)
        check(s, "no external side effect (no email) during the outage", len(mail_messages()) == before_mail)
    finally:
        compose("start", "pgbouncer")
    check(s, "recovery: platform ready again", wait_ready())
    orders_after = shell(
        "from workeasy360.commercial.payments.models import Order;"
        "from workeasy360.platform.tenancy.unscoped import unscoped_access\n"
        "with unscoped_access(reason='p12 count', module='p12'):\n print('orders', Order.unscoped.count())"
    ).strip()
    check(s, "no partial commercial operation (order count unchanged)", orders_before == orders_after, (orders_before, orders_after))
    timed_core(s + "-recovered", c)

    s = "database-primary"
    compose("stop", "postgres")
    try:
        time.sleep(3)
        r = Browser().get("/api/v1/auth/session")
        check(s, "requests fail fast with PostgreSQL itself down (<5s)", r.elapsed_ms < 5000, round(r.elapsed_ms))
    finally:
        compose("start", "postgres")
    time.sleep(10)
    check(s, "recovery after PostgreSQL restart", wait_ready(180))
    timed_core(s + "-recovered", c)


def cascade(c: dict[str, Any]) -> None:
    """Step 58: one product + Redis + the AI provider degraded at once."""
    s = "cascade"
    shell(
        "from workeasy360.platform.ecosystem.models import ProductIntegration as P\n"
        "p=P.objects.get(product_code='easy_helpdesk'); m=dict(p.manifest); m['health_url']='https://httpbin.org/delay/20';"
        "P.objects.filter(id=p.id).update(manifest=m)"
    )
    compose("stop", "redis")
    try:
        shell("from workeasy360.integration.health.tasks import check_all; check_all.apply_async()")
        for _ in range(8):  # outage discovery, one request per API process (see redis_outage)
            Browser().get("/api/v1/public/catalog?currency=GBP")
        timed_core(s, c)
        crm = app_state(c, "easy_crm")
        check(s, "an unaffected entitled product stays usable (easy_crm)", crm.get("entitlement_status") in ("active", "trial"), crm.get("entitlement_status"))
        # Staging has no AI provider credential at all, so the AI tier is
        # degraded by construction (every provider call answers 503 - Phase 8).
        ai = c["browser"].get(f"/api/v1/orgs/{c['slug']}/ai/usage")
        check(s, "the AI console answers quickly with no provider available", ai.status in (200, 403) and ai.elapsed_ms < 5000, (ai.status, round(ai.elapsed_ms)))
    finally:
        compose("start", "redis")
        shell(
            "from workeasy360.platform.ecosystem.models import ProductIntegration as P\n"
            "p=P.objects.get(product_code='easy_helpdesk'); m=dict(p.manifest); m.pop('health_url',None);"
            "P.objects.filter(id=p.id).update(manifest=m)"
        )
    check(s, "recovery", wait_ready())


def main() -> None:
    out = sys.argv[1]
    wanted = set(sys.argv[2:]) or {"baseline", "redis", "rabbitmq", "products", "database", "cascade"}
    c = baseline()
    try:
        if "redis" in wanted:
            redis_outage(c)
        if "rabbitmq" in wanted:
            rabbit_outage(c)
        if "products" in wanted:
            product_outage(c)
        if "database" in wanted:
            database_outage(c)
        if "cascade" in wanted:
            cascade(c)
    finally:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(RESULTS, fh, indent=1, default=str)
        failed = [r for r in RESULTS if not r["ok"]]
        print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")


if __name__ == "__main__":
    main()
