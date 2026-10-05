// Real-browser verification of Phase 9 (public site, self-service accounts,
// dashboard, pay-first checkout, Super Admin) against the production build and
// a real backend: CSP violations, console errors, axe (WCAG 2.1 A/AA incl.
// colour contrast), visible keyboard focus, one h1, reflow at 320px - and the
// journeys end to end, reading the emailed links from the backend's console
// email output (BACKEND_LOG). Needs ORG_SLUG, PLAN_BUSINESS and OWNER_TOTP from
// seed.py. The membership journey drives every flow the Phase 9 closure moved
// onto the organizations write boundary (D-032).
// Not part of the repository's CI.
const puppeteer = require("puppeteer-core");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const BASE = process.env.BASE || "http://127.0.0.1:3999";
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const AXE = fs.readFileSync(process.env.AXE_PATH, "utf8");
const PASSWORD = "Browser-Test-Password-1!";
const NEW_PASSWORD = "Another-Browser-Password-2!";
const { ORG_SLUG, PLAN_BUSINESS, BACKEND_LOG, OWNER_TOTP } = process.env;

const report = { pages: [], csp: [], errors: [], expected: [], reflow: [], flows: [] };
// The check's own negative requests (signing in unverified, reusing a spent
// link) make the browser log "Failed to load resource"; those are expected.
let expectingFailure = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function newPage(browserOrContext, width = 1280) {
  const page = await browserOrContext.newPage();
  await page.setViewport({ width, height: 900 });
  await page.evaluateOnNewDocument(() => {
    window.__csp = [];
    document.addEventListener("securitypolicyviolation", (e) =>
      window.__csp.push(`${e.violatedDirective} ${e.blockedURI} ${e.sourceFile || ""}`),
    );
  });
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const line = `${page.url()} :: ${m.text()}`;
    if (expectingFailure > 0 && m.text().startsWith("Failed to load resource")) report.expected.push(line);
    else report.errors.push(line);
  });
  page.on("pageerror", (e) => report.errors.push(`${page.url()} :: pageerror ${e.message}`));
  return page;
}

async function signIn(page, email, password = PASSWORD) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle0" });
  await page.type('input[type="email"]', email);
  await page.type('input[type="password"]', password);
  await Promise.all([
    page.click('button[type="submit"]'),
    page.waitForNetworkIdle({ idleTime: 800, timeout: 20000 }).catch(() => {}),
  ]);
  // /dashboard decides where to go; give it the round trip.
  await page.waitForFunction(() => !location.pathname.startsWith("/login") && location.pathname !== "/dashboard", {
    timeout: 20000,
  }).catch(() => {});
  await page.waitForNetworkIdle({ idleTime: 500, timeout: 15000 }).catch(() => {});
  const session = await page.evaluate(() => fetch("/api/v1/auth/session").then((r) => r.json()));
  if (!session.authenticated) throw new Error(`sign-in failed for ${email}`);
  return page;
}

async function login(browser, email, password = PASSWORD) {
  const context = await browser.createBrowserContext();
  return signIn(await newPage(context), email, password);
}

async function audit(page, label) {
  const csp = await page.evaluate(() => window.__csp || []);
  csp.forEach((c) => report.csp.push(`${label}: ${c}`));
  const axe = await page.evaluate(async (src) => {
    (0, eval)(src); // Runtime.evaluate is not subject to the page CSP
    const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
    return r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, sample: v.nodes[0] && v.nodes[0].html.slice(0, 140) }));
  }, AXE);
  const noFocus = [];
  await page.evaluate(() => document.activeElement && document.activeElement.blur());
  let tabbed = 0;
  for (let i = 0; i < 40; i++) {
    await page.keyboard.press("Tab");
    const info = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return null;
      const cs = getComputedStyle(el);
      const outline = cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0;
      const shadow = cs.boxShadow && cs.boxShadow !== "none";
      return { tag: el.tagName, text: (el.innerText || el.getAttribute("aria-label") || el.name || "").slice(0, 40), visible: outline || shadow || el.matches("input,select,textarea") };
    });
    if (info) {
      tabbed++;
      if (!info.visible) noFocus.push(`${info.tag} "${info.text}"`);
    }
  }
  const h1 = await page.evaluate(() => [...document.querySelectorAll("h1")].map((h) => h.innerText));
  report.pages.push({ label, axe, tabbed, noFocus, h1 });
}

