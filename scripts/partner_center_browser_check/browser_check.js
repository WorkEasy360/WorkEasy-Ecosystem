// Real-browser verification of the Partner Control Center and the
// Administrator / User sign-in modes, against the production build and a real
// backend. Same harness and rules as ../phase10_browser_check:
//   - CSP violations and console errors on every page;
//   - axe WCAG 2.1 A/AA, a visible keyboard focus indicator, exactly one h1;
//   - reflow (no horizontal scroll) at 1440, 1024, 768, 390 and 320 px;
// and the brief's journeys, driven through the UI:
//   A. Administrator: sign in as Administrator (keyboard), second factor, Admin
//      dashboard, Partners, the Partner dashboard, Resellers, a reseller's page
//   B. A normal customer chooses Administrator: refused, nothing from the
//      console shown, "Continue as User" works
//   C. A normal customer signs in as User: organization dashboard, My products
//   D. An administrator signs in as User: their own organization, not the console
//   E. One partner with reseller, white-label and affiliate capabilities: all
//      three labels and their tabs on one page, one row in every list
// plus the deep link (/admin/... while signed out) and the old addresses.
// Not part of CI. A manual screen-reader pass is NOT part of it: it is reported
// as NOT EXECUTED.
const puppeteer = require("puppeteer-core");
const crypto = require("crypto");
const fs = require("fs");

const BASE = process.env.BASE || "http://127.0.0.1:3999";
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const AXE = fs.readFileSync(process.env.AXE_PATH, "utf8");
const PASSWORD = "Browser-Test-Password-1!";
const { STAFF_TOTP, RESELLER_ID, AFFILIATE_ID, COMBINED_ID, CUSTOMER_ORG, STAFF_ORG, OUT } = process.env;
const WIDTHS = [1440, 1024, 768, 390, 320];

const report = { pages: [], csp: [], errors: [], expected: [], reflow: [], flows: [], notes: [] };
// Inside a journey the console legitimately answers 401/403 (a customer asking
// for the Admin Console, a password-only staff session before its second
// factor): the browser logs each as "Failed to load resource". They are listed
// as expected; on the audited pages every console error still counts.
let inJourney = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------------ plumbing --

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
    const refusal = /status of (401|403)/.test(m.text());
    if (m.text().startsWith("Failed to load resource") && inJourney > 0 && refusal) report.expected.push(line);
    else report.errors.push(line);
  });
  page.on("pageerror", (e) => report.errors.push(`${page.url()} :: pageerror ${e.message}`));
  return page;
}

async function audit(page, label) {
  const csp = await page.evaluate(() => window.__csp || []);
  csp.forEach((c) => report.csp.push(`${label}: ${c}`));
  const axe = await page.evaluate(async (src) => {
    (0, eval)(src); // Runtime.evaluate is not subject to the page CSP
    const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
    return r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, sample: v.nodes[0] && v.nodes[0].html.slice(0, 160) }));
  }, AXE);
  const noFocus = [];
  await page.evaluate(() => document.activeElement && document.activeElement.blur());
  let tabbed = 0;
  for (let i = 0; i < 45; i++) {
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
  report.pages.push({ label, url: page.url().replace(BASE, ""), axe, tabbed, noFocus, h1 });
}

async function visit(page, url, label, { auditIt = true } = {}) {
  console.log("visit", label);
  await page.goto(`${BASE}${url}`, { waitUntil: "networkidle0", timeout: 30000 });
  await page.waitForSelector("main", { timeout: 15000 }).catch(() => {});
  await sleep(400);
  if (auditIt) await audit(page, label);
}

async function reflow(browser, cookiesFrom, urls) {
  for (const width of WIDTHS) {
    const page = await newPage(browser, width);
    if (cookiesFrom) await page.setCookie(...(await cookiesFrom.cookies()));
    for (const url of urls) {
      await page.goto(`${BASE}${url}`, { waitUntil: "networkidle0", timeout: 30000 });
      await sleep(250);
      const w = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
      report.reflow.push({ width, url, ...w, ok: w.scroll <= w.inner + 1 });
    }
    await page.close();
  }
}

const bodyText = (page) => page.evaluate(() => document.body.innerText);

