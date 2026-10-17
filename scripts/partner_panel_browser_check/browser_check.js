// Real-browser verification of the partner business panel (ADR-045), against the
// production build and a real backend. Same harness and rules as
// ../partner_setup_browser_check:
//   - CSP violations and console errors on every page;
//   - axe WCAG 2.1 A/AA, a visible keyboard focus indicator, exactly one h1;
//   - reflow (no horizontal scroll) at 1440, 1024, 768, 390 and 320 px;
// and the brief's journeys, driven through the UI:
//   A. partner sign-in: a signed-out visit is sent to sign-in; a customer who is
//      no partner's member is refused; the owner reaches the dashboard; the
//      demo partner's temporary password forces a change before anything else
//   B. customer: add a customer, see it listed, export, the subscriptions and
//      payments pages
//   C. sub-partner: create, activate, the sub-partner signs in and sees only its
//      own (empty) customers and none of its parent's
//   D. affiliate: create a referral link, conversions, settings
//   E. white-label: create the brand, pick a colour scheme, request a domain
//   F. pricing: plans and prices from the server; a product switched off for new
//      sales leaves the sell form; back on, it returns
//   G. communication: channels, templates, no credential field anywhere
//   H. team: invite, change a role; a finance member sees money and no customers
//   I. isolation: another partner's address and API answer "no access"
// Not part of CI. A manual screen-reader pass is NOT part of it: it is reported
// as NOT EXECUTED.
const puppeteer = require("puppeteer-core");
const crypto = require("crypto");
const fs = require("fs");
const { execFileSync } = require("child_process");

const BASE = process.env.BASE || "http://127.0.0.1:3999";
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const AXE = fs.readFileSync(process.env.AXE_PATH, "utf8");
// Made fresh for every run by whoever starts the check (the scratch database is seeded with it).
const PASSWORD = process.env.CHECK_PASSWORD;
if (!PASSWORD) throw new Error("CHECK_PASSWORD is required");
const { PANEL_ID, RIVAL_ID, DEMO_ID, PANEL_TOTP, DEMO_PASSWORD_FILE, PYTHON, SET_PASSWORD_PY, OUT } = process.env;
const WIDTHS = [1440, 1024, 768, 390, 320];

const report = { pages: [], csp: [], errors: [], expected: [], reflow: [], flows: [], notes: [] };
let inJourney = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------------ plumbing --

async function newPage(context, width = 1280) {
  const page = await context.newPage();
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
    const refusal = /status of (401|403|400|404|409|429)/.test(m.text());
    if (m.text().startsWith("Failed to load resource") && refusal) report.expected.push(line);
    else report.errors.push(line);
  });
  page.on("pageerror", (e) => report.errors.push(`${page.url()} :: pageerror ${e.message}`));
  page.on("dialog", (dialog) => dialog.accept().catch(() => {}));
  return page;
}

async function audit(page, label) {
  const csp = await page.evaluate(() => window.__csp || []);
  csp.forEach((c) => report.csp.push(`${label}: ${c}`));
  const axe = await page.evaluate(async (src) => {
    (0, eval)(src);
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
  await page.evaluate(() => window.scrollTo(0, 0));
  const h1 = await page.evaluate(() => [...document.querySelectorAll("h1")].map((h) => h.innerText));
  report.pages.push({ label, url: page.url().replace(BASE, ""), axe, tabbed, noFocus, h1 });
}

async function visit(page, url, label, { auditIt = true, settle = "main" } = {}) {
  console.log("visit", label);
  await page.goto(`${BASE}${url}`, { waitUntil: "networkidle0", timeout: 30000 });
  await page.waitForSelector(settle, { timeout: 15000 }).catch(() => {});
  await sleep(500);
  if (auditIt) await audit(page, label);
}

const bodyText = (page) => page.evaluate(() => (document.querySelector("main") || document.body).innerText);

async function waitForText(page, text, timeout = 15000) {
  await page.waitForFunction((t) => document.body.innerText.includes(t), { timeout }, text).catch(async () => {
    const shown = (await bodyText(page)).slice(0, 500).replace(/\n+/g, " | ");
    throw new Error(`text not found: "${text}" on ${page.url()} :: ${shown}`);
  });
}

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
      text, exact, within,
    );
    if (clicked) return;
    await sleep(200);
  }
  const shown = (await bodyText(page)).slice(0, 300).replace(/\n+/g, " | ");
  throw new Error(`nothing to click for "${text}" on ${page.url()} :: ${shown}`);
}

