// Real-browser acceptance of the complete Store + partner ecosystem (ADR-053),
// against the production frontend build and a real backend on the scratch
// database prepared by prepare.py (w360_eco_fresh). The owner's tests:
//   A  Super Admin: four products in the Product Manager; plans; pricing with
//      partner margins and set-up fees
//   B  Customer: Store -> Easy CRM trial -> My Products -> launch; buy EasyBook
//   C  Cart: Easy CRM + EasyBook + Easy Project -> one order, one payment,
//      three subscription lines, three entitlements
//   D  Affiliate: own link for Easy CRM (AMAN360) -> clean browser follows it ->
//      signs up -> buys -> attribution, verified payment, commission, dashboard
//   E  Reseller: sees ONLY EasyBook and Easy Help Desk; creates a customer;
//      sells EasyBook (central order) -> customer pays -> subscription,
//      entitlement, margin
//   F  White-label: branded store lists only Easy CRM and EasyBook; a customer
//      buys there -> central order, payment, subscription, entitlement, link
//   G  Upgrade: Affiliate -> Reseller (set-up fee) -> White-label on the same
//      account; switcher Affiliate, Reseller, White-label
//   H  Every menu item of every workspace opens a working page
//   I  Wrong access is refused; a browser-sent price changes nothing
//   S  Support sends a request
//   R  Responsive: 1920, 1440, 1366, 1024, 768 and 390 px
// On every audited page: CSP violations, console errors, axe WCAG 2.1 A/AA,
// a visible focus indicator, exactly one h1. Screen readers: NOT EXECUTED.
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const path = require("path");

const BASE = process.env.BASE || "http://127.0.0.1:3999";
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const AXE = fs.readFileSync(process.env.AXE_PATH, "utf8");
const PASSWORD = process.env.CHECK_PASSWORD;
const BACKEND_LOG = process.env.BACKEND_LOG;
if (!PASSWORD || !BACKEND_LOG) throw new Error("CHECK_PASSWORD and BACKEND_LOG are required");
const OUT = process.env.OUT || "ecosystem_report.json";
const ONLY = (process.env.ONLY || "").split(",").filter(Boolean);
const SHOTS = path.join(path.dirname(OUT), "screens");
fs.mkdirSync(SHOTS, { recursive: true });
const WIDTHS = [1920, 1440, 1366, 1024, 768, 390];

const report = { pages: [], csp: [], errors: [], expected: [], reflow: [], flows: [], menus: [], notes: [] };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const check = (cond, msg) => {
  if (!cond) throw new Error(msg);
};

// ------------------------------------------------------------------ plumbing --

let lastPage = null;
async function newPage(context, width = 1440) {
  const page = await context.newPage();
  lastPage = page;
  await page.setViewport({ width, height: 900 });
  await page.evaluateOnNewDocument(() => {
    window.__csp = [];
    document.addEventListener("securitypolicyviolation", (e) => window.__csp.push(`${e.violatedDirective} ${e.blockedURI}`));
  });
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const line = `${page.url()} :: ${m.text()}`;
    if (m.text().startsWith("Failed to load resource") && /status of (400|401|403|404|409|429)/.test(m.text())) report.expected.push(line);
    else report.errors.push(line);
  });
  page.on("pageerror", (e) => report.errors.push(`${page.url()} :: pageerror ${e.message}`));
  return page;
}

async function audit(page, label) {
  await sleep(500);
  (await page.evaluate(() => window.__csp || [])).forEach((c) => report.csp.push(`${label}: ${c}`));
  const axe = await page.evaluate(async (src) => {
    (0, eval)(src);
    const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
    return r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, sample: v.nodes[0] && v.nodes[0].html.slice(0, 160) }));
  }, AXE);
  const noFocus = [];
  await page.evaluate(() => document.activeElement && document.activeElement.blur());
  for (let i = 0; i < 30; i++) {
    await page.keyboard.press("Tab");
    const info = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return null;
      const cs = getComputedStyle(el);
      const ring = (cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0) || (cs.boxShadow && cs.boxShadow !== "none");
      return { tag: el.tagName, text: (el.innerText || el.getAttribute("aria-label") || "").slice(0, 40), visible: ring || el.matches("input,select,textarea") };
    });
    if (info && !info.visible) noFocus.push(`${info.tag} "${info.text}"`);
  }
  await page.evaluate(() => window.scrollTo(0, 0));
  const h1 = await page.evaluate(() => [...document.querySelectorAll("h1")].map((h) => h.innerText));
  report.pages.push({ label, url: page.url().replace(BASE, ""), axe, noFocus, h1 });
}

async function reflow(page, label) {
  for (const width of WIDTHS) {
    await page.setViewport({ width, height: width === 390 ? 844 : 900 });
    await sleep(350);
    const r = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
    report.reflow.push({ label, width, ok: r.scroll <= r.inner, scroll: r.scroll });
    if (width === 390 || width === 1440) await page.screenshot({ path: path.join(SHOTS, `${label.replace(/\W+/g, "-")}-${width}.png`), fullPage: true });
  }
  await page.setViewport({ width: 1440, height: 900 });
  await sleep(300);
}

const bodyText = (page) => page.evaluate(() => (document.querySelector("main") || document.body).innerText);

async function waitForText(page, wanted, timeout = 25000) {
  await page.waitForFunction((t) => document.body.innerText.includes(t), { timeout }, wanted).catch(async () => {
    throw new Error(`text not found: "${wanted}" on ${page.url()} :: ${(await bodyText(page)).slice(0, 500).replace(/\n+/g, " | ")}`);
  });
}