async function waitForText(page, text, timeout = 15000) {
  await page.waitForFunction((t) => document.body.innerText.includes(t), { timeout }, text).catch(async () => {
    const shown = (await bodyText(page)).slice(0, 400).replace(/\n+/g, " | ");
    throw new Error(`text not found: "${text}" on ${page.url()} :: ${shown}`);
  });
}

async function waitForPath(page, test, timeout = 20000) {
  await page.waitForFunction(test, { timeout }).catch(() => {
    throw new Error(`unexpected address ${page.url()}`);
  });
  await page.waitForNetworkIdle({ idleTime: 500, timeout: 15000 }).catch(() => {});
}

// Click the first visible button/link/tab whose text or aria-label matches.
async function clickText(page, text, { exact = false, within = "body", timeout = 8000 } = {}) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const clicked = await page.evaluate(
      (text, exact, within) => {
        const root = document.querySelector(within) || document.body;
        const candidates = [...root.querySelectorAll("button, a, [role=tab]")];
        const el = candidates.find((c) => {
          if (c.disabled || c.getAttribute("aria-disabled") === "true") return false;
          const r = c.getBoundingClientRect();
          if (r.width === 0 || r.height === 0) return false;
          const t = (c.innerText || "").replace(/\s+/g, " ").trim();
          const a = (c.getAttribute("aria-label") || "").trim();
          return exact ? t === text || a === text : t.includes(text) || a.includes(text);
        });
        if (!el) return false;
        el.scrollIntoView({ block: "center" });
        el.click();
        return true;
      },
      text,
      exact,
      within,
    );
    if (clicked) return;
    await sleep(200);
  }
  const shown = (await bodyText(page)).slice(0, 300).replace(/\n+/g, " | ");
  throw new Error(`nothing to click for "${text}" on ${page.url()} :: ${shown}`);
}

// ------------------------------------------------------------------ TOTP --

function base32(input) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of input.replace(/=+$/, "").toUpperCase()) bits += alphabet.indexOf(c).toString(2).padStart(5, "0");
  const bytes = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(bytes);
}

function totp(secret, at = Date.now()) {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(at / 1000 / 30)));
  const mac = crypto.createHmac("sha1", base32(secret)).update(counter).digest();
  const offset = mac[mac.length - 1] & 0xf;
  const value = (mac.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
  return String(value).padStart(6, "0");
}

// The seed enrolled the authenticator moments before: that window is used.
const lastWindow = { [STAFF_TOTP]: Math.floor(Date.now() / 30000) };
async function freshCode(secret) {
  const window = () => Math.floor(Date.now() / 30000);
  while (lastWindow[secret] === window()) await sleep(1000);
  lastWindow[secret] = window();
  return totp(secret);
}

async function answerPrompt(page, secret, wait = 8000) {
  const selector = 'input[autocomplete="one-time-code"]';
  const shown = await page.waitForSelector(selector, { timeout: wait, visible: true }).then(() => true).catch(() => false);
  if (!shown) return false;
  await page.type(selector, await freshCode(secret));
  await clickText(page, "Verify");
  await page.waitForFunction((s) => !document.querySelector(s), { timeout: 15000 }, selector).catch(() => {});
  return true;
}

// ------------------------------------------------------------------ sign-in --

// Sign in through the page: choose a mode on /login (by keyboard: Tab to the
// choice, Enter), then the form. Returns what the address bar showed.
async function signInAs(page, mode, email, { viaKeyboard = true } = {}) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle0" });
  await page.waitForFunction(() => document.body.innerText.includes("How would you like to sign in?"), { timeout: 15000 });
  const label = mode === "admin" ? "Administrator" : "User";
  if (viaKeyboard) {
    let reached = false;
    for (let i = 0; i < 15 && !reached; i++) {
      await page.keyboard.press("Tab");
      reached = await page.evaluate((l) => document.activeElement && document.activeElement.getAttribute("aria-label") === l, label);
    }
    if (!reached) throw new Error(`the ${label} choice is not reachable by keyboard`);
    await Promise.all([page.waitForFunction((m) => location.search.includes(`mode=${m}`), {}, mode), page.keyboard.press("Enter")]);
  } else {
    await clickText(page, label, { exact: true });
  }
  await page.waitForSelector('input[type="email"]', { visible: true, timeout: 15000 });
  await page.type('input[type="email"]', email);
  await page.type('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 20000 });
  await page.waitForNetworkIdle({ idleTime: 600, timeout: 15000 }).catch(() => {});
}