async function visit(page, url, label) {
  console.log("visit", label);
  await page.goto(`${BASE}${url}`, { waitUntil: "networkidle0", timeout: 30000 });
  await page.waitForSelector("main", { timeout: 15000 }).catch(() => {});
  await sleep(500);
  await audit(page, label);
}

async function reflow(browser, cookiesFrom, urls) {
  const page = await newPage(browser, 320);
  if (cookiesFrom) await page.setCookie(...(await cookiesFrom.cookies()));
  for (const url of urls) {
    await page.goto(`${BASE}${url}`, { waitUntil: "networkidle0", timeout: 30000 });
    await sleep(300);
    const w = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
    report.reflow.push({ url, ...w, ok: w.scroll <= w.inner + 1 });
  }
  await page.close();
}

// The newest link of a kind the backend "emailed" (console email backend).
function emailedLink(kind, after = 0) {
  const text = fs.readFileSync(BACKEND_LOG, "utf8");
  const re = new RegExp(`${kind}#token=([A-Za-z0-9_.\\-]+)`, "g");
  let match;
  let last = null;
  while ((match = re.exec(text))) if (match.index >= after) last = match[1];
  return last;
}
const logSize = () => fs.statSync(BACKEND_LOG).size;

async function waitForLink(kind, after) {
  for (let i = 0; i < 40; i++) {
    const token = emailedLink(kind, after);
    if (token) return token;
    await sleep(250);
  }
  throw new Error(`no ${kind} link was emailed`);
}

async function payOnTestPage(page) {
  await page.waitForFunction(() => location.pathname.startsWith("/billing/test-checkout"), { timeout: 30000 });
  await page.waitForSelector("::-p-text(Pay (succeed))", { timeout: 15000 });
  await audit(page, "billing/test-checkout (hosted test page)");
  await Promise.all([
    page.click("::-p-text(Pay (succeed))"),
    page.waitForNetworkIdle({ idleTime: 800, timeout: 20000 }).catch(() => {}),
  ]);
}

async function orderStatuses(page, slug) {
  for (let i = 0; i < 20; i++) {
    const statuses = await page.evaluate(async (s) => {
      const r = await fetch(`/api/v1/orgs/${s}/orders`).then((x) => x.json());
      return r.results.map((o) => o.status);
    }, slug);
    if (!statuses.includes("paid") && !statuses.includes("pending_payment")) return statuses;
    await sleep(500);
  }
  return page.evaluate(async (s) => (await fetch(`/api/v1/orgs/${s}/orders`).then((x) => x.json())).results.map((o) => o.status), slug);
}