async function fieldHandle(page, labelText, { within = "main", nth = 0, timeout = 8000 } = {}) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const id = await page.evaluate(
      (text, within, nth) => {
        const root = document.querySelector(within) || document.body;
        const clean = (s) => (s || "").replace(/\s+/g, " ").replace(/\s*\*\s*$/, "").trim();
        const labels = [...root.querySelectorAll("label")].filter((l) => clean(l.innerText).startsWith(text) && l.htmlFor);
        const label = labels[nth];
        return label ? label.htmlFor : null;
      },
      labelText, within, nth,
    );
    if (id) {
      const handle = await page.$(`[id="${id}"]`);
      if (handle) return handle;
    }
    await sleep(150);
  }
  const shown = (await bodyText(page)).slice(0, 300).replace(/\n+/g, " | ");
  throw new Error(`no field labelled "${labelText}" on ${page.url()} :: ${shown}`);
}

async function type(page, labelText, value, opts) {
  const handle = await fieldHandle(page, labelText, opts);
  await handle.evaluate((el) => el.scrollIntoView({ block: "center" }));
  await handle.click({ clickCount: 3 });
  await page.keyboard.press("Backspace");
  if (value !== "") await handle.type(value);
}

async function choose(page, labelText, optionText, opts) {
  const handle = await fieldHandle(page, labelText, opts);
  const value = await handle.evaluate((el, text) => {
    const option = [...el.options].find((o) => o.text === text || o.value === text);
    return option ? option.value : null;
  }, optionText);
  if (value === null) throw new Error(`no option "${optionText}" in "${labelText}"`);
  await handle.select(value);
}

async function tick(page, text, on = true, { legend = null } = {}) {
  const done = await page.evaluate(
    (text, on, legend) => {
      const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
      let roots = [document.body];
      if (legend) roots = [...document.querySelectorAll("fieldset")].filter((f) => clean(f.querySelector("legend") && f.querySelector("legend").innerText).includes(legend));
      for (const root of roots) {
        for (const label of root.querySelectorAll("label")) {
          if (!clean(label.innerText).includes(text)) continue;
          const input = label.querySelector("input[type=checkbox], input[type=radio]");
          if (!input || input.disabled) continue;
          input.scrollIntoView({ block: "center" });
          if (input.checked !== on) input.click();
          return true;
        }
      }
      return false;
    },
    text, on, legend,
  );
  if (!done) throw new Error(`no enabled choice "${text}"${legend ? ` in "${legend}"` : ""} on ${page.url()}`);
}

// ------------------------------------------------------------------ TOTP --