// What the browser stored, besides cookies the server set: nothing about a mode or a role.
async function storageAfterSignIn(page) {
  return page.evaluate(() => ({
    local: Object.keys(localStorage),
    session: Object.keys(sessionStorage),
    cookies: document.cookie.split("; ").map((c) => c.split("=")[0]),
  }));
}

async function flow(name, fn) {
  console.log("FLOW", name);
  inJourney++;
  try {
    const detail = (await fn()) || {};
    const ok = detail.ok !== false;
    report.flows.push({ flow: name, ok, ...detail });
    if (!ok) console.log("  FAILED", JSON.stringify(detail));
    return detail;
  } catch (err) {
    console.log("  ERROR", err.message);
    report.flows.push({ flow: name, ok: false, error: err.message.slice(0, 600) });
    return { ok: false };
  } finally {
    inJourney--;
  }
}

const tabs = (page) => page.evaluate(() => [...document.querySelectorAll("[role=tab]")].map((t) => t.innerText.trim()));
const badges = (page) =>
  page.evaluate(() => {
    const list = document.querySelector('ul[aria-label="Derived from capabilities"]');
    return list ? [...list.querySelectorAll("li")].map((li) => li.innerText.trim()) : [];
  });
const centerNav = (page) =>
  page.evaluate(() => {
    const nav = document.querySelector('nav[aria-label="Partner Control Center"]');
    return nav ? [...nav.querySelectorAll("a")].map((a) => ({ text: a.innerText.trim(), current: a.getAttribute("aria-current") })) : null;
  });

// =================================================================== the journeys ==

const staff = {};

async function journeyAdmin(browser) {
  await flow("A. Administrator: sign in, second factor, dashboard, Partners, Resellers, a reseller", async () => {
    staff.context = await browser.createBrowserContext();
    const page = await newPage(staff.context);
    staff.page = page;
    await signInAs(page, "admin", "staff@browser.test");
    const landed = new URL(page.url()).pathname;
    const asked = await answerPrompt(page, STAFF_TOTP, 15000);
    await waitForText(page, "Partners");
    const sidebar = await page.evaluate(() => {
      const nav = [...document.querySelectorAll("nav")].find((n) => (n.getAttribute("aria-label") || "").includes("Super Admin"));
      return nav ? [...nav.querySelectorAll("a")].map((a) => a.innerText.trim()) : [];
    });
    await clickText(page, "Partners", { exact: true, within: 'nav[aria-label="Super Admin"]' });
    await waitForPath(page, () => location.pathname === "/admin/partners");
    await waitForText(page, "Recent activity");
    const dashboard = await bodyText(page);
    const nav = await centerNav(page);
    await clickText(page, "Resellers", { exact: true, within: 'nav[aria-label="Partner Control Center"]' });
    await waitForPath(page, () => location.pathname === "/admin/partners/resellers");
    await waitForText(page, "Seed Reseller");
    await clickText(page, "Seed Reseller", { exact: true, within: "table" });
    await waitForPath(page, () => /^\/admin\/partners\/[0-9a-f-]{36}$/.test(location.pathname));
    await waitForText(page, "Setup progress");
    const detail = { h1: await page.evaluate(() => document.querySelector("h1").innerText), badges: await badges(page), tabs: await tabs(page) };
    const store = await storageAfterSignIn(page);
    const partnerEntries = sidebar.filter((t) => ["Overview", "Resellers", "White-label", "Commissions", "Payouts"].includes(t));
    const ok =
      landed === "/admin" &&
      asked &&
      sidebar.includes("Partners") &&
      partnerEntries.length === 0 &&
      dashboard.includes("Total partners") &&
      dashboard.includes("Recent partners") &&
      nav &&
      nav.length === 9 &&
      nav.find((n) => n.text === "Dashboard").current === "page" &&
      detail.h1 === "Seed Reseller" &&
      detail.badges.includes("Reseller") &&
      !detail.tabs.includes("Referrals") &&
      store.local.length === 0 &&
      store.session.length === 0 &&
      !store.cookies.some((c) => /mode|role/i.test(c));
    return { ok, landed, second_factor_asked: asked, sidebar, center_nav: nav && nav.map((n) => n.text), detail, storage: store };
  });
}