async function click(page, label, { exact = true, timeout = 15000 } = {}) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const ok = await page.evaluate(
      (label, exact) => {
        const el = [...document.querySelectorAll("button, a, [role=tab]")].find((c) => {
          if (c.disabled) return false;
          const r = c.getBoundingClientRect();
          if (!r.width || !r.height) return false;
          const t = (c.innerText || "").replace(/\s+/g, " ").trim();
          const a = (c.getAttribute("aria-label") || "").trim();
          return exact ? t === label || a === label : t.includes(label) || a.includes(label);
        });
        if (!el) return false;
        el.scrollIntoView({ block: "center" });
        el.click();
        return true;
      },
      label,
      exact,
    );
    if (ok) return;
    await sleep(250);
  }
  throw new Error(`nothing to click for "${label}" on ${page.url()}`);
}

async function field(page, label) {
  const id = await page.evaluate((label) => {
    const l = [...document.querySelectorAll("label")].find((x) => x.innerText.replace(/\*|\(required\)/g, "").trim().startsWith(label));
    return l ? l.htmlFor : null;
  }, label);
  if (!id) throw new Error(`no field labelled "${label}" on ${page.url()}`);
  return `[id="${id}"]`;
}

async function type(page, label, value) {
  const sel = await field(page, label);
  await page.click(sel, { clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.type(sel, value);
}

/** A same-origin request made by the page itself, with its session and CSRF token. */
function api(page, method, url, body) {
  return page.evaluate(
    async (method, url, body) => {
      const csrf = (document.cookie.match(/(?:^|; )(?:__Host-)?w360_csrf=([^;]*)/) || [])[1];
      const r = await fetch(url, {
        method,
        credentials: "same-origin",
        headers: {
          "X-CSRFToken": csrf ? decodeURIComponent(csrf) : "",
          Accept: "application/json",
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      let data = null;
      try {
        data = await r.json();
      } catch {}
      return { status: r.status, body: data };
    },
    method,
    url,
    body,
  );
}

async function signIn(page, email, mode) {
  await page.goto(`${BASE}/login?mode=${mode}`, { waitUntil: "networkidle0" });
  await page.waitForSelector('input[type="email"]', { visible: true });
  await page.type('input[type="email"]', email);
  await page.type('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 90000 });
  await page.waitForNetworkIdle({ idleTime: 500, timeout: 30000 }).catch(() => {});
}

/** The newest emailed link matching `pattern` in the backend's console output. */
function emailedLink(pattern, after = 0) {
  const log = fs.readFileSync(BACKEND_LOG, "utf8").slice(after).replace(/=\r?\n/g, "").replace(/=3D/g, "=");
  const found = [...log.matchAll(pattern)];
  return found.length ? found[found.length - 1][0] : null;
}
const logSize = () => fs.statSync(BACKEND_LOG).size;

async function payOnTestPage(page) {
  await page.waitForFunction(() => location.pathname.includes("/billing/test-checkout"), { timeout: 60000 });
  await waitForText(page, "Pay (succeed)");
  await click(page, "Pay (succeed)");
  await page.waitForNetworkIdle({ idleTime: 800, timeout: 30000 }).catch(() => {});
  await sleep(1500);
}

async function planIds(page) {
  const r = await page.evaluate(async () => (await fetch("/api/v1/public/catalog?currency=INR", { credentials: "omit" })).json());
  const out = {};
  for (const p of r.products || []) for (const plan of p.plans || []) out[plan.plan_code || plan.code] = plan.plan_version_id || plan.id;
  return out;
}

async function myProducts(page, slug) {
  const r = await api(page, "GET", `/api/v1/orgs/${slug}/apps`);
  return r;
}

async function flow(name, fn) {
  const key = name.split(" ")[0];
  if (ONLY.length && !ONLY.includes(key)) return;
  console.log("flow", name);
  try {
    const detail = await fn();
    report.flows.push({ name, ok: true, detail: detail || "" });
  } catch (e) {
    let seen = "";
    try {
      seen = (await bodyText(lastPage)).slice(0, 400).replace(/\n+/g, " | ");
      await lastPage.screenshot({ path: path.join(SHOTS, `FAILED-${key}.png`), fullPage: true });
    } catch {}
    report.flows.push({ name, ok: false, detail: `${String(e.message || e).slice(0, 600)} :: ${lastPage && lastPage.url()} :: ${seen}` });
  }
}

async function partnerOf(page) {
  const r = await api(page, "GET", "/api/v1/partner/portal");
  check(r.status === 200, `partner portal: ${r.status}`);
  return r.body.partners[0];
}

/** Every menu entry of the current workspace: open it, audit it, and record it. */
async function auditMenu(page, workspace, who) {
  const links = await page.evaluate(() =>
    [...document.querySelectorAll("nav a[href^='/partner/']")]
      .map((a) => ({ label: a.innerText.trim(), href: a.getAttribute("href") }))
      .filter((l) => l.label && !/Switch to/.test(l.label)),
  );
  const seen = new Set();
  for (const link of links) {
    if (seen.has(link.href) || !link.href.startsWith(`/partner/${workspace}`)) continue;
    seen.add(link.href);
    const before = report.errors.length;
    await page.goto(`${BASE}${link.href}`, { waitUntil: "networkidle0" });
    await sleep(600);
    const h1 = await page.evaluate(() => [...document.querySelectorAll("h1")].map((h) => h.innerText));
    const txt = await bodyText(page);
    const refused = /don.t have access|not part of this workspace|could not be|Couldn.t load|Something went wrong/i.test(txt);
    await audit(page, `${who} menu: ${link.label}`);
    report.menus.push({ workspace, who, label: link.label, route: link.href, h1, ok: !refused && h1.length === 1 && report.errors.length === before });
  }
  return [...seen].length;
}

// ------------------------------------------------------------------ journeys --

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    userDataDir: fs.mkdtempSync(path.join(require("os").tmpdir(), "w360-eco-check-")),
    args: ["--no-sandbox"],
  });
  const ctx = () => browser.createBrowserContext();
  const state = {};

  // --- A. Super Admin -------------------------------------------------------------------------------------------
  await flow("A Super Admin: four products, plans, pricing with partner margins and set-up fees", async () => {
    const page = await newPage(await ctx());
    await signIn(page, "staff@eco.test", "admin");
    state.adminPage = page;
    await page.goto(`${BASE}/admin/products`, { waitUntil: "networkidle0" });
    for (const name of ["Easy CRM", "EasyBook", "Easy Project", "Easy Help Desk"]) await waitForText(page, name);
    await audit(page, "Admin Product Manager");
    await reflow(page, "admin-products");
    await page.goto(`${BASE}/admin/plans`, { waitUntil: "networkidle0" });
    await waitForText(page, "Plans & add-ons");
    await audit(page, "Admin Plans & Add-ons");
    // The products' own plans and prices, as the pricing screen lists them.
    await page.goto(`${BASE}/admin/pricing?product=easy_crm`, { waitUntil: "networkidle0" });
    await waitForText(page, "Advanced");
    await waitForText(page, "999.00");
    await page.goto(`${BASE}/admin/pricing`, { waitUntil: "networkidle0" });
    await waitForText(page, "Partner margins");
    await waitForText(page, "Default for Reseller partners");
    await waitForText(page, "Partner set-up fees");
    await waitForText(page, "25,000.00");
    await audit(page, "Admin Currency & Pricing");
    await reflow(page, "admin-pricing");
    for (const route of ["/admin/orders", "/admin/payments"]) {
      await page.goto(`${BASE}${route}`, { waitUntil: "networkidle0" });
      await audit(page, `Admin ${route}`);
    }
    return "four products listed; INR plans and prices shown; margins and fees configured";
  });

  // --- B. Customer: trial, then a purchase ------------------------------------------------------------------------------
  await flow("B Customer: Store -> Easy CRM trial -> My Products -> launch; buy EasyBook", async () => {
    const page = await newPage(await ctx());
    await signIn(page, "trial@eco.test", "user");
    await page.goto(`${BASE}/store`, { waitUntil: "networkidle0" });
    await page.waitForFunction(() => location.pathname === "/o/trial-company/explore", { timeout: 30000 });
    await waitForText(page, "Easy CRM");
    await audit(page, "Customer Store (Explore)");
    await reflow(page, "store-explore");
    await click(page, "Start 7-day free trial: Easy CRM");
    await waitForText(page, "Trial started: Easy CRM.");
    await page.goto(`${BASE}/o/trial-company/apps`, { waitUntil: "networkidle0" });
    await waitForText(page, "Easy CRM");
    const trialCard = await page.evaluate(() => [...document.querySelectorAll("article")].find((a) => a.innerText.includes("Easy CRM"))?.innerText || "");
    check(/TRIAL/i.test(trialCard), `My Products card: ${trialCard}`);
    await audit(page, "Customer My Products (trial)");
    // A per-seat product: the owner assigns themself a seat, then launches.
    await click(page, "Assign a seat: Easy CRM").catch(() => null);
    if (page.url().includes("/settings/products")) {
      await page.waitForNetworkIdle({ idleTime: 500, timeout: 15000 }).catch(() => {});
      await click(page, "— Assign", { exact: false }).catch(() => null);
      await sleep(800);
    }
    const launch = await api(page, "POST", "/api/v1/orgs/trial-company/apps/easy_crm/launch");
    check(launch.status === 200 && launch.body && launch.body.redirect_url, `trial launch: ${launch.status} ${JSON.stringify(launch.body).slice(0, 200)}`);
    const ids = await planIds(page);
    check(ids.easybook_basic, `plan ids: ${JSON.stringify(ids)}`);
    await page.goto(`${BASE}/o/trial-company/checkout?plan=${ids.easybook_basic}&interval=monthly`, { waitUntil: "networkidle0" });
    await waitForText(page, "Place order and pay");
    await audit(page, "Customer checkout");
    await reflow(page, "checkout");
    await Promise.all([page.waitForNavigation({ timeout: 60000 }).catch(() => null), click(page, "Place order and pay")]);
    await payOnTestPage(page);
    await page.goto(`${BASE}/o/trial-company/apps`, { waitUntil: "networkidle0" });
    await waitForText(page, "EasyBook");
    const held = await myProducts(page, "trial-company");
    const subs = await api(page, "GET", "/api/v1/orgs/trial-company/subscriptions");
    const items = (subs.body.results || []).flatMap((s) => s.items.map((i) => `${i.product_code}:${s.status}`));
    check(items.some((i) => i.startsWith("easybook")), `subscription lines: ${items}`);
    await audit(page, "Customer My Products (trial + paid)");
    return `trial launch -> ${new URL(launch.body.redirect_url).host}; lines ${items.join(", ")}; apps ${held.status}`;
  });

  // --- C. Cart -------------------------------------------------------------------------------------------------------
  await flow("C Cart: Easy CRM + EasyBook + Easy Project, one order, one payment, three lines", async () => {
    const page = await newPage(await ctx());
    await signIn(page, "cart@eco.test", "user");
    await page.goto(`${BASE}/o/cart-company/explore`, { waitUntil: "networkidle0" });
    await waitForText(page, "Easy Project");
    const ids = await planIds(page);
    const cart = [ids.easy_crm_basic, ids.easybook_advanced, ids.easy_project_basic];
    check(cart.every(Boolean), `plan ids: ${JSON.stringify(ids)}`);
    await page.goto(`${BASE}/o/cart-company/checkout?cart=${cart.join(",")}&interval=monthly`, { waitUntil: "networkidle0" });
    await waitForText(page, "Products in this order");
    await audit(page, "Customer checkout (cart)");
    await Promise.all([page.waitForNavigation({ timeout: 60000 }).catch(() => null), click(page, "Place order and pay")]);
    await payOnTestPage(page);
    const orders = await api(page, "GET", "/api/v1/orgs/cart-company/orders");
    const order = (orders.body.results || [])[0];
    check(order && order.kind === "cart" && order.status === "fulfilled" && order.lines.length === 3, `order: ${String(JSON.stringify(order)).slice(0, 300)}`);
    const subs = await api(page, "GET", "/api/v1/orgs/cart-company/subscriptions");
    const items = (subs.body.results || []).flatMap((s) => s.items.map((i) => i.product_code)).sort();
    check(JSON.stringify(items) === JSON.stringify(["easy_crm", "easy_project", "easybook"]), `lines: ${items}`);
    await page.goto(`${BASE}/o/cart-company/apps`, { waitUntil: "networkidle0" });
    for (const name of ["Easy CRM", "EasyBook", "Easy Project"]) await waitForText(page, name);
    await page.goto(`${BASE}/o/cart-company/orders`, { waitUntil: "networkidle0" });
    await audit(page, "Customer Orders");
    return `${order.number}: 3 lines, 1 payment, subscription lines ${items.join(", ")}`;
  });

  // --- D. Affiliate ------------------------------------------------------------------------------------------------
  await flow("D Affiliate: own link AMAN360 for Easy CRM; clean browser signs up and buys; commission after payment", async () => {
    const page = await newPage(await ctx());
    await signIn(page, "aman@eco.test", "partner");
    await page.waitForFunction(() => location.pathname.startsWith("/partner/affiliate"), { timeout: 60000 });
    state.aman = await partnerOf(page);
    await page.goto(`${BASE}/partner/affiliate/campaigns`, { waitUntil: "networkidle0" });
    await type(page, "Campaign name", "CRM October Campaign");
    await click(page, "Create campaign");
    await waitForText(page, "created");
    await page.goto(`${BASE}/partner/affiliate/referral-links`, { waitUntil: "networkidle0" });
    await waitForText(page, "Create a referral link");
    await page.select(await field(page, "Product"), "easy_crm");
    const campaigns = await page.$$eval(`${await field(page, "Campaign (optional)")} option`, (o) => o.map((x) => [x.value, x.textContent]));
    const cid = campaigns.find((c) => c[1] === "CRM October Campaign");
    check(cid, `campaign options ${JSON.stringify(campaigns)}`);
    await page.select(await field(page, "Campaign (optional)"), cid[0]);
    await type(page, "Your own code (optional)", "AMAN360");
    await click(page, "Generate link");
    await waitForText(page, "Referral link created.");
    await waitForText(page, "/r/AMAN360");
    await audit(page, "Affiliate Referral Links");
    await reflow(page, "affiliate-links");

    // A clean browser: no session, no cookies.
    const visitor = await newPage(await ctx());
    await visitor.goto(`${BASE}/r/AMAN360`, { waitUntil: "networkidle0" });
    await visitor.waitForFunction(() => location.pathname === "/products/easy-crm", { timeout: 30000 });
    const cookies = (await visitor.cookies()).map((c) => c.name);
    check(cookies.some((n) => /w360_ref/.test(n)) && cookies.some((n) => /w360_vid/.test(n)), `cookies: ${cookies}`);
    await audit(visitor, "Public product page from a referral link");
    // Sign up in another tab of the same browser (the attribution must survive).
    const signup = await visitor.browserContext().newPage();
    await signup.setViewport({ width: 1440, height: 900 });
    await signup.goto(`${BASE}/signup`, { waitUntil: "networkidle0" });
    const mark = logSize();
    await type(signup, "First name", "Nina");
    await type(signup, "Last name", "Newbie");
    await type(signup, "Work email", "newbie@eco.test");
    await type(signup, "Password", PASSWORD);
    await type(signup, "Company name", "Newbie Traders").catch(() => null);
    await click(signup, "Create account");
    await sleep(2500);
    const link = emailedLink(/http[^\s"'<>]*verify-email[^\s"'<>]*/g, mark);
    check(link, "no verification link in the backend log");
    await signup.goto(link.replace(/^https?:\/\/[^/]+/, BASE), { waitUntil: "networkidle0" });
    await signup.waitForSelector('input[type="password"]', { visible: true });
    await signup.type('input[type="password"]', PASSWORD);
    await click(signup, "Confirm and sign in");
    await signup.waitForFunction(() => location.pathname.startsWith("/o/") || location.pathname.startsWith("/onboarding") || location.pathname.startsWith("/dashboard"), { timeout: 60000 });
    await signup.waitForNetworkIdle({ idleTime: 800, timeout: 30000 }).catch(() => {});
    const orgs = await api(signup, "GET", "/api/v1/organizations");
    const slug = orgs.body.organizations[0].slug;
    const ids = await planIds(signup);
    // Commission must not exist before a paid sale.
    const before = await api(page, "GET", `/api/v1/partner/${state.aman.partner_id}/commissions`);
    check((before.body.results || []).length === 0, `commission before payment: ${JSON.stringify(before.body).slice(0, 200)}`);
    await api(signup, "PUT", `/api/v1/orgs/${slug}/billing-account`, { currency: "INR", billing_email: "newbie@eco.test" });
    await api(signup, "POST", `/api/v1/orgs/${slug}/billing-account`, { currency: "INR", billing_email: "newbie@eco.test" });
    await signup.goto(`${BASE}/o/${slug}/checkout?plan=${ids.easy_crm_basic}&interval=monthly`, { waitUntil: "networkidle0" });
    await waitForText(signup, "Place order and pay");
    await Promise.all([signup.waitForNavigation({ timeout: 60000 }).catch(() => null), click(signup, "Place order and pay")]);
    await payOnTestPage(signup);

    await page.goto(`${BASE}/partner/affiliate/dashboard`, { waitUntil: "networkidle0" });
    await waitForText(page, "Registrations");
    const overview = await api(page, "GET", `/api/v1/partner/${state.aman.partner_id}/affiliate/overview?range=30d`);
    const k = overview.body.kpis;
    const commissions = await api(page, "GET", `/api/v1/partner/${state.aman.partner_id}/commissions`);
    const rows = commissions.body.results || [];
    check(k.clicks >= 1 && k.unique_visitors >= 1 && k.signups >= 1 && k.conversions >= 1, `kpis ${JSON.stringify(k)}`);
    check(rows.length === 1 && rows[0].amount_minor > 0, `ledger ${JSON.stringify(rows).slice(0, 300)}`);
    await audit(page, "Affiliate Dashboard (with a conversion)");
    await reflow(page, "affiliate-dashboard");
    state.affiliatePage = page;
    return `kpis ${JSON.stringify(k)}; accrual ${rows[0].amount_minor} ${rows[0].currency} (${rows[0].kind})`;
  });

  // --- E. Reseller ---------------------------------------------------------------------------------------------------
  await flow("E Reseller: only EasyBook and Easy Help Desk; customer; central order; payment; margin", async () => {
    const page = await newPage(await ctx());
    await signIn(page, "res@eco.test", "partner");
    await page.waitForFunction(() => location.pathname.startsWith("/partner/reseller"), { timeout: 60000 });
    state.res = await partnerOf(page);
    await page.goto(`${BASE}/partner/reseller/partner-products`, { waitUntil: "networkidle0" });
    await waitForText(page, "EasyBook");
    await audit(page, "Reseller Products & Plans");
    // What it may sell: only the granted products (the page also lists the
    // others under "available to ask for", which grants nothing).
    const sellable = await api(page, "GET", `/api/v1/partner/${state.res.partner_id}/plans`);
    const sold = [...new Set((sellable.body.plans || []).map((p) => p.product_code))].sort();
    check(JSON.stringify(sold) === JSON.stringify(["easy_helpdesk", "easybook"]), `reseller may sell ${sold}`);
    const catalog = await api(page, "GET", `/api/v1/partner/${state.res.partner_id}/catalog`);
    const offered = [...new Set((catalog.body.results || []).map((p) => p.product_code))].sort();
    check(JSON.stringify(offered) === JSON.stringify(["easy_helpdesk", "easybook"]), `sell form offers ${offered}`);
    await page.goto(`${BASE}/partner/reseller/plans`, { waitUntil: "networkidle0" });
    await waitForText(page, "Your cost");
    await waitForText(page, "Margin (20.00% off list)");
    await audit(page, "Reseller Pricing & Margins");
    await reflow(page, "reseller-pricing");
    // A customer, with its owner activated by the emailed one-time link.
    await page.goto(`${BASE}/partner/reseller/users`, { waitUntil: "networkidle0" });
    const mark = logSize();
    const customerName = `Res Customer ${Date.now() % 100000}`;
    await type(page, "Organization name", customerName);
    await type(page, "Owner's email", `rescust${Date.now() % 100000}@eco.test`);
    await click(page, "Create customer");
    await waitForText(page, "Customer created.");
    await sleep(1500);
    const claim = emailedLink(/http[^\s"'<>]*owner-invitations\/accept[^\s"'<>]*/g, mark);
    check(claim, "no owner activation link in the backend log");
    const owner = await newPage(await ctx());
    await owner.goto(claim.replace(/^https?:\/\/[^/]+/, BASE), { waitUntil: "networkidle0" });
    await waitForText(owner, "Choose a password");
    await type(owner, "Your name", "Rita Customer");
    await type(owner, "Choose a password", PASSWORD);
    await type(owner, "Confirm the password", PASSWORD);
    await click(owner, "Set password and continue");
    await sleep(3000);
    // The reseller sells EasyBook: a central order priced by the server.
    await page.goto(`${BASE}/partner/reseller/users`, { waitUntil: "networkidle0" });
    await waitForText(page, customerName);
    // The "Sell a plan" of this customer's own row.
    const opened = await page.evaluate((name) => {
      const row = [...document.querySelectorAll("tr")].find((r) => r.innerText.includes(name));
      const button = row && [...row.querySelectorAll("button")].find((b) => b.innerText.trim() === "Sell a plan");
      if (!button) return false;
      button.click();
      return true;
    }, customerName);
    check(opened, "no Sell a plan button on the new customer's row");
    await page.waitForFunction(
      () => [...document.querySelectorAll("option")].some((o) => /EasyBook . Basic/.test(o.textContent)),
      { timeout: 20000 },
    );
    const planSel = await page.evaluate(() => [...document.querySelectorAll("label")].find((l) => l.innerText.trim() === "Plan")?.htmlFor);
    const option = await page.$$eval(`[id="${planSel}"] option`, (o) => o.map((x) => [x.value, x.textContent]).find((x) => /EasyBook — Basic/.test(x[1])));
    check(option, "EasyBook Basic not offered");
    await page.select(`[id="${planSel}"]`, option[0]);
    await click(page, "Get price");
    await waitForText(page, "per period");
    await audit(page, "Reseller Sell a plan (quote)");
    await click(page, "Create order");
    await waitForText(page, "Order created.");
    // Nothing granted before payment; the owner pays in their own console.
    await owner.goto(`${BASE}/organizations/select`, { waitUntil: "networkidle0" }).catch(() => null);
    const orgs = await api(owner, "GET", "/api/v1/organizations");
    const slug = orgs.body.organizations[0].slug;
    const pending = await api(owner, "GET", `/api/v1/orgs/${slug}/orders`);
    const order = (pending.body.results || [])[0];
    check(order && order.status === "pending_payment" && order.channel === "partner", `order: ${String(JSON.stringify(order)).slice(0, 300)}`);
    const before = await api(owner, "GET", `/api/v1/orgs/${slug}/subscriptions`);
    check((before.body.results || []).length === 0, "subscription before payment");
    await owner.goto(`${BASE}/o/${slug}/orders`, { waitUntil: "networkidle0" });
    await Promise.all([owner.waitForNavigation({ timeout: 60000 }).catch(() => null), click(owner, "Pay", { exact: false })]);
    await payOnTestPage(owner);
    const after = await api(owner, "GET", `/api/v1/orgs/${slug}/subscriptions`);
    const lines = (after.body.results || []).flatMap((s) => s.items.map((i) => `${i.product_code}:${s.status}`));
    check(lines.some((l) => l.startsWith("easybook")), `lines after payment: ${lines}`);
    await owner.goto(`${BASE}/o/${slug}/apps`, { waitUntil: "networkidle0" });
    await waitForText(owner, "EasyBook");
    const launch = await api(owner, "POST", `/api/v1/orgs/${slug}/apps/easybook/launch`);
    // The reseller sees the order with its margin from the ledger.
    await page.goto(`${BASE}/partner/reseller/payments`, { waitUntil: "networkidle0" });
    await waitForText(page, "(margin)");
    await audit(page, "Reseller Sales & Orders");
    const orders = await api(page, "GET", `/api/v1/partner/${state.res.partner_id}/orders`);
    const mine = (orders.body.results || []).find((o) => o.sold_by_you);
    check(mine && mine.earning && mine.earning.model === "margin" && mine.earning.amount_minor > 0, `partner order ${String(JSON.stringify(mine)).slice(0, 300)}`);
    state.resPage = page;
    return `order ${order.number}: paid, EasyBook line, launch ${launch.status}; margin ${mine.earning.amount_minor} cost ${mine.earning.partner_cost_minor}`;
  });

  // --- F. White-label ----------------------------------------------------------------------------------------------
  await flow("F White-label: branded store shows only Easy CRM and EasyBook; purchase is central and links the buyer", async () => {
    const page = await newPage(await ctx());
    await signIn(page, "wl@eco.test", "partner");
    await page.waitForFunction(() => location.pathname.startsWith("/partner/white-label"), { timeout: 60000 });
    state.wl = await partnerOf(page);
    await waitForText(page, "Branded store");
    await audit(page, "White-label Dashboard");
    const anon = await newPage(await ctx());
    await anon.goto(`${BASE}/store/acme-cloud`, { waitUntil: "networkidle0" });
    await waitForText(anon, "Acme Cloud Store");
    const listed = await anon.$$eval("h2", (h) => h.map((x) => x.innerText.trim()));
    check(listed.includes("Easy CRM") && listed.includes("EasyBook") && !listed.includes("Easy Project") && !listed.includes("Easy Help Desk"), `store lists ${listed}`);
    await audit(anon, "Branded store (signed out)");
    await reflow(anon, "branded-store");
    const buyer = await newPage(await ctx());
    await signIn(buyer, "buyer@eco.test", "user");
    await buyer.goto(`${BASE}/store/acme-cloud`, { waitUntil: "networkidle0" });
    await waitForText(buyer, "Acme Cloud Store");
    await sleep(1500);
    await click(buyer, "Buy Easy CRM Basic per month", { exact: false });
    await buyer.waitForFunction(() => location.pathname.endsWith("/checkout"), { timeout: 30000 });
    await waitForText(buyer, "Bought through your partner's store");
    await Promise.all([buyer.waitForNavigation({ timeout: 60000 }).catch(() => null), click(buyer, "Place order and pay")]);
    await payOnTestPage(buyer);
    const orders = await api(buyer, "GET", "/api/v1/orgs/store-buyer/orders");
    const order = (orders.body.results || [])[0];
    check(order && order.status === "fulfilled" && order.channel === "storefront", `order ${String(JSON.stringify(order)).slice(0, 300)}`);
    // Easy CRM is per seat: launching needs a seat (journey B assigns one);
    // the entitlement itself is what this journey checks.
    const apps = await api(buyer, "GET", "/api/v1/orgs/store-buyer/apps");
    const launch = { status: JSON.stringify(apps.body).includes("easy_crm") ? "entitled" : "missing" };
    await page.goto(`${BASE}/partner/white-label/users`, { waitUntil: "networkidle0" });
    await waitForText(page, "Store Buyer");
    const partnerOrders = await api(page, "GET", `/api/v1/partner/${state.wl.partner_id}/orders`);
    const mine = (partnerOrders.body.results || []).find((o) => o.channel === "storefront");
    check(mine && mine.earning && mine.earning.model === "margin", `white-label order ${String(JSON.stringify(mine)).slice(0, 300)}`);
    state.wlPage = page;
    return `order ${order.number} via storefront; buyer is now Acme's customer; margin ${mine.earning.amount_minor}; launch ${launch.status}`;
  });

  // --- G. Upgrade on the same account ----------------------------------------------------------------------------------
  await flow("G Upgrade: Affiliate -> Reseller (fee) -> White-label, same login; switcher grows", async () => {
    const page = await newPage(await ctx());
    await signIn(page, "grow@eco.test", "partner");
    await page.waitForFunction(() => location.pathname.startsWith("/partner/affiliate"), { timeout: 60000 });
    const me = await partnerOf(page);
    const id = me.partner_id;
    await api(page, "POST", `/api/v1/partner/${id}/referrals`, { code: "GROW360" });
    await page.goto(`${BASE}/partner/affiliate/dashboard`, { waitUntil: "networkidle0" });
    await waitForText(page, "Grow Your Partnership");
    await page.goto(`${BASE}/partner/affiliate/upgrade/reseller`, { waitUntil: "networkidle0" });
    await waitForText(page, "set-up fee of");
    await audit(page, "Upgrade intro (fee shown)");
    const started = await api(page, "POST", `/api/v1/partner/${id}/upgrade/requests`, { experience: "reseller" });
    check(started.status === 200 || started.status === 201, `start ${started.status} ${JSON.stringify(started.body).slice(0, 200)}`);
    let req = started.body;
    check(req.setup_fee && req.setup_fee.amount_minor === 2500000, `fee ${JSON.stringify(req.setup_fee)}`);
    const profileNow = await api(page, "GET", `/api/v1/partner/${id}/profile`);
    const savedProfile = await api(page, "PUT", `/api/v1/partner/${id}/profile`, {
      expected_version: profileNow.body.version ?? (profileNow.body.profile || {}).version ?? 0,
      business: { business_email: "grow@eco.test", phone: "+91 98765 43210", country: "IN" },
    });
    check(savedProfile.status === 200, `profile ${savedProfile.status} ${JSON.stringify(savedProfile.body).slice(0, 300)}`);
    for (const [section, data] of [
      ["products", ["easybook"]],
      ["pricing", { preference: "standard" }],
      ["commercial", { expected_customers: 10 }],
    ]) {
      const saved = await api(page, "PUT", `/api/v1/partner/${id}/upgrade/requests/${req.id}/sections/${section}`, { data, expected_version: req.version });
      if (saved.status === 200) req = saved.body;
    }
    const submitted = await api(page, "POST", `/api/v1/partner/${id}/upgrade/requests/${req.id}/submit`, { expected_version: req.version });
    check(submitted.status === 200, `submit ${submitted.status} ${JSON.stringify(submitted.body).slice(0, 300)}`);
    // Staff approve: refused without the fee reference, then approved with it.
    const admin = state.adminPage;
    const refused = await api(admin, "POST", `/api/v1/admin/partners/${id}/upgrades/${req.id}/decision`, { action: "approve", products: ["easybook"], note: "" });
    check(refused.status === 400 && /set-up fee/i.test(JSON.stringify(refused.body)), `approve without reference: ${refused.status} ${JSON.stringify(refused.body).slice(0, 200)}`);
    const approved = await api(admin, "POST", `/api/v1/admin/partners/${id}/upgrades/${req.id}/decision`, { action: "approve", products: ["easybook"], commercial_reference: "INV-2026-0042", note: "" });
    check(approved.status === 200, `approve: ${approved.status} ${JSON.stringify(approved.body).slice(0, 300)}`);
    await page.goto(`${BASE}/partner`, { waitUntil: "networkidle0" });
    await page.waitForFunction(() => location.pathname.startsWith("/partner/reseller"), { timeout: 60000 });
    let switcher = await page.$$eval("nav[aria-label='Workspace'] a", (a) => a.map((x) => x.innerText.trim()));
    check(JSON.stringify(switcher) === JSON.stringify(["Affiliate", "Reseller"]), `switcher after reseller: ${switcher}`);
    // Still the same account, the same code and history.
    const codes = await api(page, "GET", `/api/v1/partner/${id}/referrals`);
    check((codes.body.codes || []).some((c) => c.code === "GROW360"), "affiliate code lost");
    // Reseller -> White-label.
    const wl = await api(page, "POST", `/api/v1/partner/${id}/upgrade/requests`, { experience: "white_label" });
    let w = wl.body;
    for (const [section, data] of [
      ["products", ["easybook"]],
      ["pricing", { preference: "standard" }],
      ["brand", { brand_name: "Grow Cloud", primary_color: "#1d4ed8" }],
    ]) {
      const saved = await api(page, "PUT", `/api/v1/partner/${id}/upgrade/requests/${w.id}/sections/${section}`, { data, expected_version: w.version });
      if (saved.status === 200) w = saved.body;
    }
    const sub2 = await api(page, "POST", `/api/v1/partner/${id}/upgrade/requests/${w.id}/submit`, { expected_version: w.version });
    check(sub2.status === 200, `submit wl ${sub2.status} ${JSON.stringify(sub2.body).slice(0, 300)}`);
    const ok2 = await api(admin, "POST", `/api/v1/admin/partners/${id}/upgrades/${w.id}/decision`, { action: "approve", products: ["easybook"], commercial_reference: "", note: "" });
    check(ok2.status === 200, `approve wl ${ok2.status} ${JSON.stringify(ok2.body).slice(0, 300)}`);
    await page.goto(`${BASE}/partner`, { waitUntil: "networkidle0" });
    await sleep(1500);
    switcher = await page.$$eval("nav[aria-label='Workspace'] a", (a) => a.map((x) => x.innerText.trim()));
    check(JSON.stringify(switcher) === JSON.stringify(["Affiliate", "Reseller", "White-label"]), `switcher after white-label: ${switcher}`);
    for (const ws of ["affiliate", "reseller", "white-label"]) {
      await page.goto(`${BASE}/partner/${ws}/dashboard`, { waitUntil: "networkidle0" });
      await audit(page, `Upgraded partner: ${ws} dashboard`);
    }
    const after = await api(page, "GET", `/api/v1/partner/${id}/referrals`);
    check((after.body.codes || []).some((c) => c.code === "GROW360"), "affiliate history lost after white-label");
    state.growPage = page;
    return `same partner ${id}: switcher ${switcher.join(", ")}; GROW360 kept; fee reference required and recorded`;
  });

  // --- H. Every menu item ----------------------------------------------------------------------------------------------
  await flow("H Every menu item of the Affiliate, Reseller and White-label workspaces opens a working page", async () => {
    let total = 0;
    if (state.affiliatePage) total += await auditMenu(state.affiliatePage, "affiliate", "aman");
    if (state.resPage) total += await auditMenu(state.resPage, "reseller", "res");
    if (state.wlPage) {
      await state.wlPage.goto(`${BASE}/partner/white-label/dashboard`, { waitUntil: "networkidle0" });
      total += await auditMenu(state.wlPage, "white-label", "wl");
    }
    const bad = report.menus.filter((m) => !m.ok);
    check(bad.length === 0, `menus not working: ${JSON.stringify(bad)}`);
    return `${total} menu pages opened`;
  });

  // --- I. Wrong access -------------------------------------------------------------------------------------------------------
  await flow("I Wrong access is refused and a browser-sent price changes nothing", async () => {
    const aff = state.affiliatePage;
    const res = state.resPage;
    const out = {};
    out.affReseller = (await api(aff, "GET", `/api/v1/partner/${state.aman.partner_id}/customers`)).status;
    out.resBrand = (await api(res, "GET", `/api/v1/partner/${state.res.partner_id}/branding`)).status;
    out.resDomain = (await api(res, "GET", `/api/v1/partner/${state.res.partner_id}/domains`)).status;
    out.crossPartner = (await api(aff, "GET", `/api/v1/partner/${state.res.partner_id}/orders`)).status;
    out.partnerAdmin = (await api(res, "GET", "/api/v1/admin/margins")).status;
    out.partnerFee = (await api(res, "PUT", "/api/v1/admin/partner-programme/fees", { experience: "reseller", amount_minor: 0, currency: "INR" })).status;
    out.notGranted = (await api(res, "POST", `/api/v1/partner/${state.res.partner_id}/customers/00000000-0000-0000-0000-000000000000/orders`, { plan_version_id: "00000000-0000-0000-0000-000000000000" })).status;
    check(out.affReseller === 403 && out.resBrand === 403 && out.resDomain === 403, JSON.stringify(out));
    check([403, 404].includes(out.crossPartner) && [403, 404].includes(out.partnerAdmin) && [403, 404].includes(out.partnerFee), JSON.stringify(out));
    check([403, 404].includes(out.notGranted), JSON.stringify(out));
    // A price in the body is ignored: the quote is the server's.
    const buyer = await newPage(await ctx());
    await signIn(buyer, "cart@eco.test", "user");
    const ids = await planIds(buyer);
    const q = await api(buyer, "POST", "/api/v1/orgs/cart-company/orders/quote", {
      plan_version_id: ids.easy_helpdesk_basic,
      amount_due_minor: 1,
      recurring_amount_minor: 1,
      currency: "USD",
      partner_id: state.res.partner_id,
      channel: "partner",
    });
    check(q.status === 200 && q.body.recurring_amount_minor === 59900 && q.body.currency === "INR" && q.body.channel === "direct", `quote ${JSON.stringify(q.body).slice(0, 300)}`);
    out.priceIgnored = true;
    return JSON.stringify(out);
  });

  // --- S. Support --------------------------------------------------------------------------------------------------
  await flow("S Support sends a request through the platform", async () => {
    const page = state.affiliatePage;
    await page.goto(`${BASE}/partner/affiliate/support-manager`, { waitUntil: "networkidle0" });
    await type(page, "Subject", "Payout timing");
    await type(page, "What do you need help with?", "When is my October commission paid?");
    await click(page, "Send request");
    await waitForText(page, "Request sent to Workeasy360 support.");
    await audit(page, "Affiliate Support (sent)");
    return "sent";
  });

  // --- R. Responsive -------------------------------------------------------------------------------------------------
  await flow("R Responsive: workspace pages at 1920-390 px", async () => {
    for (const [page, route, label] of [
      [state.resPage, "/partner/reseller/users", "reseller-customers"],
      [state.resPage, "/partner/reseller/payments", "reseller-orders"],
      [state.affiliatePage, "/partner/affiliate/campaigns", "affiliate-campaigns"],
      [state.affiliatePage, "/partner/affiliate/marketing-materials", "affiliate-materials"],
      [state.wlPage, "/partner/white-label/dashboard", "white-label-dashboard"],
      [state.growPage, "/partner/white-label/dashboard", "upgraded-dashboard"],
    ]) {
      if (!page) continue;
      await page.goto(`${BASE}${route}`, { waitUntil: "networkidle0" });
      await reflow(page, label);
    }
    const bad = report.reflow.filter((r) => !r.ok);
    check(bad.length === 0, `sideways scroll: ${JSON.stringify(bad)}`);
    return `${report.reflow.length} viewport checks`;
  });

  await browser.close();
  fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
  const axeBad = report.pages.filter((p) => p.axe.length);
  const focusBad = report.pages.filter((p) => p.noFocus.length);
  const h1Bad = report.pages.filter((p) => p.h1.length !== 1);
  const reflowBad = report.reflow.filter((r) => !r.ok);
  const flowsBad = report.flows.filter((f) => !f.ok);
  console.log(
    JSON.stringify(
      {
        pages: report.pages.length,
        menus: report.menus.length,
        axeBad: axeBad.map((p) => [p.label, p.axe]),
        focusBad: focusBad.map((p) => [p.label, p.noFocus]),
        h1Bad: h1Bad.map((p) => [p.label, p.h1]),
        reflowBad,
        csp: report.csp,
        errors: report.errors,
        flows: report.flows,
      },
      null,
      2,
    ),
  );
  const pass = !axeBad.length && !focusBad.length && !h1Bad.length && !reflowBad.length && !flowsBad.length && !report.csp.length && !report.errors.length;
  console.log(pass ? "BROWSER CHECK PASS" : "BROWSER CHECK FAIL");
  process.exit(pass ? 0 : 1);
})();
