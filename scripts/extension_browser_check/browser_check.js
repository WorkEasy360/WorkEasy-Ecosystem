// Real-browser verification of the Phase 8 extension (consoles, access policy,
// communication, payments) against the production build: CSP violations,
// console errors, axe (WCAG 2.1 A/AA incl. colour contrast), visible keyboard
// focus, reflow at 320px, the small-screen navigation drawer, and one
// end-to-end payment (Pay -> step-up -> hosted test page -> webhook -> paid).
// Needs ORG_SLUG and OWNER_TOTP from seed.py. Not part of the repository's CI.
const puppeteer = require("puppeteer-core");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const BASE = process.env.BASE || "http://127.0.0.1:3999";
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const AXE = fs.readFileSync(process.env.AXE_PATH, "utf8");
const PASSWORD = "Browser-Test-Password-1!";
const { ORG_SLUG, OWNER_TOTP } = process.env;

const report = { pages: [], csp: [], errors: [], reflow: [], flows: [] };

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

async function newPage(browser, width = 1280) {
  const page = await browser.newPage();
  await page.setViewport({ width, height: 900 });
  await page.evaluateOnNewDocument(() => {
    window.__csp = [];
    document.addEventListener("securitypolicyviolation", (e) =>
      window.__csp.push(`${e.violatedDirective} ${e.blockedURI} ${e.sourceFile || ""}`),
    );
  });
  page.on("console", (m) => {
    if (m.type() === "error") report.errors.push(`${page.url()} :: ${m.text()}`);
  });
  page.on("pageerror", (e) => report.errors.push(`${page.url()} :: pageerror ${e.message}`));
  return page;
}

async function login(browser, email) {
  const context = await browser.createBrowserContext();
  const page = await newPage(context);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle0" });
  await page.type('input[name="email"], input[type="email"]', email);
  await page.type('input[type="password"]', PASSWORD);
  await Promise.all([
    page.click('button[type="submit"]'),
    page.waitForNetworkIdle({ idleTime: 800, timeout: 20000 }).catch(() => {}),
  ]);
  const session = await page.evaluate(() => fetch("/api/v1/auth/session").then((r) => r.json()));
  if (!session.authenticated) throw new Error(`login failed for ${email}`);
  return page;
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
  const text = await page.evaluate(() => document.querySelector("main")?.innerText.slice(0, 160) || "");
  report.pages.push({ label, axe, tabbed, noFocus, text, h1 });
}

async function visit(page, url, label) {
  await page.goto(`${BASE}${url}`, { waitUntil: "networkidle0", timeout: 30000 });
  await page.waitForSelector("main", { timeout: 15000 }).catch(() => {});
  await new Promise((r) => setTimeout(r, 500));
  await audit(page, label);
}

async function reflow(browser, cookiesFrom, urls) {
  const page = await newPage(browser, 320);
  await page.setCookie(...(await cookiesFrom.cookies()));
  for (const url of urls) {
    await page.goto(`${BASE}${url}`, { waitUntil: "networkidle0", timeout: 30000 });
    await new Promise((r) => setTimeout(r, 300));
    const w = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
    report.reflow.push({ url, ...w, ok: w.scroll <= w.inner + 1 });
  }
  await page.close();
}

// The drawer below 900px: Menu opens it and moves focus inside, Escape closes
// it and returns focus to Menu, and following a link closes it.
async function drawer(browser, cookiesFrom, url) {
  const page = await newPage(browser, 360);
  await page.setCookie(...(await cookiesFrom.cookies()));
  await page.goto(`${BASE}${url}`, { waitUntil: "networkidle0", timeout: 30000 });
  const menu = await page.waitForSelector('button[aria-label="Menu"]');
  await menu.click();
  await new Promise((r) => setTimeout(r, 300)); // effects run after paint
  const opened = await page.evaluate(() => ({
    expanded: document.querySelector('button[aria-label="Menu"]').getAttribute("aria-expanded"),
    focusInNav: !!document.activeElement?.closest("nav"),
  }));
  await page.keyboard.press("Escape");
  const closed = await page.evaluate(() => ({
    expanded: document.querySelector('button[aria-label="Menu"]').getAttribute("aria-expanded"),
    focusOnMenu: document.activeElement?.getAttribute("aria-label") === "Menu",
  }));
  await menu.click();
  await new Promise((r) => setTimeout(r, 400)); // the drawer slides in
  await Promise.all([
    page.evaluate(() =>
      [...document.querySelectorAll("nav a")]
        .find((a) => a.getAttribute("aria-current") !== "page" && a.getBoundingClientRect().height > 0)
        ?.click(),
    ),
    page.waitForNetworkIdle({ idleTime: 500, timeout: 15000 }).catch(() => {}),
  ]);
  const afterNav = await page.evaluate(() => document.querySelector('button[aria-label="Menu"]').getAttribute("aria-expanded"));
  const ok = opened.expanded === "true" && opened.focusInNav && closed.expanded === "false" && closed.focusOnMenu && afterNav === "false";
  report.flows.push({ flow: `drawer ${url}`, ok, opened, closed, afterNav });
  await page.close();
}