async function journeyCustomerAsAdmin(browser) {
  await flow("B. a normal customer chooses Administrator: refused, nothing shown, Continue as User", async () => {
    const context = await browser.createBrowserContext();
    const page = await newPage(context);
    const answers = [];
    page.on("response", (r) => {
      if (r.url().includes("/api/v1/admin/")) answers.push(`${r.status()} ${new URL(r.url()).pathname}`);
    });
    await signInAs(page, "admin", "customer@browser.test");
    await waitForText(page, "You don't have administrator access.");
    const shown = await bodyText(page);
    const consoleShown = await page.evaluate(() => ({
      nav: [...document.querySelectorAll("nav")].map((n) => n.getAttribute("aria-label")),
      tables: document.querySelectorAll("table").length,
    }));
    await audit(page, "B: administrator access refused");
    await clickText(page, "Continue as User", { exact: true });
    await waitForPath(page, () => location.pathname.startsWith("/o/"));
    const after = new URL(page.url()).pathname;
    await context.close();
    const leaked = /platform\.staff_|Partner Control Center|Super Admin|Seed Reseller|Total partners/.test(shown);
    const ok =
      answers.length > 0 &&
      answers.every((a) => a.startsWith("403 ")) &&
      !leaked &&
      consoleShown.nav.length === 0 &&
      consoleShown.tables === 0 &&
      after.startsWith(`/o/${CUSTOMER_ORG}`);
    return { ok, admin_api_answers: answers, leaked, console_shown: consoleShown, continued_to: after };
  });
}

async function journeyCustomerAsUser(browser) {
  await flow("C. a normal customer signs in as User: organization dashboard, My products", async () => {
    const context = await browser.createBrowserContext();
    const page = await newPage(context);
    await signInAs(page, "user", "customer@browser.test", { viaKeyboard: false });
    await waitForPath(page, () => location.pathname.startsWith("/o/"));
    const landed = new URL(page.url()).pathname;
    await waitForText(page, "My products");
    await clickText(page, "My products", { exact: true, within: "nav" });
    await waitForPath(page, () => location.pathname.endsWith("/apps"));
    await waitForText(page, "Example Desk");
    const apps = new URL(page.url()).pathname;
    await audit(page, "C: My products as a User");
    await context.close();
    return { ok: landed.startsWith(`/o/${CUSTOMER_ORG}`) && apps === `/o/${CUSTOMER_ORG}/apps`, landed, apps };
  });
}

async function journeyAdminAsUser(browser) {
  await flow("D. an administrator signs in as User: their own organization, not the console", async () => {
    const context = await browser.createBrowserContext();
    const page = await newPage(context);
    const visited = [];
    page.on("framenavigated", (f) => {
      if (f === page.mainFrame()) visited.push(new URL(f.url()).pathname);
    });
    await signInAs(page, "user", "staff@browser.test", { viaKeyboard: false });
    await waitForPath(page, () => location.pathname.startsWith("/o/"));
    const landed = new URL(page.url()).pathname;
    await waitForText(page, "Staff Own Company");
    const text = await bodyText(page);
    await context.close();
    const ok = landed.startsWith(`/o/${STAFF_ORG}`) && !visited.some((p) => p.startsWith("/admin")) && !text.includes("Two-step verification is required");
    return { ok, landed, visited };
  });
}

async function journeyCombined() {
  await flow("E. one partner with reseller, white-label and affiliate: one page, one row everywhere", async () => {
    const page = staff.page;
    await visit(page, `/admin/partners/${COMBINED_ID}`, "E: the combined partner");
    await waitForText(page, "Setup progress");
    const header = { badges: await badges(page), tabs: await tabs(page) };
    await clickText(page, "Referrals", { exact: true, within: '[role="tablist"]' });
    await waitForText(page, "Referrals by state");
    const rows = {};
    for (const [label, url] of [
      ["all", "/admin/partners/all"],
      ["resellers", "/admin/partners/resellers"],
      ["affiliates", "/admin/partners/affiliates"],
      ["white-label", "/admin/partners/white-label"],
    ]) {
      await page.goto(`${BASE}${url}`, { waitUntil: "networkidle0" });
      await waitForText(page, "Seed Combined");
      rows[label] = await page.evaluate(() => [...document.querySelectorAll("table a")].filter((a) => a.innerText.trim() === "Seed Combined").length);
    }
    const ok =
      JSON.stringify(header.badges) === JSON.stringify(["Reseller", "White-label", "Affiliate"]) &&
      ["Branding", "Domains", "Referrals"].every((t) => header.tabs.includes(t)) &&
      Object.values(rows).every((n) => n === 1);
    return { ok, header, name_links_per_list: rows };
  });
}