// A visitor chooses a paid plan on the pricing page, signs up, confirms the
// emailed link with the password, and lands in checkout for that plan; pays on
// the provider's page; the product is then in My products.
async function signupJourney(browser) {
  const context = await browser.createBrowserContext();
  const page = await newPage(context);
  await page.goto(`${BASE}/pricing`, { waitUntil: "networkidle0" });
  await Promise.all([
    page.click('[aria-label="Buy: Business"]'),
    page.waitForFunction(() => location.pathname === "/signup", { timeout: 15000 }),
  ]);
  await page.waitForSelector("::-p-aria(First name)");
  await page.type("::-p-aria(First name)", "Nora");
  await page.type("::-p-aria(Last name)", "Newcomer");
  await page.type("::-p-aria(Work email)", "nora@browser.test");
  await page.type('input[autocomplete="new-password"]', PASSWORD);
  await page.type("::-p-aria(Company name (optional))", "Newcomer Labs");
  const mark = logSize();
  await page.click("::-p-text(Create account)");
  await page.waitForSelector("::-p-text(Check your email)", { timeout: 15000 });
  await audit(page, "signup: check your email");
  const token = await waitForLink("verify-email", mark);

  // Sign-in before verifying is refused with the reason (the password matched).
  expectingFailure++;
  const early = await page.evaluate(async () => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("w360_csrf="));
    const r = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json", "x-csrftoken": csrf ? csrf.split("=")[1] : "" },
      body: JSON.stringify({ email: "nora@browser.test", password: "Browser-Test-Password-1!" }),
    });
    return { status: r.status, body: await r.json().catch(() => ({})) };
  });
  await sleep(300);
  expectingFailure--;
  report.flows.push({ flow: "an unverified account cannot sign in", ok: early.status >= 400 && JSON.stringify(early.body).includes("email_not_verified"), early });

  await page.goto(`${BASE}/verify-email#token=${token}`, { waitUntil: "networkidle0" });
  await page.waitForSelector("::-p-text(Confirm and sign in)");
  await audit(page, "verify-email (link opened)");
  await page.type('input[type="password"]', PASSWORD);
  await page.click("::-p-text(Confirm and sign in)");
  await page.waitForFunction(() => location.pathname.includes("/checkout"), { timeout: 30000 });
  const slug = new URL(page.url()).pathname.split("/")[2];
  report.flows.push({ flow: "verification signs in and resumes the chosen plan at checkout", ok: true, url: page.url().replace(BASE, "") });

  // No billing account yet: currency first (the region is not the customer's).
  await page.waitForSelector("::-p-aria(Currency)", { timeout: 15000 });
  await audit(page, "checkout (billing details first)");
  await page.select("::-p-aria(Currency)", "GBP");
  await page.click("::-p-text(Open billing account)");
  await page.waitForSelector("::-p-text(Place order and pay)", { timeout: 20000 });
  await audit(page, "checkout (server quote)");
  await page.click("::-p-text(Place order and pay)");
  await payOnTestPage(page);
  const statuses = await orderStatuses(page, slug);
  report.flows.push({ flow: "a paid order is fulfilled (pay-first, no step-up)", ok: statuses.includes("fulfilled"), statuses });
  const apps = await page.evaluate(async (s) => (await fetch(`/api/v1/orgs/${s}/apps`).then((x) => x.json())).apps, slug);
  const desk = apps.find((a) => a.product_code === "exampledesk");
  report.flows.push({ flow: "the product is then held (entitlement active)", ok: !!desk && desk.entitlement_status === "active", desk });
  await visit(page, `/o/${slug}/apps`, "new customer: my products");
  await visit(page, `/o/${slug}/orders`, "new customer: orders");
  await page.close();
}

function base32(secret) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of secret.replace(/=+$/, "").toUpperCase()) bits += alphabet.indexOf(c).toString(2).padStart(5, "0");
  const bytes = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(bytes);
}

// RFC 6238 (SHA-1, 30 s, 6 digits) - what the authenticator app would show.
function totp(secret, at = Date.now()) {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(at / 1000 / 30)));
  const mac = crypto.createHmac("sha1", base32(secret)).update(counter).digest();
  const offset = mac[mac.length - 1] & 0xf;
  const value = (mac.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
  return String(value).padStart(6, "0");
}