async function payInvoice(owner) {
  await owner.goto(`${BASE}/o/${ORG_SLUG}/settings/billing`, { waitUntil: "networkidle0" });
  const pay = await owner.waitForSelector('button[aria-label^="Pay invoice"]', { timeout: 15000 });
  await pay.click();
  const code = await owner.waitForSelector('input[autocomplete="one-time-code"]', { timeout: 15000 });
  await audit(owner, "org settings/billing (step-up prompt)");
  await code.type(totp(OWNER_TOTP));
  await Promise.all([
    owner.waitForNavigation({ timeout: 60000 }),
    owner.click("::-p-text(Verify)"),
  ]);
  if (!owner.url().includes("/billing/test-checkout")) throw new Error(`not sent to the hosted page: ${owner.url()}`);
  await owner.waitForSelector("::-p-text(Pay (succeed))", { timeout: 15000 });
  await audit(owner, "billing/test-checkout (hosted test page)");
  await owner.click("::-p-text(Pay (succeed))");
  await owner.waitForNetworkIdle({ idleTime: 800, timeout: 20000 }).catch(() => {});
  // Back on the billing page as a provider would return the payer.
  const attempt = await owner.evaluate(async (slug) => {
    const r = await fetch(`/api/v1/orgs/${slug}/billing/invoices`).then((x) => x.json());
    return r.results.map((i) => i.status);
  }, ORG_SLUG);
  report.flows.push({ flow: "pay an invoice with step-up through the test provider", ok: attempt.includes("paid"), invoices: attempt });
}

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--no-sandbox", "--disable-gpu"] });
  try {
    // Signed out: the invitation page answers a missing link calmly.
    const anon = await newPage(await browser.createBrowserContext());
    await visit(anon, "/invitations/accept", "public /invitations/accept (no link)");

    const owner = await login(browser, "owner@browser.test");
    const landing = owner.url();
    report.flows.push({ flow: "an owner lands on the organization dashboard", ok: landing.endsWith(`/o/${ORG_SLUG}/dashboard`), landing });
    const orgUrls = [
      "dashboard", "apps", "marketplace", "settings/organization", "settings/billing", "settings/products",
      "settings/members", "settings/invitations", "settings/security", "settings/security/authentication",
      "settings/security/network", "settings/audit", "settings/sign-ins", "settings/profile",
    ].map((s) => `/o/${ORG_SLUG}/${s}`);
    for (const u of orgUrls) await visit(owner, u, `org ${u.replace(ORG_SLUG, "<slug>")}`);
    await reflow(browser, owner, orgUrls);
    await drawer(browser, owner, `/o/${ORG_SLUG}/dashboard`);

    const member = await login(browser, "member1@browser.test");
    report.flows.push({ flow: "a member lands on their apps", ok: member.url().endsWith(`/o/${ORG_SLUG}/apps`), landing: member.url() });
    await visit(member, `/o/${ORG_SLUG}/apps`, "member apps");

    const staff = await login(browser, "staff@browser.test");
    report.flows.push({ flow: "staff land on the Super Admin console", ok: staff.url().endsWith("/admin"), landing: staff.url() });
    const adminUrls = ["/admin", "/admin/users", "/admin/customers", "/admin/partners", "/admin/plans", "/admin/pricing",
      "/admin/payments", "/admin/commissions", "/admin/payouts", "/admin/products", "/admin/communication", "/admin/ai",
      "/admin/security", "/admin/audit", "/admin/diagnostics"];
    for (const u of adminUrls) await visit(staff, u, `admin ${u}`);
    // A user's detail page, reached the way staff reach it.
    await staff.goto(`${BASE}/admin/users`, { waitUntil: "networkidle0" });
    const detail = await staff.$('main a[href^="/admin/users/"]');
    if (detail) {
      await Promise.all([detail.click(), staff.waitForNetworkIdle({ idleTime: 500, timeout: 15000 }).catch(() => {})]);
      await audit(staff, "admin /admin/users/<id>");
      adminUrls.push(new URL(staff.url()).pathname);
    }
    await reflow(browser, staff, adminUrls);
    await drawer(browser, staff, "/admin");

    await payInvoice(owner);
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
  console.log("Console errors:", report.errors.length, report.errors.slice(0, 8));
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