async function journeyDeepLinkAndOldAddresses(browser) {
  await flow("deep link: /admin/... while signed out -> Administrator sign-in -> back to that page", async () => {
    const context = await browser.createBrowserContext();
    const page = await newPage(context);
    await page.goto(`${BASE}/admin/partners/affiliates`, { waitUntil: "networkidle0" });
    await waitForPath(page, () => location.pathname === "/login");
    const sentTo = page.url().replace(BASE, "");
    await waitForText(page, "Sign in as Administrator");
    await page.type('input[type="email"]', "staff@browser.test");
    await page.type('input[type="password"]', PASSWORD);
    await page.click('button[type="submit"]');
    await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 20000 });
    await answerPrompt(page, STAFF_TOTP, 15000);
    await waitForText(page, "Seed Affiliate");
    const back = new URL(page.url()).pathname;
    // A hostile return address is ignored.
    await context.close();
    const hostile = await browser.createBrowserContext();
    const p2 = await newPage(hostile);
    await p2.goto(`${BASE}/login?mode=admin&next=${encodeURIComponent("https://evil.example/admin")}`, { waitUntil: "networkidle0" });
    await p2.type('input[type="email"]', "customer@browser.test");
    await p2.type('input[type="password"]', PASSWORD);
    await p2.click('button[type="submit"]');
    await p2.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 20000 });
    const hostileLanded = p2.url();
    await hostile.close();
    return {
      ok: sentTo === `/login?mode=admin&next=${encodeURIComponent("/admin/partners/affiliates")}` && back === "/admin/partners/affiliates" && hostileLanded.startsWith(`${BASE}/admin`),
      sent_to: sentTo,
      returned_to: back,
      hostile_next_landed: hostileLanded.replace(BASE, ""),
    };
  });

  await flow("old addresses keep working inside the Control Center", async () => {
    const page = staff.page;
    const moves = {};
    for (const [from, to] of [
      ["/admin/commissions?unpaid=1", "/admin/partners/commissions?unpaid=1"],
      ["/admin/payouts?status=open", "/admin/partners/payouts?status=open"],
      ["/admin/partners/resellers?scope=all&status=active", "/admin/partners/all?status=active"],
    ]) {
      await page.goto(`${BASE}${from}`, { waitUntil: "networkidle0" });
      moves[from] = page.url().replace(BASE, "");
      if (moves[from] !== to) return { ok: false, moves };
    }
    return { ok: true, moves };
  });
}

const STAFF_PAGES = [
  ["/admin/partners", "Partner dashboard"],
  ["/admin/partners/all", "All partners"],
  ["/admin/partners/resellers", "Resellers"],
  ["/admin/partners/white-label", "White-label"],
  ["/admin/partners/affiliates", "Affiliates"],
  ["/admin/partners/customers", "Partner customers"],
  ["/admin/partners/products", "Products & pricing"],
  ["/admin/partners/commissions", "Commissions"],
  ["/admin/partners/payouts", "Payouts"],
  ["/admin/partners/new", "Add partner: purpose"],
  ["/admin/partners/new?purpose=affiliate", "Add partner: affiliate"],
  ["/admin/partners/resellers/new", "Add reseller"],
];

async function auditPages(browser) {
  for (const [url, label] of STAFF_PAGES) await visit(staff.page, url, label);
  await visit(staff.page, `/admin/partners/${AFFILIATE_ID}?tab=referrals`, "affiliate detail: Referrals");
  await visit(staff.page, `/admin/partners/${RESELLER_ID}`, "reseller detail: Overview");
  await visit(staff.page, `/admin/partners/${COMBINED_ID}`, "combined detail: Overview");
  const context = await browser.createBrowserContext();
  const page = await newPage(context);
  for (const [url, label] of [
    ["/login", "sign in: choose a mode"],
    ["/login?mode=admin", "sign in: Administrator"],
    ["/login?mode=user", "sign in: User"],
  ]) {
    await visit(page, url, label);
  }
  await context.close();
}