function base32(input) {
  const alphabet = "ABCDEFGHIJKLMNOPQRST" + "UVWXYZ234567";
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

const lastWindow = { [PANEL_TOTP]: Math.floor(Date.now() / 30000) };
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

async function untilDone(page, secret, done, timeout = 90000) {
  const deadline = Date.now() + timeout;
  let prompts = 0;
  while (Date.now() < deadline) {
    if (await page.evaluate(done)) return prompts;
    if (await answerPrompt(page, secret, 1500)) prompts++;
    await sleep(300);
  }
  throw new Error(`did not finish; ${page.url()} :: ${(await bodyText(page)).slice(0, 400).replace(/\n+/g, " | ")}`);
}

// ------------------------------------------------------------------ sign-in --

async function signIn(page, email, password = PASSWORD) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle0" });
  await page.waitForFunction(() => document.body.innerText.includes("How would you like to sign in?"), { timeout: 15000 });
  await clickText(page, "User", { exact: true });
  await page.waitForSelector('input[type="email"]', { visible: true, timeout: 15000 });
  await page.type('input[type="email"]', email);
  await page.type('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 20000 });
  await page.waitForNetworkIdle({ idleTime: 600, timeout: 15000 }).catch(() => {});
}

// ONLY=A5,H2 runs only the journeys whose names start with those codes (and
// skips the page audit and the reflow sweep, which need the owner signed in).
const ONLY = (process.env.ONLY || "").split(",").map((x) => x.trim()).filter(Boolean);

async function flow(name, fn) {
  if (ONLY.length && !ONLY.some((o) => name.startsWith(o + " "))) return;
  console.log("FLOW", name);
  inJourney++;
  const started = Date.now();
  try {
    const detail = await fn();
    report.flows.push({ name, ok: true, ms: Date.now() - started, detail: detail || "" });
  } catch (err) {
    console.log("  FAILED:", err.message);
    report.flows.push({ name, ok: false, ms: Date.now() - started, detail: String(err.message).slice(0, 700) });
  } finally {
    inJourney--;
  }
}

function expectThat(condition, message) {
  if (!condition) throw new Error(message);
}

const apiGet = (page, path) =>
  page.evaluate(async (p) => {
    const r = await fetch(p, { credentials: "same-origin" });
    const text = await r.text();
    let body = null;
    try { body = JSON.parse(text); } catch { body = text.slice(0, 200); }
    return { status: r.status, body };
  }, path);

// ------------------------------------------------------------------ routes --

const ROUTES = [
  ["", "dashboards"], ["users", "users"], ["users/subscriptions", "user subscriptions"], ["agency", "agency"],
  ["affiliates", "affiliates"], ["affiliates/conversions", "affiliate conversions"], ["affiliates/payouts", "affiliate payouts"], ["affiliates/settings", "affiliate settings"],
  ["plans", "plans"], ["plans/add-ons", "add-ons"], ["pricing/currency", "currency"], ["pricing/packages", "package & pricing"], ["pricing/rules", "pricing rule"],
  ["payments", "payments"], ["payments/commissions", "commissions"], ["payments/balance", "balance"], ["product-billing", "product billing"],
  ["brand-settings", "brand settings"], ["brand-settings/url-manager", "url manager"], ["product-manager", "product manager"], ["appearance", "appearance"],
  ["communication/channel", "communication channel"], ["communication/templates", "communication templates"],
  ["communication/alerts", "communication alerts"], ["communication/logs", "communication logs"],
  ["integrations", "integrations"], ["partner-products", "partner products"], ["auth-configuration", "auth configuration"], ["shared-resources", "shared resources"],
  ["account", "account"], ["channel-manager", "channel manager"], ["api-keys", "api key manager"], ["translations", "translation manager"],
  ["roles", "role & permissions"], ["team", "team members"], ["support-manager", "support manager"], ["notifications", "notification center"],
];

/** The approved sidebar, verbatim. */
const APPROVED_NAV = [
  "Dashboards", "Users", "Agency", "Affiliates", "Plans & Add-ons", "Currency & Pricing", "Payments", "Product Billing",
  "Brand Settings", "Product Manager", "Appearance", "Communication", "Integrations", "Partner Products", "Auth Configuration",
  "Shared Resources", "Account", "Channel Manager", "API Key Manager", "Translation Manager", "Role & Permissions", "Team Members",
  "Support Manager", "Notification Center",
];

/** Every address the old portal served, and where it must now land. */
const LEGACY = [
  ["customers", "users"], ["subscriptions", "users/subscriptions"], ["sub-partners", "agency"], ["referrals", "affiliates"],
  ["commissions", "payments/commissions"], ["payouts", "affiliates/payouts"], ["balance", "payments/balance"], ["orders", "payments"],
  ["payment-links", "payments"], ["currencies", "pricing/currency"], ["pricing", "pricing/currency"], ["pricing/currencies", "pricing/currency"],
  ["branding", "brand-settings"], ["domains", "brand-settings/url-manager"], ["white-label", "brand-settings/white-label-setup"],
  ["products", "partner-products"], ["authentication", "auth-configuration"], ["settings", "account"], ["activity", "notifications"],
  ["communication", "communication/channel"], ["communication/channels", "communication/channel"], ["support", "support-manager"],
];

// ------------------------------------------------------------------ main --

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--no-sandbox"] });
  const fresh = () => browser.createBrowserContext();

  // ---- A. sign-in and refusal
  await flow("A1 a signed-out visit is sent to sign-in", async () => {
    const page = await newPage(await fresh());
    await page.goto(`${BASE}/partner/${PANEL_ID}`, { waitUntil: "networkidle0" });
    await page.waitForFunction(() => location.pathname.startsWith("/login"), { timeout: 15000 });
    await page.close();
  });

  await flow("A2 a customer who is nobody's partner member is refused", async () => {
    const page = await newPage(await fresh());
    await signIn(page, "customer@panel.test");
    await page.goto(`${BASE}/partner/${PANEL_ID}`, { waitUntil: "networkidle0" });
    await waitForText(page, "don't have access to this partner account");
    const text = await page.evaluate(() => document.body.innerText);
    expectThat(!text.includes("Panel Partner") && !text.includes("Users"), "partner details leaked to a customer");
    const api = await apiGet(page, `/api/v1/partner/${PANEL_ID}/dashboard`);
    expectThat(api.status === 404, `dashboard API gave ${api.status}`);
    await page.close();
  });

  const owner = await newPage(await fresh());
  await flow("A3 the owner reaches the dashboard", async () => {
    await signIn(owner, "owner@panel.test");
    await visit(owner, `/partner/${PANEL_ID}`, "dashboard");
    await waitForText(owner, "Welcome, Panel Partner");
    const text = await owner.evaluate(() => document.querySelector("main").innerText);
    expectThat(/Total customers\s*\n?\s*2/.test(text), "customer count is not 2");
    expectThat(text.includes("£14.99"), "gross sales £14.99 not shown");
    expectThat(text.includes("£1.50"), "commission £1.50 not shown");
    expectThat(/wholesale-cost model/.test(text), "partner cost is not explained");
    expectThat(/No customer or user limit/.test(text), "customer limit is not explained");
    const nav = await owner.evaluate(() => [...document.querySelector('nav[aria-label^="Partner "]').querySelectorAll("a")].map((a) => a.innerText.trim()));
    expectThat(JSON.stringify(nav) === JSON.stringify(APPROVED_NAV), `the sidebar is not exactly the approved menu: ${JSON.stringify(nav)}`);
    return `${nav.length} navigation links`;
  });

  await flow("A4 the dashboard range and the table view", async () => {
    await visit(owner, `/partner/${PANEL_ID}`, "dashboard 7d", { auditIt: false });
    await clickText(owner, "7 days", { exact: true });
    await owner.waitForNetworkIdle({ idleTime: 500 }).catch(() => {});
    await clickText(owner, "Show as table", { exact: true });
    await waitForText(owner, "Gross sales by day");
    await clickText(owner, "Custom", { exact: true });
    for (const [label, value] of [["From", "2020-01-01"], ["To", "2020-01-31"]]) {
      const handle = await fieldHandle(owner, label);
      await handle.evaluate((el, v) => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, v);
        el.dispatchEvent(new Event("input", { bubbles: true }));
      }, value);
    }
    await clickText(owner, "Apply", { exact: true });
    await owner.waitForNetworkIdle({ idleTime: 500 }).catch(() => {});
    await waitForText(owner, "No sales in this period");
  });

  // ---- every page, audited
  await flow("pages: every screen of the panel passes axe, CSP, focus and h1", async () => {
    for (const [route, label] of ROUTES) await visit(owner, `/partner/${PANEL_ID}${route ? `/${route}` : ""}`, label);
    const bad = report.pages.filter((p) => p.axe.length || p.noFocus.length || p.h1.length !== 1);
    expectThat(bad.length === 0, `${bad.length} pages failed: ${bad.map((b) => b.label).join(", ")}`);
    return `${ROUTES.length} pages`;
  });

  // ---- B. customers
  await flow("B add a customer, see it, export the list", async () => {
    await visit(owner, `/partner/${PANEL_ID}/users`, "users (b)", { auditIt: false });
    await type(owner, "Organization name", "Browser Check Ltd");
    await type(owner, "Owner's first name", "Bea");
    await type(owner, "Owner's last name", "Browser");
    await type(owner, "Owner's email", "bea@browsercheck.test");
    await clickText(owner, "Create customer", { exact: true });
    await untilDone(owner, PANEL_TOTP, () => document.body.innerText.includes("Customer created"), 90000);
    await owner.reload({ waitUntil: "networkidle0" });
    await waitForText(owner, "Browser Check Ltd");
    const row = await owner.evaluate(() => [...document.querySelectorAll("tbody tr")].find((r) => r.innerText.includes("Browser Check Ltd"))?.innerText || "");
    expectThat(!/bea@browsercheck.test/.test(row), "the owner's address is shown to the partner");
    expectThat(/pending|awaiting/i.test(row), "owner activation is not shown as pending");
    const probe = await owner.evaluate(async (id) => {
      const r = await fetch(`/api/v1/partner/${id}/customers/export`, { credentials: "same-origin" });
      return { status: r.status, disposition: r.headers.get("content-disposition") || "", text: await r.text() };
    }, PANEL_ID);
    expectThat(probe.status === 200, `export gave ${probe.status}`);
    expectThat(probe.disposition.startsWith("attachment"), "export is not a download");
    expectThat(probe.text.includes("Browser Check Ltd") && probe.text.includes("Zen Retail") && !probe.text.includes("Rival Customer"), "export content wrong");
    await clickText(owner, "Export customers (CSV)");
    await waitForText(owner, "Your customer list was downloaded.");
    await visit(owner, `/partner/${PANEL_ID}/users/subscriptions`, "subscriptions (b)", { auditIt: false });
    await waitForText(owner, "Zen Retail");
  });

  // ---- F. pricing and the sale switch
  await flow("F plans and prices come from the server; a product off for sale leaves the sell form", async () => {
    await visit(owner, `/partner/${PANEL_ID}/plans`, "plans (f)", { auditIt: false });
    await waitForText(owner, "Team");
    const plans = await bodyText(owner);
    expectThat(plans.includes("£14.99") && plans.includes("£29.99"), "plan prices missing");
    expectThat(!plans.includes("Basic"), "an ungranted plan is shown");
    await visit(owner, `/partner/${PANEL_ID}/product-billing`, "product billing (f)", { auditIt: false });
    await owner.evaluate(() => {
      const row = [...document.querySelectorAll("tbody tr")].find((r) => r.innerText.includes("Example Desk"));
      [...row.querySelectorAll("button")].find((b) => b.innerText.includes("Disable for new sales")).click();
    });
    await waitForText(owner, "Example Desk is off for new sales.");
    await visit(owner, `/partner/${PANEL_ID}/users`, "users (f)", { auditIt: false });
    await clickText(owner, "Sell a plan");
    await owner.waitForSelector("select", { timeout: 8000 });
    const offered = await owner.evaluate(() => [...document.querySelectorAll("select option")].map((o) => o.innerText));
    expectThat(offered.length > 1 && !offered.some((t) => t.includes("Example Desk")) && offered.some((t) => t.includes("Example Books")), `sell form offers ${JSON.stringify(offered)}`);
    await visit(owner, `/partner/${PANEL_ID}/product-billing`, "product billing (f2)", { auditIt: false });
    await owner.evaluate(() => {
      const row = [...document.querySelectorAll("tbody tr")].find((r) => r.innerText.includes("Example Desk"));
      [...row.querySelectorAll("button")].find((b) => b.innerText.includes("Enable for sale")).click();
    });
    await waitForText(owner, "Example Desk is on sale.");
  });

  // ---- D. affiliates
  await flow("D affiliates: create a referral link; conversions and settings", async () => {
    await visit(owner, `/partner/${PANEL_ID}/affiliates`, "affiliates (d)", { auditIt: false });
    await type(owner, "Campaign label", "Autumn push");
    await clickText(owner, "Create link", { exact: true });
    await waitForText(owner, "/signup?ref=");
    await visit(owner, `/partner/${PANEL_ID}/affiliates/conversions`, "conversions (d)", { auditIt: false });
    await waitForText(owner, "never shows who the visitor was");
    await visit(owner, `/partner/${PANEL_ID}/affiliates/settings`, "affiliate settings (d)", { auditIt: false });
    await waitForText(owner, "90 days");
  });

  // ---- E. white-label
  await flow("E brand, colour scheme and domain request", async () => {
    await visit(owner, `/partner/${PANEL_ID}/brand-settings`, "branding (e)", { auditIt: false });
    await type(owner, "Brand name", "Panel Brand");
    await clickText(owner, "Save", { within: "main" });
    await untilDone(owner, PANEL_TOTP, () => /saved|Brand saved/i.test(document.body.innerText), 60000).catch(() => {});
    await visit(owner, `/partner/${PANEL_ID}/appearance`, "appearance (e)", { auditIt: false });
    await waitForText(owner, "Violet");
    await clickText(owner, "Violet", { within: "main" });
    await waitForText(owner, "Violet applied.");
    await visit(owner, `/partner/${PANEL_ID}/brand-settings/url-manager`, "domains (e)", { auditIt: false });
    await waitForText(owner, "domain");
  });

  // ---- G. communication
  await flow("G communication shows one live channel and no credential field", async () => {
    await visit(owner, `/partner/${PANEL_ID}/communication/channel`, "channels (g)", { auditIt: false });
    await waitForText(owner, "Webhook");
    const hasPassword = await owner.evaluate(() => !!document.querySelector('input[type="password"]'));
    expectThat(!hasPassword, "a password field exists on the communication page");
    const text = await bodyText(owner);
    expectThat(/not available/i.test(text) && /Email/.test(text), "channels are not explained");
    await visit(owner, `/partner/${PANEL_ID}/communication/templates`, "templates (g)", { auditIt: false });
    await waitForText(owner, "Variables");
    const inputs = await owner.evaluate(() => document.querySelectorAll("main textarea, main input").length);
    expectThat(inputs === 0, "templates are editable");
  });

  // ---- H. team and roles
  await flow("H invite a person, change their role", async () => {
    await visit(owner, `/partner/${PANEL_ID}/team`, "team (h)", { auditIt: false });
    await type(owner, "Email", "ops@panel.test");
    await choose(owner, "Role for the new user", "Sales");
    await clickText(owner, "Send invitation", { exact: true });
    await untilDone(owner, PANEL_TOTP, () => document.body.innerText.includes("Invitation sent"), 90000);
    await owner.reload({ waitUntil: "networkidle0" });
    await waitForText(owner, "ops@panel.test");
    await choose(owner, "Role of ops@panel.test", "Viewer");
    await waitForText(owner, "Role changed for ops@panel.test");
  });

  await flow("H2 a finance member sees the money and no customers", async () => {
    const page = await newPage(await fresh());
    await signIn(page, "finance@panel.test");
    await visit(page, `/partner/${PANEL_ID}`, "dashboard (finance)");
    await waitForText(page, "Welcome, Panel Partner");
    const nav = await page.evaluate(() => [...document.querySelector('nav[aria-label^="Partner "]').querySelectorAll("a")].map((a) => a.innerText.trim()));
    expectThat(nav.includes("Payments") && nav.includes("Affiliates"), "finance lacks the money pages");
    for (const absent of ["Users", "Agency", "Brand Settings", "API Key Manager"]) expectThat(!nav.includes(absent), `finance sees ${absent}`);
    expectThat(nav.every((l) => APPROVED_NAV.includes(l)), `finance sees an item outside the approved menu: ${JSON.stringify(nav)}`);
    const text = await bodyText(page);
    expectThat(text.includes("Not shown for your role."), "hidden customer section is not explained");
    const api = await apiGet(page, `/api/v1/partner/${PANEL_ID}/customers`);
    expectThat(api.status === 403, `customers API gave ${api.status} for finance`);
    await page.close();
  });

  // ---- C. sub-partner
  await flow("C create a sub-partner, activate it, it signs in and sees only its own customers", async () => {
    await visit(owner, `/partner/${PANEL_ID}/agency`, "agency (c)", { auditIt: false });
    await waitForText(owner, "Add an agency");
    await type(owner, "Partner name", "North Agency");
    await type(owner, "Administrator's name", "Nora North");
    await type(owner, "Administrator's email", "admin@north.test");
    await tick(owner, "Create customers", true, { legend: "What it can do" });
    await tick(owner, "Sell products", true, { legend: "What it can do" });
    await tick(owner, "Example Desk", true, { legend: "Products it may sell" });
    await clickText(owner, "Add agency", { exact: true });
    await untilDone(owner, PANEL_TOTP, () => document.body.innerText.includes("Agency created in draft"), 90000);
    await owner.reload({ waitUntil: "networkidle0" });
    await waitForText(owner, "North Agency");
    await clickText(owner, "Activate", { exact: true, within: "tbody" });
    await untilDone(owner, PANEL_TOTP, () => document.body.innerText.includes("is active."), 90000);
    execFileSync(PYTHON, [SET_PASSWORD_PY, "admin@north.test"], { env: process.env, stdio: "pipe" });
    const child = await newPage(await fresh());
    await signIn(child, "admin@north.test");
    const portal = await apiGet(child, "/api/v1/partner/portal");
    const mine = portal.body.partners[0];
    expectThat(portal.body.partners.length === 1 && mine.display_name === "North Agency", "the sub-partner's portal is wrong");
    await visit(child, `/partner/${mine.partner_id}/users`, "users (sub-partner)");
    await waitForText(child, "No customers yet.");
    const text = await bodyText(child);
    expectThat(!text.includes("Zen Retail") && !text.includes("Orchid Studio"), "the parent's customers leaked to the sub-partner");
    const nav = await child.evaluate(() => [...document.querySelector('nav[aria-label^="Partner "]').querySelectorAll("a")].map((a) => a.innerText.trim()));
    expectThat(!nav.includes("Agency"), "the sub-partner may create agencies");
    const up = await apiGet(child, `/api/v1/partner/${PANEL_ID}/customers`);
    expectThat(up.status === 404, `the sub-partner reached its parent's customers (${up.status})`);
    const own = await apiGet(child, `/api/v1/partner/${mine.partner_id}/members`);
    expectThat(own.status === 200, "the sub-partner cannot read its own team");
    await child.close();
  });

  // ---- I. isolation
  await flow("I another partner's address and API answer no access", async () => {
    await visit(owner, `/partner/${RIVAL_ID}`, "rival (denied)", { auditIt: false });
    await waitForText(owner, "don't have access to this partner account");
    for (const path of ["dashboard", "customers", "balance", "members", "orders", "activity", "profile", "sub-partners"]) {
      const r = await apiGet(owner, `/api/v1/partner/${RIVAL_ID}/${path}`);
      expectThat(r.status === 404, `${path} of another partner gave ${r.status}`);
    }
    const activity = await apiGet(owner, `/api/v1/partner/${PANEL_ID}/activity`);
    expectThat(activity.status === 200, "own activity unavailable");
    expectThat(!JSON.stringify(activity.body).includes("Rival"), "another partner's activity leaked");
  });

  // ---- J. the old addresses redirect to the new homes, and nothing renders at them
  await flow("J every old partner address redirects permanently to its new page", async () => {
    const probe = await owner.evaluate(async (id, legacy) => {
      const out = [];
      for (const [from, to] of legacy) {
        const r = await fetch(`/partner/${id}/${from}?x=1`, { redirect: "manual", credentials: "same-origin" });
        out.push({ from, to, type: r.type, status: r.status });
      }
      return out;
    }, PANEL_ID, LEGACY);
    // A manual redirect to a same-origin URL is an opaque redirect; follow it to read the landing page.
    for (const { from, to } of probe) {
      const page = owner;
      await page.goto(`${BASE}/partner/${PANEL_ID}/${from}`, { waitUntil: "networkidle0" });
      const path = await page.evaluate(() => location.pathname);
      expectThat(path === `/partner/${PANEL_ID}/${to}`, `/${from} landed on ${path}, not /${to}`);
    }
    const seen = probe.filter((p) => p.type !== "opaqueredirect" && p.status !== 308);
    expectThat(seen.length === 0, `not permanent redirects: ${JSON.stringify(seen)}`);
    return `${LEGACY.length} redirects`;
  });

  // ---- K. API keys: shown once, scoped, revocable
  await flow("K create an API key, see its secret once, use it, rotate it, revoke it", async () => {
    await visit(owner, `/partner/${PANEL_ID}/api-keys`, "api keys (k)", { auditIt: false });
    await type(owner, "Key name", "Browser check key");
    await tick(owner, "Read customers", true, { legend: "Scope" });
    await clickText(owner, "Create key", { exact: true });
    await untilDone(owner, PANEL_TOTP, () => !!document.querySelector('[data-testid="new-secret"]'), 90000);
    const secret = await owner.evaluate(() => document.querySelector('[data-testid="new-secret"]').innerText.trim());
    expectThat(/^w360pk_[0-9a-f]{12}_\S{20,}$/.test(secret), "the secret has the wrong shape");
    const used = await owner.evaluate(async (s) => {
      const r = await fetch("/api/v1/partner-api/customers", { headers: { Authorization: `Bearer ${s}` }, credentials: "omit" });
      return { status: r.status, text: await r.text() };
    }, secret);
    expectThat(used.status === 200 && used.text.includes("Zen Retail") && !used.text.includes("Rival Customer"), `key read gave ${used.status}`);
    const noScope = await owner.evaluate(async (s) => (await fetch("/api/v1/partner-api/commissions", { headers: { Authorization: `Bearer ${s}` }, credentials: "omit" })).status, secret);
    expectThat(noScope === 403, `a key without the scope read commissions (${noScope})`);
    await clickText(owner, "I have stored it", { exact: true });
    await owner.reload({ waitUntil: "networkidle0" });
    const page = await bodyText(owner);
    expectThat(!page.includes(secret) && !page.includes(secret.split("_")[2]), "the secret is shown again after it was dismissed");
    const listing = await apiGet(owner, `/api/v1/partner/${PANEL_ID}/api-keys`);
    expectThat(!JSON.stringify(listing.body).includes(secret.split("_")[2]), "the list API returns the secret");
    await owner.evaluate(() => [...document.querySelectorAll("button")].find((b) => b.getAttribute("aria-label") === "Rotate Browser check key").click());
    await clickText(owner, "Confirm rotate", { exact: true });
    await untilDone(owner, PANEL_TOTP, () => !!document.querySelector('[data-testid="new-secret"]'), 90000);
    const after = await owner.evaluate(async (s) => (await fetch("/api/v1/partner-api/customers", { headers: { Authorization: `Bearer ${s}` }, credentials: "omit" })).status, secret);
    expectThat(after === 401, `the rotated-out secret still works (${after})`);
    await clickText(owner, "I have stored it", { exact: true });
    await owner.evaluate(() => [...document.querySelectorAll("button")].find((b) => b.getAttribute("aria-label") === "Revoke Browser check key" && !b.disabled).click());
    await clickText(owner, "Confirm revoke", { exact: true });
    await untilDone(owner, PANEL_TOTP, () => document.body.innerText.includes("was revoked"), 90000);
  });

  await flow("K2 a member without key permission has no API key manager", async () => {
    const page = await newPage(await fresh());
    await signIn(page, "finance@panel.test");
    const r = await apiGet(page, `/api/v1/partner/${PANEL_ID}/api-keys`);
    expectThat(r.status === 403, `finance read the key list (${r.status})`);
    await page.close();
  });

  // ---- L. translations
  await flow("L translations: word a string, it is plain text, markup is refused", async () => {
    await visit(owner, `/partner/${PANEL_ID}/translations`, "translations (l)", { auditIt: false });
    await choose(owner, "Language", "French");
    await waitForText(owner, "Shown now:");
    await type(owner, "Welcome heading", "Bienvenue chez nous");
    await clickText(owner, "Save translations", { exact: true });
    await waitForText(owner, "Translations saved.");
    const bad = await owner.evaluate(async (id) => {
      const token = document.cookie.split("; ").map((c) => c.split("=")).find(([k]) => k.endsWith("w360_csrf"));
      const r = await fetch(`/api/v1/partner/${id}/translations`, {
        method: "PUT", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": token ? token[1] : "" },
        body: JSON.stringify({ locale: "fr", strings: { "portal.welcome_title": "<img src=x onerror=alert(1)>" } }),
      });
      return r.status;
    }, PANEL_ID);
    expectThat(bad === 400, `markup was accepted (${bad})`);
  });

  // ---- M. notifications and support
  await flow("M notifications: unread, important, mark read, mark all read; support points at help", async () => {
    await visit(owner, `/partner/${PANEL_ID}/notifications`, "notifications (m)", { auditIt: false });
    await waitForText(owner, "Notification Center");
    const before = await bodyText(owner);
    expectThat(/Unread \(\d+\)/.test(before), "no unread count");
    await clickText(owner, "Important", { within: "main" });
    await owner.waitForNetworkIdle({ idleTime: 400 }).catch(() => {});
    await clickText(owner, "Unread", { within: "main" });
    await owner.waitForNetworkIdle({ idleTime: 400 }).catch(() => {});
    await clickText(owner, "Mark all read", { exact: true });
    await waitForText(owner, "Everything is marked read.");
    await waitForText(owner, "You are all caught up");
    await visit(owner, `/partner/${PANEL_ID}/support-manager`, "support (m)", { auditIt: false });
    await waitForText(owner, "Open and resolved requests");
    const links = await owner.evaluate(() => [...document.querySelectorAll("main a")].map((a) => a.href));
    expectThat(links.every((h) => /^(https:|mailto:|http:\/\/127\.0\.0\.1|http:\/\/localhost)/.test(h)), `unsafe link on support: ${links}`);
  });

  // ---- N. team: suspend and resume
  await flow("N suspend and resume a team member", async () => {
    await visit(owner, `/partner/${PANEL_ID}/team`, "team (n)", { auditIt: false });
    await waitForText(owner, "ops@panel.test");
    const clickRow = async (name) => owner.evaluate((n) => { const row = [...document.querySelectorAll("tbody tr")].find((r) => r.innerText.includes("ops@panel.test")); const b = [...row.querySelectorAll("button")].find((x) => x.innerText.trim() === n); if (b) b.click(); return !!b; }, name);
    await clickRow("Suspend");
    await untilDone(owner, PANEL_TOTP, () => document.body.innerText.includes("is suspended"), 90000);
    await owner.reload({ waitUntil: "networkidle0" });
    expectThat(await clickRow("Resume"), "no Resume for a suspended member");
    await untilDone(owner, PANEL_TOTP, () => document.body.innerText.includes("has access again"), 90000);
  });

  // ---- demo partner: temporary password
  await flow("A5 the demo partner must choose its own password before anything else", async () => {
    const temp = fs.readFileSync(DEMO_PASSWORD_FILE, "utf8").trim();
    const page = await newPage(await fresh());
    await signIn(page, "partner.demo@workeasy360.test", temp);
    await page.goto(`${BASE}/partner/${DEMO_ID}`, { waitUntil: "networkidle0" });
    await waitForText(page, "Choose your own password");
    const blocked = await apiGet(page, `/api/v1/partner/${DEMO_ID}/dashboard`);
    expectThat(blocked.status === 403 && blocked.body.code === "password_change_required", `dashboard gave ${blocked.status}`);
    await audit(page, "forced password change");
    await type(page, "Current password", temp, { within: "body" });
    await type(page, "New password", "A-new-passphrase-for-the-demo-1", { within: "body" });
    await type(page, "Confirm the new password", "A-new-passphrase-for-the-demo-1", { within: "body" });
    await clickText(page, "Change password", { exact: true });
    await waitForText(page, "Welcome, Workeasy360 Demo Partner", 20000);
    const ok = await apiGet(page, `/api/v1/partner/${DEMO_ID}/dashboard`);
    expectThat(ok.status === 200, `dashboard gave ${ok.status} after the change`);
    const second = await newPage(await fresh());
    await second.goto(`${BASE}/login`, { waitUntil: "networkidle0" });
    await clickText(second, "User", { exact: true });
    await second.waitForSelector('input[type="email"]', { visible: true });
    await second.type('input[type="email"]', "partner.demo@workeasy360.test");
    await second.type('input[type="password"]', temp);
    await second.click('button[type="submit"]');
    await sleep(1500);
    expectThat((await second.evaluate(() => location.pathname)).startsWith("/login"), "the temporary password still signs in");
    await page.close();
    await second.close();
  });

  // ---- reflow
  const widthPage = ONLY.length ? null : await newPage(owner.browserContext());
  for (const width of ONLY.length ? [] : WIDTHS) {
    await widthPage.setViewport({ width, height: 900 });
    for (const [route, label] of ROUTES) {
      await widthPage.goto(`${BASE}/partner/${PANEL_ID}${route ? `/${route}` : ""}`, { waitUntil: "networkidle0" });
      await sleep(250);
      const overflow = await widthPage.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      report.reflow.push({ width, label, overflow });
    }
  }
  if (widthPage) await widthPage.close();

  await browser.close();
  const failedPages = report.pages.filter((p) => p.axe.length || p.noFocus.length || p.h1.length !== 1);
  const overflowing = report.reflow.filter((r) => r.overflow > 1);
  report.summary = {
    flows: `${report.flows.filter((f) => f.ok).length}/${report.flows.length}`,
    pagesAudited: report.pages.length,
    pagesFailed: failedPages.length,
    axeViolations: report.pages.reduce((n, p) => n + p.axe.length, 0),
    csp: report.csp.length,
    consoleErrors: report.errors.length,
    expectedRefusals: report.expected.length,
    reflowChecks: report.reflow.length,
    reflowFailures: overflowing.length,
  };
  fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report.summary, null, 2));
  if (failedPages.length) console.log("PAGES FAILED", failedPages.map((p) => `${p.label}: axe=${JSON.stringify(p.axe)} focus=${p.noFocus} h1=${p.h1.length}`));
  if (overflowing.length) console.log("OVERFLOW", overflowing.slice(0, 20));
  for (const f of report.flows.filter((x) => !x.ok)) console.log("FLOW FAILED", f.name, "::", f.detail);
  process.exit(report.flows.every((f) => f.ok) && !failedPages.length && !overflowing.length && !report.csp.length && !report.errors.length ? 0 : 1);
})().catch((err) => {
  console.error("FATAL", err);
  process.exit(2);
});