// A request the page itself makes, with its session and CSRF token.
async function api(page, method, url, body) {
  return page.evaluate(
    async ({ method, url, body }) => {
      const csrf = document.cookie.split("; ").find((c) => c.startsWith("w360_csrf="));
      const r = await fetch(url, {
        method,
        headers: { "content-type": "application/json", "x-csrftoken": csrf ? csrf.split("=")[1] : "" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      let json = null;
      try {
        json = await r.json();
      } catch {
        json = null;
      }
      return { status: r.status, body: json };
    },
    { method, url, body },
  );
}

// Every flow the Phase 9 closure moved onto the organizations write boundary,
// driven through the real HTTP surface: invite -> accept in the invitee's own
// browser -> suspend -> reactivate -> step up -> sign the member out -> rename
// (the member's chooser follows) -> the security policy with its version.
async function membershipJourney(browser, owner) {
  const org = `/api/v1/orgs/${ORG_SLUG}`;
  const mark = logSize();
  const invited = await api(owner, "POST", `${org}/invitations`, { email: "joiner@browser.test", role: "member" });
  const token = await waitForLink("invitations/accept", mark);

  const context = await browser.createBrowserContext();
  const joiner = await newPage(context);
  await joiner.goto(`${BASE}/invitations/accept#token=${token}`, { waitUntil: "networkidle0" });
  await joiner.waitForSelector('input[autocomplete="name"]', { timeout: 15000 });
  await audit(joiner, "invitations/accept (link opened)");
  await joiner.type('input[autocomplete="name"]', "Jo Joiner");
  const passwords = await joiner.$$('input[autocomplete="new-password"]');
  await passwords[0].type(PASSWORD);
  await passwords[1].type(PASSWORD);
  await Promise.all([
    joiner.click("::-p-text(Create account and join)"),
    joiner.waitForNetworkIdle({ idleTime: 800, timeout: 20000 }).catch(() => {}),
  ]);
  const accessAfterJoin = (await api(joiner, "GET", `${org}/access`)).status;
  report.flows.push({
    flow: "an invitee accepts in the browser and is a member (join_member)",
    ok: invited.status === 201 && accessAfterJoin === 200,
    invited: invited.status,
    accessAfterJoin,
  });

  const members = (await api(owner, "GET", `${org}/members?limit=100`)).body;
  const row = (members.results || members.items || []).find((m) => m.email === "joiner@browser.test");
  const id = row && row.membership_id;

  expectingFailure++;
  const suspended = (await api(owner, "POST", `${org}/members/${id}/suspend`)).status;
  const whileSuspended = (await api(joiner, "GET", `${org}/access`)).status;
  const reactivated = (await api(owner, "POST", `${org}/members/${id}/reactivate`)).status;
  const afterReactivate = (await api(joiner, "GET", `${org}/access`)).status;
  report.flows.push({
    flow: "suspending shuts the member out and reactivating lets them back (set_member_status)",
    ok: suspended === 200 && whileSuspended >= 400 && reactivated === 200 && afterReactivate === 200,
    suspended,
    whileSuspended,
    reactivated,
    afterReactivate,
  });

  const refused = await api(owner, "POST", `${org}/members/${id}/sign-out`);
  const withoutStepUp = `${refused.status} ${refused.body && refused.body.code}`;
  const stepped = (await api(owner, "POST", "/api/v1/auth/mfa/step-up", { code: totp(OWNER_TOTP) })).status;
  const signedOut = (await api(owner, "POST", `${org}/members/${id}/sign-out`)).status;
  const afterSignOut = (await api(joiner, "GET", `${org}/access`)).status;
  await sleep(300);
  expectingFailure--;
  report.flows.push({
    flow: "signing a member out needs step-up and ends their session here (cut_off_member_sessions)",
    ok: withoutStepUp === "401 step_up_required" && stepped === 200 && signedOut === 200 && afterSignOut >= 400,
    withoutStepUp,
    stepped,
    signedOut,
    afterSignOut,
  });

  const renamed = await api(owner, "PATCH", `${org}/organization`, { display_name: "Browser Organization Renamed" });
  // Cut off here, the member signs in again (a new session) and finds the
  // organization under its new name.
  const again = await login(browser, "joiner@browser.test");
  const chooser = (await api(again, "GET", "/api/v1/organizations")).body;
  report.flows.push({
    flow: "renaming reaches the member's organization chooser in the same transaction (rename_organization)",
    ok: renamed.status === 200 && JSON.stringify(chooser).includes("Browser Organization Renamed"),
    renamed: renamed.status,
  });

  const policy = (await api(owner, "GET", `${org}/security/policy`)).body;
  const change = { require_mfa: false, session_max_age_minutes: 720, password_min_length: null, version: policy.version };
  const saved = (await api(owner, "PUT", `${org}/security/policy`, change)).status;
  expectingFailure++;
  const stale = await api(owner, "PUT", `${org}/security/policy`, change);
  await sleep(300);
  expectingFailure--;
  report.flows.push({
    flow: "the security policy saves once per version; a stale save is a conflict (update_access_policy)",
    ok: saved === 200 && stale.status === 409 && stale.body && stale.body.code === "concurrent_modification",
    saved,
    stale: stale.status,
  });
  await visit(owner, `/o/${ORG_SLUG}/settings/members`, "org /o/<slug>/settings/members (after the journey)");
  await joiner.close();
}

async function resetJourney(browser) {
  const page = await newPage(await browser.createBrowserContext());
  await page.goto(`${BASE}/forgot-password`, { waitUntil: "networkidle0" });
  await page.type('input[type="email"]', "member@browser.test");
  const mark = logSize();
  await page.click("::-p-text(Email me a reset link)");
  await page.waitForSelector("::-p-text(Check your email)", { timeout: 15000 });
  const token = await waitForLink("reset-password", mark);
  await page.goto(`${BASE}/reset-password#token=${token}`, { waitUntil: "networkidle0" });
  await page.waitForSelector("::-p-aria(New password)");
  await audit(page, "reset-password (link opened)");
  await page.type("::-p-aria(New password)", NEW_PASSWORD);
  await page.type("::-p-aria(Repeat the new password)", NEW_PASSWORD);
  await page.click("::-p-text(Change password)");
  await page.waitForSelector("::-p-text(Password changed)", { timeout: 15000 });
  expectingFailure++;
  const reused = await page.evaluate(async (t) => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("w360_csrf="));
    const r = await fetch("/api/v1/public/password/reset", {
      method: "POST",
      headers: { "content-type": "application/json", "x-csrftoken": csrf ? csrf.split("=")[1] : "" },
      body: JSON.stringify({ token: t, password: "Yet-Another-Password-3!" }),
    });
    return r.status;
  }, token);
  await sleep(300);
  expectingFailure--;
  await signIn(page, "member@browser.test", NEW_PASSWORD);
  report.flows.push({ flow: "a reset link sets the password once and the new one signs in", ok: reused >= 400, reused });
  await page.close();
}

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--no-sandbox", "--disable-gpu"] });
  try {
    // --- the public site, signed out ---------------------------------------------------
    const anon = await newPage(await browser.createBrowserContext());
    const publicUrls = ["/", "/pricing", "/products/example-desk", "/products/voice-desk", "/services", "/contact",
      "/signup", "/login", "/forgot-password", "/verify-email", "/reset-password"];
    for (const u of publicUrls) await visit(anon, u, `public ${u}`);
    await anon.goto(`${BASE}/`, { waitUntil: "networkidle0" });
    await anon.click("::-p-text(Products)");
    await sleep(300);
    const menu = await anon.evaluate(() => ({
      expanded: [...document.querySelectorAll("button[aria-expanded]")].some((b) => b.innerText.startsWith("Products") && b.getAttribute("aria-expanded") === "true"),
      links: [...document.querySelectorAll("ul a")].map((a) => a.innerText),
    }));
    report.flows.push({ flow: "the Products menu lists the catalog", ok: menu.expanded && menu.links.some((l) => l.includes("Example Desk")), menu });
    await reflow(browser, null, publicUrls);

    // --- journeys ----------------------------------------------------------------------
    await signupJourney(browser);
    await resetJourney(browser);

    // --- everyone lands on the dashboard; staff on the Super Admin ---------------------
    const owner = await login(browser, "owner@browser.test");
    report.flows.push({ flow: "an owner lands on the dashboard", ok: owner.url().endsWith(`/o/${ORG_SLUG}/dashboard`), landing: owner.url() });
    const admin = await login(browser, "admin@browser.test");
    report.flows.push({ flow: "an administrator lands on the dashboard", ok: admin.url().endsWith(`/o/${ORG_SLUG}/dashboard`), landing: admin.url() });
    const member = await login(browser, "member@browser.test", NEW_PASSWORD);
    report.flows.push({ flow: "a member lands on the dashboard", ok: member.url().endsWith(`/o/${ORG_SLUG}/dashboard`), landing: member.url() });
    const staff = await login(browser, "staff@browser.test");
    report.flows.push({ flow: "the bootstrapped Super Admin lands on the console", ok: staff.url().endsWith("/admin"), landing: staff.url() });

    // --- the owner's console -----------------------------------------------------------
    const orgUrls = ["dashboard", "apps", "explore", "orders", "help", "settings/billing", `checkout?plan=${PLAN_BUSINESS}&interval=monthly`]
      .map((s) => `/o/${ORG_SLUG}/${s}`);
    for (const u of orgUrls) await visit(owner, u, `org ${u.replace(ORG_SLUG, "<slug>").replace(PLAN_BUSINESS, "<plan>")}`);
    await visit(member, `/o/${ORG_SLUG}/dashboard`, "member dashboard");
    await reflow(browser, owner, orgUrls);

    await membershipJourney(browser, owner);

    // The seeded order awaiting payment is paid from Orders - no step-up.
    await owner.goto(`${BASE}/o/${ORG_SLUG}/orders`, { waitUntil: "networkidle0" });
    const pay = await owner.waitForSelector('button[aria-label^="Pay order"]', { timeout: 15000 });
    await pay.click();
    const stepUp = await owner.waitForSelector('input[autocomplete="one-time-code"]', { timeout: 4000 }).then(() => true).catch(() => false);
    await payOnTestPage(owner);
    const statuses = await orderStatuses(owner, ORG_SLUG);
    report.flows.push({ flow: "paying an order from Orders needs no second factor and fulfils it", ok: !stepUp && statuses.includes("fulfilled"), stepUp, statuses });

    // --- the Super Admin ---------------------------------------------------------------
    const adminUrls = ["/admin", "/admin/users", "/admin/customers", "/admin/products", "/admin/plans", "/admin/orders",
      "/admin/subscriptions", "/admin/entitlements", "/admin/payments", "/admin/settings", "/admin/security", "/admin/audit"];
    for (const u of adminUrls) await visit(staff, u, `admin ${u}`);
    await staff.goto(`${BASE}/admin/users`, { waitUntil: "networkidle0" });
    const detail = await staff.$('main a[href^="/admin/users/"]');
    if (detail) {
      await Promise.all([detail.click(), staff.waitForNetworkIdle({ idleTime: 500, timeout: 15000 }).catch(() => {})]);
      await audit(staff, "admin /admin/users/<id>");
    }
    const segments = await staff.evaluate(async () => {
      const out = {};
      for (const s of ["explorers", "trial", "customers", "unverified"]) {
        const r = await fetch(`/api/v1/admin/users?segment=${s}`).then((x) => x.json());
        out[s] = r.results.map((u) => u.email).sort();
      }
      return out;
    });
    report.flows.push({
      flow: "users are segmented by what their organizations hold",
      ok: segments.customers.includes("nora@browser.test") && segments.trial.includes("customer@browser.test") && !segments.explorers.includes("nora@browser.test"),
      segments,
    });
    await reflow(browser, staff, adminUrls);
  } finally {
    await browser.close();
  }
  fs.writeFileSync(path.join(__dirname, "browser_report.json"), JSON.stringify(report, null, 2));
  const violations = report.pages.flatMap((p) => p.axe.map((v) => `${p.label}: ${v.id} (${v.impact}) x${v.nodes} ${v.sample || ""}`));
  console.log("PAGES", report.pages.length);
  report.pages.forEach((p) => console.log(`  ${p.label} [h1 ${JSON.stringify(p.h1)}] [tab stops ${p.tabbed}]`));
  const h1 = report.pages.filter((p) => p.h1.length !== 1).map((p) => `${p.label}: ${JSON.stringify(p.h1)}`);
  console.log("Pages without exactly one h1:", h1.length, h1);
  console.log("CSP violations:", report.csp.length, report.csp.slice(0, 5));
  console.log("Console errors:", report.errors.length, report.errors.slice(0, 10));
  console.log("Expected (the check's own refused requests):", report.expected.length, report.expected);
  console.log("Axe violations:", violations.length);
  violations.forEach((v) => console.log("  ", v));
  const nf = report.pages.filter((p) => p.noFocus.length).map((p) => `${p.label}: ${[...new Set(p.noFocus)].join(", ")}`);
  console.log("Elements without a visible focus indicator:", nf.length);
  nf.forEach((n) => console.log("  ", n));
  const bad = report.reflow.filter((r) => !r.ok);
  console.log("Pages with horizontal scroll at 320px:", bad.length, bad);
  console.log("Flows:");
  report.flows.forEach((f) => console.log(`  [${f.ok ? "PASS" : "FAIL"}] ${f.flow}`, f.ok ? "" : JSON.stringify(f)));
})().catch((e) => {
  console.error("FAILED", e);
  process.exit(1);
});