async function reflowAll(browser) {
  const detailPages = [`/admin/partners/${COMBINED_ID}`, `/admin/partners/${AFFILIATE_ID}?tab=referrals`];
  await reflow(browser, staff.page, [...STAFF_PAGES.map(([u]) => u), ...detailPages]);
  await reflow(browser, null, ["/login", "/login?mode=admin", "/login?mode=user"]);
}

// The local navigation on a phone: one strip that scrolls inside itself, with
// the current section in view, and the page itself never wider than the screen.
async function narrowNav(browser) {
  await flow("the Control Center navigation at 390 and 320 px", async () => {
    const out = {};
    for (const width of [390, 320]) {
      const page = await newPage(browser, width);
      await page.setCookie(...(await staff.page.cookies()));
      await page.goto(`${BASE}/admin/partners/payouts`, { waitUntil: "networkidle0" });
      out[width] = await page.evaluate(() => {
        const list = document.querySelector('nav[aria-label="Partner Control Center"] ul');
        const current = list && list.querySelector('[aria-current="page"]');
        const r = current ? current.getBoundingClientRect() : null;
        const box = list ? list.getBoundingClientRect() : null;
        return {
          scrollsInside: list ? list.scrollWidth > list.clientWidth : false,
          currentVisible: Boolean(r && box && r.left >= box.left - 1 && r.right <= box.right + 1),
          page: document.documentElement.scrollWidth <= window.innerWidth + 1,
        };
      });
      await page.close();
    }
    return { ok: Object.values(out).every((o) => o.currentVisible && o.page), widths: out };
  });
}

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--no-sandbox"] });
  await journeyAdmin(browser);
  await journeyCustomerAsAdmin(browser);
  await journeyCustomerAsUser(browser);
  await journeyAdminAsUser(browser);
  if (staff.page) {
    await journeyCombined();
    await journeyDeepLinkAndOldAddresses(browser);
    await auditPages(browser);
    await reflowAll(browser);
    await narrowNav(browser);
  }

  const failedFlows = report.flows.filter((f) => !f.ok);
  const axeFailures = report.pages.filter((p) => p.axe.length > 0);
  const focusFailures = report.pages.filter((p) => p.noFocus.length > 0);
  const h1Failures = report.pages.filter((p) => p.h1.length !== 1);
  const reflowFailures = report.reflow.filter((r) => !r.ok);
  report.summary = {
    flows: `${report.flows.length - failedFlows.length}/${report.flows.length} passed`,
    pages: report.pages.length,
    axe_pages_with_violations: axeFailures.length,
    pages_with_an_invisible_focus: focusFailures.length,
    pages_without_exactly_one_h1: h1Failures.length,
    csp_violations: report.csp.length,
    console_errors: report.errors.length,
    expected_refusals_in_journeys: report.expected.length,
    reflow_checks: report.reflow.length,
    reflow_failures: reflowFailures.length,
    screen_reader_pass: "NOT EXECUTED",
  };
  if (OUT) fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report.summary, null, 2));
  failedFlows.forEach((f) => console.log("FAILED FLOW:", JSON.stringify(f)));
  axeFailures.forEach((p) => console.log("AXE:", p.label, JSON.stringify(p.axe)));
  focusFailures.forEach((p) => console.log("FOCUS:", p.label, JSON.stringify(p.noFocus)));
  h1Failures.forEach((p) => console.log("H1:", p.label, JSON.stringify(p.h1)));
  reflowFailures.forEach((r) => console.log("REFLOW:", JSON.stringify(r)));
  report.csp.forEach((c) => console.log("CSP:", c));
  report.errors.slice(0, 20).forEach((e) => console.log("ERROR:", e));
  await browser.close();
  process.exit(failedFlows.length || axeFailures.length || focusFailures.length || h1Failures.length || reflowFailures.length || report.csp.length || report.errors.length ? 1 : 0);
})().catch((err) => {
  console.error("CHECK CRASHED", err);
  process.exit(2);
});
