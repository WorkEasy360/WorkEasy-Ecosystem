// Real-browser verification of admin-created partners and the type-specific
// workspaces (ADR-047), against the production build and a real backend. Same
// harness and rules as ../partner_panel_browser_check:
//   - CSP violations and console errors on every audited page;
//   - axe WCAG 2.1 A/AA, a visible keyboard focus indicator, exactly one h1;
//   - reflow (no horizontal scroll) at 1440, 1024, 768, 390 and 320 px;
// and the brief's journeys, driven through the UI:
//   A. Super Admin adds a RESELLER, signs out, and the partner signs in at the
//      partner portal with the exact email and password typed -> Reseller dashboard
//   B. ... a WHITE-LABEL partner -> White-label dashboard with brand / domain status
//   C. ... an AFFILIATE -> Affiliate dashboard (referral link, copy, tracking link)
//   D. capability isolation: the affiliate's API calls and manual addresses
//   E. the password: weak, mismatch, an existing email (link / cancel), nothing in
//      browser storage, the password only in two requests
//   F. suspension and reactivation; the admin list; adding an experience later
// Not part of CI. A manual screen-reader pass is NOT part of it: NOT EXECUTED.
const puppeteer = require("puppeteer-core");
const crypto = require("crypto");
const fs = require("fs");

const BASE = process.env.BASE || "http://127.0.0.1:3999";
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const AXE = fs.readFileSync(process.env.AXE_PATH, "utf8");
// Made fresh for every run by whoever starts the check (the scratch database is seeded with it).
const PASSWORD = process.env.CHECK_PASSWORD;
if (!PASSWORD) throw new Error("CHECK_PASSWORD is required");
const { STAFF_TOTP, OUT } = process.env;
// The password Super Admin types for each partner administrator: unrelated to the
// staff password, different per partner, never fixed in the repository.
const PARTNER_PASSWORD = (tag) => `${crypto.randomBytes(9).toString("hex")} ${tag} ${crypto.randomBytes(3).toString("hex")}`;
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
  // Let CSS transitions settle: a button that has just become enabled is mid-fade,
  // and axe would measure the colour of the blend.
  await sleep(450);
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
  // Select everything and delete it: a field may already hold text (a display name
  // that followed the business name, an address from an earlier attempt).
  await handle.focus();
  await page.keyboard.down("Control");
  await page.keyboard.press("KeyA");
  await page.keyboard.up("Control");
  await page.keyboard.press("Backspace");
  const left = await handle.evaluate((el) => el.value);
  if (left !== "") throw new Error(`could not clear "${labelText}": it still holds "${left}"`);
  if (value !== "") await handle.type(value);
}

async function choose(page, labelText, optionText, opts) {
  const handle = await fieldHandle(page, labelText, opts);
  // The options of a list can arrive after the list itself: wait for the one asked for.
  const deadline = Date.now() + 10000;
  let value = null;
  for (;;) {
    value = await handle.evaluate((el, text) => {
      const option = [...el.options].find((o) => o.text === text || o.value === text);
      return option ? option.value : null;
    }, optionText);
    if (value !== null || Date.now() > deadline) break;
    await sleep(250);
  }
  if (value === null) throw new Error(`no option "${optionText}" in "${labelText}"`);
  await handle.select(value);
}

async function tick(page, text, on = true, { legend = null, timeout = 10000 } = {}) {
  // A step's heading shows before the list under it has loaded: wait for the choice.
  const deadline = Date.now() + timeout;
  for (;;) {
    const done = await page.evaluate(
      (text, on, legend) => {
        // textContent, lower-cased: a card title may be drawn in capitals by the stylesheet.
        const clean = (s) => (s || "").replace(/\s+/g, " ").trim().toLowerCase();
        let roots = [document.body];
        if (legend) roots = [...document.querySelectorAll("fieldset")].filter((f) => clean(f.querySelector("legend") && f.querySelector("legend").textContent).includes(legend.toLowerCase()));
        for (const root of roots) {
          for (const label of root.querySelectorAll("label")) {
            if (!clean(label.textContent).includes(text.toLowerCase())) continue;
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
    if (done) return;
    if (Date.now() > deadline) throw new Error(`no enabled choice "${text}"${legend ? ` in "${legend}"` : ""} on ${page.url()}`);
    await sleep(250);
  }
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

// ONLY=A2,H runs only the journeys whose names start with those codes. Later journeys
// need the partners an earlier one created, so run A-C together before the rest.
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


// ------------------------------------------------------------------ waiting --

/** Wait until `predicate(arg)` is true in the page, answering the Super Admin's
 * second-factor prompt whenever one appears (a step-up can interrupt any command). */
async function until(page, predicate, arg, timeout = 90000, what = "the page to finish") {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    let done = false;
    try {
      done = await page.evaluate(predicate, arg);
    } catch {
      /* the page is navigating */
    }
    if (done) return;
    try {
      await answerPrompt(page, STAFF_TOTP, 1500);
    } catch {
      /* not a prompt */
    }
    await sleep(300);
  }
  const shown = await bodyText(page).catch(() => "");
  throw new Error(`gave up waiting for ${what}; ${page.url()} :: ${shown.slice(0, 400).replace(/\n+/g, " | ")}`);
}

const untilText = (page, text, timeout, what) =>
  until(page, (t) => document.body.innerText.includes(t), text, timeout, what || `"${text}"`);

/** Click a step button and wait for the next step's heading. */
async function advance(page, button, expected) {
  await clickText(page, button, { exact: true });
  await untilText(page, expected, 90000);
}

// ------------------------------------------------------------------ sign-in --

async function signInMode(page, mode, email, password) {
  await page.goto(`${BASE}/login?mode=${mode}`, { waitUntil: "networkidle0" });
  await page.waitForSelector('input[type="email"]', { visible: true, timeout: 15000 });
  await page.type('input[type="email"]', email);
  await page.type('input[type="password"]', password);
  await page.click('button[type="submit"]');
}

async function staffSignIn(page) {
  await signInMode(page, "admin", "staff@browser.test", PASSWORD);
  await until(page, () => location.pathname.startsWith("/admin"), null, 90000, "the Super Admin console");
  await page.waitForNetworkIdle({ idleTime: 700, timeout: 20000 }).catch(() => {});
}

const WORKSPACE_HOME = /^\/partner\/(reseller|white-label|affiliate)\/dashboard$/;

/** The person signs in on the partner portal with exactly what was typed for them. */
async function partnerSignIn(page, email, password) {
  await signInMode(page, "partner", email, password);
  await page.waitForFunction((re) => new RegExp(re).test(location.pathname), { timeout: 40000 }, WORKSPACE_HOME.source);
  await page.waitForNetworkIdle({ idleTime: 700, timeout: 20000 }).catch(() => {});
}

async function signOut(page) {
  await page.click('button[aria-haspopup="menu"]');
  await clickText(page, "Sign out", { exact: true });
  await page.waitForFunction(() => location.pathname.startsWith("/login"), { timeout: 20000 });
}

const sidebarLabels = (page) =>
  page.evaluate(() =>
    [...document.querySelectorAll('nav[aria-label$=" workspace"] a')].map((a) => a.innerText.replace(/\s+/g, " ").trim()),
  );

const post = (page, path, body) =>
  page.evaluate(
    async (path, body) => {
      const csrf = (document.cookie.match(/(?:^|; )(?:__Host-)?w360_csrf=([^;]*)/) || [])[1];
      const r = await fetch(path, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrf ? decodeURIComponent(csrf) : "",
          "Idempotency-Key": (crypto.randomUUID && crypto.randomUUID()) || String(Math.random()),
        },
        body: JSON.stringify(body || {}),
      });
      const text = await r.text();
      let json = null;
      try {
        json = JSON.parse(text);
      } catch {
        json = text.slice(0, 200);
      }
      return { status: r.status, body: json };
    },
    path,
    body,
  );

/** Something real was rendered: not a load error, not a refusal. */
async function expectRendered(page, label) {
  const text = await bodyText(page);
  expectThat(text.trim().length > 40, `${label}: the page is empty`);
  expectThat(!/Couldn.t load|could not be loaded|You don.t have access|That page does not exist/i.test(text), `${label}: ${text.slice(0, 160).replace(/\n+/g, " | ")}`);
}

// ------------------------------------------------------------------ what was typed --

/** Every password typed in this run -> where it was typed. */
const secrets = new Map();
const track = (value, where) => secrets.set(value, where);
const carried = []; // { path, where } a request whose body or address held one
const echoed = []; // { path, where } a response that repeated one
const cacheControl = {}; // path -> Cache-Control of the last answer

const SENSITIVE = ["/api/v1/admin/partners/credentials-check", "/api/v1/admin/partners/create"];

function watchPasswords(page) {
  page.on("request", (req) => {
    let address = req.url();
    try {
      address += ` ${decodeURIComponent(req.url())}`;
    } catch {
      /* malformed escape */
    }
    const haystack = `${req.postData() || ""} ${address}`;
    for (const [value, where] of secrets) {
      if (haystack.includes(value)) carried.push({ path: new URL(req.url()).pathname, where });
    }
  });
  page.on("response", async (res) => {
    const path = new URL(res.url()).pathname;
    if (!path.startsWith("/api/v1/admin/partners/")) return;
    cacheControl[path] = res.headers()["cache-control"] || "";
    try {
      const text = await res.text();
      for (const [value, where] of secrets) if (text.includes(value)) echoed.push({ path, where });
    } catch {
      /* a redirect or a closed page has no body */
    }
  });
}

/** Everything a script on the page (or a person reading the address bar) could see. */
async function storageHolds(page, needles) {
  const cookies = (await page.cookies()).map((c) => c.value);
  return page.evaluate(
    (needles, cookies) => {
      const dump = [document.cookie, ...cookies, ...Object.entries(localStorage).flat(), ...Object.entries(sessionStorage).flat(), location.href, document.referrer];
      for (const entry of performance.getEntriesByType("resource")) dump.push(entry.name);
      return needles.filter((n) => dump.some((d) => String(d).includes(n)));
    },
    needles,
    cookies,
  );
}

/** No horizontal scroll at the five widths, for the page as it stands. */
async function reflowHere(page, label) {
  for (const width of WIDTHS) {
    await page.setViewport({ width, height: 900 });
    await sleep(250);
    const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    report.reflow.push({ url: label, width, over });
  }
  await page.setViewport({ width: 1280, height: 900 });
}

// ------------------------------------------------------------------ Add Partner --

const SELLER_STEPS = ["Business details", "Login credentials", "Partner type", "Products", "Commission", "Pricing", "Review"];
const AFFILIATE_STEPS = ["Business details", "Login credentials", "Partner type", "Products to promote", "Commission", "Review"];

/** The heading of step `index` as a person arriving at it sees it. Until a type is
 * chosen (steps 1-3) the pricing step is counted, so the total is 7 for everyone. */
function headingFor(index, type) {
  const titles = type === "Affiliate" ? AFFILIATE_STEPS : SELLER_STEPS;
  return `Step ${index + 1} of ${index < 3 ? 7 : titles.length}: ${titles[index]}`;
}

async function fillBusiness(page, spec) {
  await waitForText(page, headingFor(0, spec.type));
  await type(page, "Business / partner name", spec.legal);
  await type(page, "Display name", spec.display);
  await type(page, "Primary contact name", spec.contact);
  await type(page, "Email", spec.email);
  await choose(page, "Country", "GB");
}

/** Drive the Add Partner screen as a person would, to the Create button. */
async function walkToReview(page, spec) {
  await page.goto(`${BASE}/admin/partners/new`, { waitUntil: "networkidle0" });
  await fillBusiness(page, spec);
  await advance(page, "Save & continue", headingFor(1, spec.type));
  await type(page, "Partner admin email", spec.adminEmail);
  await type(page, "Password", spec.password);
  await type(page, "Confirm password", spec.password);
  await advance(page, "Check & continue", headingFor(2, spec.type));
  await tick(page, spec.type);
  await advance(page, "Save & continue", headingFor(3, spec.type));
  for (const product of spec.products) await tick(page, product);
  await advance(page, "Save & continue", headingFor(4, spec.type));
  await advance(page, "Save & continue", headingFor(5, spec.type));
  if (spec.type !== "Affiliate") await advance(page, "Save & continue", headingFor(6, spec.type));
  await waitForText(page, "Configured");
}

/** Create the partner through the UI and return where it lives. */
async function addPartnerViaUi(page, spec) {
  track(spec.password, `${spec.display} administrator`);
  await walkToReview(page, spec);
  const review = await bodyText(page);
  expectThat(!review.includes(spec.password), "the review shows the password");
  const held = await page.evaluate((pw) => [...document.querySelectorAll("input")].some((i) => i.value.includes(pw)), spec.password);
  expectThat(!held, "an input on the review still holds the password");
  expectThat(review.includes("Configured"), "the review does not say the password is configured");
  await clickText(page, "Create Partner", { exact: true });
  await untilText(page, "Partner created", 120000);
  const href = await page.evaluate(() => {
    const a = [...document.querySelectorAll("a")].find((x) => /^\/admin\/partners\/[0-9a-f-]{36}$/.test(x.getAttribute("href") || ""));
    return a ? a.getAttribute("href") : "";
  });
  const id = href.split("/").pop();
  expectThat(/^[0-9a-f-]{36}$/.test(id), `no partner id on the success page (${href})`);
  const text = await bodyText(page);
  expectThat(text.includes(spec.adminEmail), "the success page does not name the administrator");
  expectThat(text.includes(`They land on the ${spec.type} workspace`), `the success page does not name the workspace: ${text.slice(0, 300)}`);
  expectThat(!text.includes(spec.password), "the success page shows the password");
  expectThat((await page.$$('input[type="password"]')).length === 0, "a password field is still on the success page");
  return { id, href };
}

const SPECS = {
  reseller: { type: "Reseller", legal: "Acme Reseller Private Ltd", display: "Acme Reseller", contact: "Rhea Seller", email: "hello@acme-reseller.browser.test", adminEmail: "owner@acme-reseller.browser.test", products: ["Easy CRM", "EasyBook"] },
  whiteLabel: { type: "White-label", legal: "Bright Brand Private Ltd", display: "Bright Brand", contact: "Wanda White", email: "hello@bright-brand.browser.test", adminEmail: "owner@bright-brand.browser.test", products: ["Easy CRM"] },
  affiliate: { type: "Affiliate", legal: "Link Lane Media", display: "Link Lane", contact: "Alma Affiliate", email: "hello@link-lane.browser.test", adminEmail: "owner@link-lane.browser.test", products: ["Easy CRM"] },
};
for (const [key, spec] of Object.entries(SPECS)) spec.password = PARTNER_PASSWORD(key);

const NAV = {
  reseller: ["Dashboard", "Customers", "Products & Plans", "Pricing & Margins", "Sales & Orders", "Subscriptions", "Commissions", "Payouts", "Sub-Resellers", "Team & Permissions", "Reports & Analytics", "Support", "Settings"],
  whiteLabel: ["Dashboard", "Brand Settings", "Custom Domain", "Website Branding", "Products & Plans", "Pricing & Plans", "Customers", "Subscriptions", "Orders & Billing", "Commissions", "Payouts", "Team & Permissions", "Reports & Analytics", "Support", "Settings"],
  affiliate: ["Dashboard", "My Products", "Referral Links", "Referral Codes", "Campaigns", "Marketing Materials", "Leads & Referrals", "Conversions", "Commissions", "Payouts", "Reports & Analytics", "Profile & Settings", "Support"],
};

const made = {}; // key -> { id, href, page? }
const needs = (key, journey) => {
  if (!made[key]) throw new Error(`needs journey ${journey} to have created the ${key} partner`);
};

// ------------------------------------------------------------------ main --

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--no-sandbox"] });
  const fresh = () => browser.createBrowserContext();

  // The persistent Super Admin session used by the later journeys.
  const staff = { context: await fresh(), signedIn: false };
  staff.page = await newPage(staff.context);
  watchPasswords(staff.page);
  const staffReady = async () => {
    if (staff.signedIn) return;
    await staffSignIn(staff.page);
    staff.signedIn = true;
  };

  // The reseller's owner, kept signed in for the later journeys.
  const reseller = { context: await fresh() };
  reseller.page = await newPage(reseller.context);

  // ---------------------------------------------------------------- A. reseller
  await flow("A Super Admin adds a reseller; the same email and password sign in and land on the Reseller dashboard", async () => {
    const context = await fresh();
    const page = await newPage(context);
    watchPasswords(page);
    await staffSignIn(page);
    await visit(page, "/admin/partners/all", "admin partners (before)");
    expectThat((await bodyText(page)).includes("+ Add Partner"), "the + Add Partner action is not on the Partners list");
    const spec = SPECS.reseller;
    made.reseller = await addPartnerViaUi(page, spec);
    // The administrator signs out; the partner signs in with exactly what was typed.
    await signOut(page);
    await partnerSignIn(page, spec.adminEmail, spec.password);
    expectThat(new URL(page.url()).pathname === "/partner/reseller/dashboard", `landed on ${page.url()}`);
    await waitForText(page, "Welcome, Acme Reseller");
    const labels = await sidebarLabels(page);
    expectThat(JSON.stringify(labels) === JSON.stringify(NAV.reseller), `Reseller menu: ${JSON.stringify(labels)}`);
    const portal = await apiGet(page, "/api/v1/partner/portal");
    const me = portal.body.partners[0];
    expectThat(me.landing === "reseller" && me.default_experience === "reseller", `the server did not resolve the reseller landing: ${me.landing}/${me.default_experience}`);
    expectThat(me.workspaces.length === 1, "a switcher would be offered to a single-workspace partner");
    expectThat(!(await page.$('nav[aria-label="Workspace"]')), "a workspace switcher is shown");
    expectThat(!JSON.stringify(portal.body).includes(spec.password), "the password is in the portal answer");
    await audit(page, "reseller dashboard");
    await page.close();
    await context.close();
  });

  await flow("A2 the reseller signs in again; its pages open and the friendly addresses resolve", async () => {
    needs("reseller", "A");
    const spec = SPECS.reseller;
    await partnerSignIn(reseller.page, spec.adminEmail, spec.password);
    for (const [route, label] of [
      ["users", "customers"],
      ["users/subscriptions", "subscriptions"],
      ["partner-products", "products"],
      ["plans", "pricing"],
      ["payments", "orders"],
      ["payments/commissions", "commissions"],
      ["payouts", "payouts"],
      ["agency", "sub-resellers"],
      ["team", "team"],
      ["reports", "reports"],
      ["account", "settings"],
      ["support-manager", "support"],
    ]) {
      await visit(reseller.page, `/partner/reseller/${route}`, `reseller ${label}`);
      await expectRendered(reseller.page, `reseller ${label}`);
    }
    // A friendly address opens the page it names.
    await reseller.page.goto(`${BASE}/partner/reseller/customers`, { waitUntil: "networkidle0" });
    await reseller.page.waitForFunction(() => location.pathname.endsWith("/users"), { timeout: 15000 });
    // And the short address a person would type.
    await reseller.page.goto(`${BASE}/reseller/dashboard`, { waitUntil: "networkidle0" });
    expectThat(new URL(reseller.page.url()).pathname === "/partner/reseller/dashboard", `short address went to ${reseller.page.url()}`);
    // The affiliate-only pages are not part of this workspace.
    await reseller.page.goto(`${BASE}/partner/reseller/referral-links`, { waitUntil: "networkidle0" });
    await waitForText(reseller.page, "That page is not part of the Reseller workspace.");
  });

  // ---------------------------------------------------------------- B. white-label
  await flow("B Super Admin adds a white-label partner; it lands on the White-label dashboard", async () => {
    await staffReady();
    const spec = SPECS.whiteLabel;
    made.whiteLabel = await addPartnerViaUi(staff.page, spec);
    const context = await fresh();
    const page = await newPage(context);
    await partnerSignIn(page, spec.adminEmail, spec.password);
    expectThat(new URL(page.url()).pathname === "/partner/white-label/dashboard", `landed on ${page.url()}`);
    await waitForText(page, "Welcome, Bright Brand");
    await waitForText(page, "White-label status");
    const text = await bodyText(page);
    for (const needle of ["Brand setup", "Not started", "Custom domain", "No domain requested"]) {
      expectThat(text.includes(needle), `the white-label status does not show "${needle}"`);
    }
    const labels = await sidebarLabels(page);
    expectThat(JSON.stringify(labels) === JSON.stringify(NAV.whiteLabel), `White-label menu: ${JSON.stringify(labels)}`);
    await audit(page, "white-label dashboard");
    for (const [route, label] of [
      ["brand-settings", "brand settings"],
      ["brand-settings/url-manager", "custom domain"],
      ["brand-settings/white-label-setup", "website branding"],
      ["partner-products", "wl products"],
      ["users", "wl customers"],
      ["users/subscriptions", "wl subscriptions"],
      ["plans", "wl pricing"],
      ["payments", "wl orders"],
      ["payments/commissions", "wl commissions"],
      ["payouts", "wl payouts"],
      ["team", "wl team"],
      ["reports", "wl reports"],
      ["account", "wl settings"],
      ["support-manager", "wl support"],
    ]) {
      await visit(page, `/partner/white-label/${route}`, label);
      await expectRendered(page, label);
    }
    made.whiteLabel.page = page;
  });

  // ---------------------------------------------------------------- C. affiliate
  await flow("C Super Admin adds an affiliate; it lands on the Affiliate dashboard with a referral link to copy", async () => {
    await staffReady();
    const spec = SPECS.affiliate;
    made.affiliate = await addPartnerViaUi(staff.page, spec);
    const context = await fresh();
    await context.overridePermissions(BASE, ["clipboard-read", "clipboard-write", "clipboard-sanitized-write"]);
    const page = await newPage(context);
    await partnerSignIn(page, spec.adminEmail, spec.password);
    expectThat(new URL(page.url()).pathname === "/partner/affiliate/dashboard", `landed on ${page.url()}`);
    await waitForText(page, "Welcome, Link Lane");
    const labels = await sidebarLabels(page);
    expectThat(JSON.stringify(labels) === JSON.stringify(NAV.affiliate), `Affiliate menu: ${JSON.stringify(labels)}`);
    // A brand-new affiliate has no link yet.
    await waitForText(page, "You have no active referral link yet.");
    const first = await bodyText(page);
    expectThat(first.includes("Conversion rate") && first.includes("No clicks in this period"), "a rate is shown with no clicks");
    expectThat(first.includes("Signups") && first.includes("no sign-up step"), "Signups is not stated as unavailable");
    await audit(page, "affiliate dashboard (no link yet)");
    // Create one, then copy it.
    await visit(page, "/partner/affiliate/referral-links", "referral links (empty)");
    await choose(page, "Campaign", "LinkedIn");
    await clickText(page, "Create tracking link", { exact: true });
    await waitForText(page, "Tracking link created for LinkedIn.");
    await visit(page, "/partner/affiliate/dashboard", "affiliate dashboard (with a link)");
    const link = await page.$eval("input[readonly]", (el) => el.value).catch(() => "");
    expectThat(/\/signup\?ref=[A-Za-z0-9_-]{4,}$/.test(link), `the referral link is not a sign-up link: ${link}`);
    await page.bringToFront();
    await clickText(page, "Copy Link", { exact: true });
    await untilText(page, "Copied.", 8000).catch(async () => {
      if ((await bodyText(page)).includes("Could not copy")) report.notes.push("the browser refused clipboard access in headless mode: the copy confirmation was not exercised");
      else throw new Error("neither a copy confirmation nor a refusal was shown");
    });
    if ((await bodyText(page)).includes("Copied.")) {
      const copied = await page.evaluate(() => navigator.clipboard.readText()).catch(() => null);
      if (copied !== null) expectThat(copied === link, `the clipboard holds "${copied}", not the link`);
    }
    for (const [route, label] of [
      ["referral-links", "referral links"],
      ["referral-codes", "referral codes"],
      ["campaigns", "campaigns"],
      ["marketing-materials", "marketing materials"],
      ["leads", "leads"],
      ["conversions", "conversions"],
      ["payments/commissions", "aff commissions"],
      ["payouts", "aff payouts"],
      ["reports", "aff reports"],
      ["account", "aff profile"],
      ["support-manager", "aff support"],
    ]) {
      await visit(page, `/partner/affiliate/${route}`, label);
      await expectRendered(page, label);
    }
    // The marketing page says plainly that there is no library.
    await page.goto(`${BASE}/partner/affiliate/marketing-materials`, { waitUntil: "networkidle0" });
    await waitForText(page, "no shared marketing library yet");
    made.affiliate.page = page;
  });

  // ---------------------------------------------------------------- D. isolation
  await flow("D an affiliate cannot reach customers, pricing, subscriptions or another partner - by API or by address", async () => {
    needs("affiliate", "C");
    needs("reseller", "A");
    const page = made.affiliate.page;
    const id = made.affiliate.id;
    const other = made.reseller.id;
    const refused = [400, 401, 403, 404];
    for (const path of ["customers", "customers/export", "catalog", "plans", "price-book", "currencies", "packages", "orders", "sub-partners", "branding", "domains"]) {
      const r = await apiGet(page, `/api/v1/partner/${id}/${path}`);
      expectThat(refused.includes(r.status), `GET ${path} answered ${r.status}`);
    }
    const stranger = "00000000-0000-4000-8000-000000000001";
    const mutations = [
      [`/api/v1/partner/${id}/customers`, { organization_name: "Smuggled Ltd", owner_email: "x@example.com" }],
      [`/api/v1/partner/${id}/sub-partners`, { code: "smuggled", display_name: "Smuggled" }],
      [`/api/v1/partner/${id}/domains`, { hostname: "evil.example.com" }],
      [`/api/v1/partner/${id}/customers/${stranger}/sell`, { plan_version_id: stranger, quantity: 1 }],
      [`/api/v1/partner/${id}/customers/${stranger}/billing`, { currency: "GBP" }],
      [`/api/v1/partner/${id}/customers/${stranger}/subscriptions/${stranger}/cancel`, {}],
      [`/api/v1/partner/${id}/customers/${stranger}/items/${stranger}/change-plan`, { to_plan_version_id: stranger, expected_version: 0 }],
      [`/api/v1/admin/partners/${id}/prices`, { plan_version_id: stranger, interval: "monthly", amount_minor: 1 }],
      [`/api/v1/admin/partners/${id}/commission`, { rate_bp: 9000, reason: "Raise my own rate", expected_version: 0 }],
      [`/api/v1/admin/partners/${id}/capabilities`, { capability: "customer.manage", reason: "Give me more access" }],
      [`/api/v1/admin/partners/${id}/experience`, { action: "grant", experience: "reseller", reason: "Make me a reseller" }],
    ];
    for (const [path, body] of mutations) {
      const r = await post(page, path, body);
      expectThat(refused.includes(r.status), `POST ${path} answered ${r.status}`);
    }
    // Another partner's data, named by id: not found.
    for (const path of ["dashboard", "customers", "referrals", "reports", "members"]) {
      const r = await apiGet(page, `/api/v1/partner/${other}/${path}`);
      expectThat(r.status === 404, `another partner's ${path} answered ${r.status}`);
    }
    // The reseller workspace by address - the long one and the short one.
    for (const url of ["/partner/reseller/users", "/reseller/customers", "/partner/reseller/dashboard"]) {
      await page.goto(`${BASE}${url}`, { waitUntil: "networkidle0" });
      await waitForText(page, "You don't have access to the Reseller workspace.");
      expectThat((await sidebarLabels(page)).length === 0, `${url}: a sidebar is drawn for a workspace the account does not hold`);
    }
    await audit(page, "refused: reseller workspace");
    await page.goto(`${BASE}/partner/white-label/dashboard`, { waitUntil: "networkidle0" });
    await waitForText(page, "You don't have access to the White-label workspace.");
    // A commercial page typed into the affiliate workspace is not part of it.
    for (const url of ["/partner/affiliate/users", "/partner/affiliate/plans", "/partner/affiliate/brand-settings", "/partner/affiliate/agency"]) {
      await page.goto(`${BASE}${url}`, { waitUntil: "networkidle0" });
      await waitForText(page, "That page is not part of the Affiliate workspace.");
    }
    await audit(page, "refused: page outside the workspace");
    // The generic panel's address for the same account may open its page shell, but the data is refused.
    const answers = [];
    page.on("response", (res) => {
      if (res.url().includes(`/partner/${id}/customers`)) answers.push(res.status());
    });
    await page.goto(`${BASE}/partner/${id}/users`, { waitUntil: "networkidle0" });
    await sleep(800);
    expectThat(answers.every((s) => refused.includes(s)), `the generic customers address answered ${answers}`);
    expectThat(!/Smuggled|Acme Reseller/.test(await bodyText(page)), "customer data reached the affiliate");
    // And the reseller cannot open the affiliate's pages either.
    await reseller.page.goto(`${BASE}/partner/affiliate/dashboard`, { waitUntil: "networkidle0" });
    await waitForText(reseller.page, "You don't have access to the Affiliate workspace.");
    await reseller.page.goto(`${BASE}/partner/white-label/dashboard`, { waitUntil: "networkidle0" });
    await waitForText(reseller.page, "You don't have access to the White-label workspace.");
  });

  // ---------------------------------------------------------------- E. the password
  const GOOD = "Violet Harbor Lantern 2049";
  await flow("E a weak, mismatched or common password is refused; an existing email is linked or cancelled, never overwritten", async () => {
    await staffReady();
    const page = staff.page;
    for (const [value, where] of [["short1", "too short"], [GOOD, "typed for a taken address"], ["Violet Harbor Lantern 2050", "mismatch"], ["password1234", "common"]]) track(value, where);
    await page.goto(`${BASE}/admin/partners/new`, { waitUntil: "networkidle0" });
    const spec = { type: "Reseller", legal: "Password Probe Ltd", display: "Password Probe", contact: "Pat Probe", email: "hello@password-probe.browser.test", adminEmail: "existing.customer@browser.test", products: ["Easy CRM"] };
    await fillBusiness(page, spec);
    await advance(page, "Save & continue", headingFor(1, spec.type));
    // Attributes, guidance and the strength hint.
    const attrs = await page.evaluate(() => [...document.querySelectorAll('input[type="password"]')].map((i) => ({ ac: i.autocomplete, req: i.required })));
    expectThat(attrs.length === 2 && attrs.every((a) => a.ac === "new-password" && a.req), `password inputs: ${JSON.stringify(attrs)}`);
    await type(page, "Partner admin email", "weak@password-probe.browser.test");
    await type(page, "Password", "short1");
    await type(page, "Confirm password", "short1");
    const guidance = await bodyText(page);
    expectThat(guidance.includes("Strength:") && guidance.includes("Too short") && guidance.includes("At least 12 characters"), "the strength and requirements are not shown");
    // Short.
    await clickText(page, "Check & continue", { exact: true });
    await waitForText(page, "must be at least 12 characters");
    // Mismatch.
    await type(page, "Password", GOOD);
    await type(page, "Confirm password", "Violet Harbor Lantern 2050");
    await clickText(page, "Check & continue", { exact: true });
    await waitForText(page, "do not match");
    // Common: long enough, refused by the platform's own policy.
    await type(page, "Password", "password1234");
    await type(page, "Confirm password", "password1234");
    await clickText(page, "Check & continue", { exact: true });
    await untilText(page, "This password is too common.", 60000, "the platform's refusal of a common password");
    // Show and hide.
    await clickText(page, "Show", { exact: true, within: "form" });
    expectThat((await page.$eval('input[autocomplete="new-password"]', (el) => el.type)) === "text", "Show did not reveal the password");
    await clickText(page, "Hide", { exact: true, within: "form" });
    expectThat((await page.$eval('input[autocomplete="new-password"]', (el) => el.type)) === "password", "Hide did not hide it again");
    await audit(page, "add partner: credentials (refused)");
    // An address that already has an account: say so, offer to link, change nothing.
    await type(page, "Partner admin email", "existing.customer@browser.test");
    await type(page, "Password", GOOD);
    await type(page, "Confirm password", GOOD);
    await clickText(page, "Check & continue", { exact: true });
    await untilText(page, "This email already belongs to an existing Workeasy360 account.", 60000);
    await clickText(page, "Cancel", { exact: true, within: '[role="alert"]' });
    expectThat((await page.$eval('input[type="email"]', (el) => el.value)) === "", "Cancel did not clear the address");
    expectThat((await page.$$eval('input[type="password"]', (els) => els.every((e) => e.value === ""))), "Cancel left a typed password in the form");
    // ... and link it instead.
    await type(page, "Partner admin email", "existing.customer@browser.test");
    await type(page, "Password", GOOD);
    await type(page, "Confirm password", GOOD);
    await clickText(page, "Check & continue", { exact: true });
    await untilText(page, "This email already belongs to an existing Workeasy360 account.", 60000);
    await clickText(page, "Link existing user as Partner Admin", { exact: true });
    await waitForText(page, "Existing account linked.");
    expectThat((await page.$$('input[type="password"]')).length === 0, "a password field is shown for a linked account");
    await advance(page, "Check & continue", headingFor(2, spec.type));
    await tick(page, "Reseller");
    await advance(page, "Save & continue", headingFor(3, spec.type));
    await tick(page, "Easy CRM");
    await advance(page, "Save & continue", headingFor(4, spec.type));
    await advance(page, "Save & continue", headingFor(5, spec.type));
    await reflowHere(page, "add partner: pricing step");
    await advance(page, "Save & continue", headingFor(6, spec.type));
    await waitForText(page, "the existing account keeps its own password");
    await audit(page, "add partner: review (linked account)");
    await reflowHere(page, "add partner: review step");
    await clickText(page, "Create Partner", { exact: true });
    await untilText(page, "Partner created", 120000);
    expectThat(/signs in with the password they already have/.test(await bodyText(page)), "the success page does not say the existing password stays");
    // That person's own password still works - and the one typed here does not.
    const wrong = await fresh();
    const w = await newPage(wrong);
    await signInMode(w, "partner", "existing.customer@browser.test", GOOD);
    await sleep(3000);
    expectThat(new URL(w.url()).pathname.startsWith("/login"), `the password typed for a taken address signed the account in: ${w.url()}`);
    await w.close();
    await wrong.close();
    const right = await fresh();
    const p = await newPage(right);
    await partnerSignIn(p, "existing.customer@browser.test", PASSWORD);
    expectThat(new URL(p.url()).pathname === "/partner/reseller/dashboard", `the linked account landed on ${p.url()}`);
    await p.close();
    await right.close();
    // What the browser kept.
    const held = await storageHolds(page, [...secrets.keys()]);
    expectThat(held.length === 0, `the browser kept a typed password: ${held.join(", ")}`);
  });

  await flow("E2 a typed password reached the credentials check, the create request and the sign-in, and nowhere else", async () => {
    const allowed = new Set([...SENSITIVE, "/api/v1/auth/login"]);
    const strays = carried.filter((c) => !allowed.has(c.path));
    expectThat(strays.length === 0, `a password was sent to ${[...new Set(strays.map((s) => s.path))].join(", ")}`);
    const paths = new Set(carried.map((c) => c.path));
    for (const path of SENSITIVE) expectThat(paths.has(path), `no typed password reached ${path}`);
    expectThat(echoed.length === 0, `an answer repeated a password: ${[...new Set(echoed.map((e) => e.path))].join(", ")}`);
    for (const path of SENSITIVE) expectThat(/no-store/.test(cacheControl[path] || ""), `${path} is cacheable: "${cacheControl[path]}"`);
    return `${carried.length} requests carried a typed password; ${echoed.length} answers repeated one`;
  });

  // ---------------------------------------------------------------- F. the admin list
  await flow("F the Partners list shows the administrator, the primary experience, the capabilities and the last sign-in", async () => {
    needs("reseller", "A");
    await staffReady();
    await visit(staff.page, "/admin/partners/all", "admin partners (after)");
    await waitForText(staff.page, "Acme Reseller");
    const heads = await staff.page.evaluate(() => [...document.querySelectorAll("thead th")].map((h) => h.innerText.trim()));
    expectThat(JSON.stringify(heads) === JSON.stringify(["Partner", "Admin email", "Primary experience", "Capabilities", "Products", "Customers", "Status", "Created", "Last login", "Actions"]), `columns: ${JSON.stringify(heads)}`);
    // Cells by column: 0 admin email, 1 primary experience, 2 capabilities, 5 status, 7 last login.
    const rows = await staff.page.evaluate(() =>
      Object.fromEntries(
        [...document.querySelectorAll("tbody tr")].map((r) => {
          const cells = [...r.querySelectorAll("td")].map((c) => c.innerText.replace(/\s+/g, " ").trim());
          return [r.querySelector("th")?.innerText.split(String.fromCharCode(10))[0], { admin: cells[0], primary: cells[1], status: cells[5], last: cells[7] }];
        }),
      ),
    );
    const row = (name) => {
      expectThat(rows[name], `no row for ${name}: ${Object.keys(rows)}`);
      return rows[name];
    };
    expectThat(row("Acme Reseller").admin === "owner@acme-reseller.browser.test" && /^reseller$/i.test(row("Acme Reseller").primary), `reseller row: ${JSON.stringify(row("Acme Reseller"))}`);
    expectThat(row("Bright Brand").admin === "owner@bright-brand.browser.test" && /^white-label$/i.test(row("Bright Brand").primary), `white-label row: ${JSON.stringify(row("Bright Brand"))}`);
    expectThat(row("Link Lane").admin === "owner@link-lane.browser.test" && /^affiliate$/i.test(row("Link Lane").primary), `affiliate row: ${JSON.stringify(row("Link Lane"))}`);
    for (const name of ["Acme Reseller", "Bright Brand", "Link Lane"]) {
      expectThat(/active/i.test(row(name).status), `${name} is not active: ${row(name).status}`);
      expectThat(row(name).last && !/never/i.test(row(name).last), `${name} shows no last sign-in: ${row(name).last}`);
    }
    // The admin actions are reachable from the row.
    await staff.page.evaluate(() => document.querySelectorAll("tbody details").forEach((d) => (d.open = true)));
    const actions = await staff.page.evaluate(() => [...document.querySelectorAll("tbody tr:first-child a")].map((a) => a.innerText.trim()));
    for (const label of ["View", "Edit details", "Change capabilities and experience", "Products and pricing", "Commission", "Reset password"]) {
      expectThat(actions.includes(label), `no "${label}" action on a row: ${JSON.stringify(actions)}`);
    }
    await audit(staff.page, "admin partners (actions open)");
  });

  // ---------------------------------------------------------------- G. suspension
  await flow("G suspending a partner takes away its new actions and keeps its history; reactivating gives them back", async () => {
    needs("reseller", "A");
    await staffReady();
    const page = staff.page;
    const id = made.reseller.id;
    const r = reseller.page;
    await visit(page, `/admin/partners/${id}`, "partner detail (active)", { settle: "h1" });
    await waitForText(page, "Primary experience:");
    await clickText(page, "Suspend", { exact: true });
    await type(page, "Reason for", "Suspended by the browser check");
    await clickText(page, "Confirm", { exact: true });
    await untilText(page, "Partner suspended.", 90000);
    await r.goto(`${BASE}/partner/reseller/dashboard`, { waitUntil: "networkidle0" });
    await waitForText(r, "This partner account is suspended");
    const suspended = (await apiGet(r, "/api/v1/partner/portal")).body.partners[0];
    expectThat(suspended.status === "suspended", `portal status ${suspended.status}`);
    expectThat(!suspended.permissions.some((p) => /\.(provision|sell|manage)$/.test(p)), `a suspended partner still holds ${suspended.permissions.filter((p) => /\.(provision|sell|manage)$/.test(p))}`);
    expectThat((await apiGet(r, `/api/v1/partner/${id}/customers`)).status === 200, "a suspended partner lost sight of its history");
    expectThat((await sidebarLabels(r)).includes("Customers"), "a suspended partner lost its navigation");
    await audit(r, "suspended partner dashboard");
    await clickText(page, "Reactivate", { exact: true });
    await type(page, "Reason for", "Reactivated by the browser check");
    await clickText(page, "Confirm", { exact: true });
    await untilText(page, "Partner reactivated.", 90000);
    await r.reload({ waitUntil: "networkidle0" });
    expectThat(!(await bodyText(r)).includes("is suspended"), "the suspended notice remains after reactivation");
    const back = (await apiGet(r, "/api/v1/partner/portal")).body.partners[0];
    expectThat(back.status === "active" && back.landing === "reseller" && back.permissions.includes("platform.partner_customer.provision"), `reactivated: ${back.status}/${back.landing}`);
  });

  // ---------------------------------------------------------------- H. a second experience
  await flow("H adding an experience later gives a second workspace and a switcher, and takes nothing away", async () => {
    needs("affiliate", "C");
    await staffReady();
    const page = staff.page;
    const id = made.affiliate.id;
    await visit(page, `/admin/partners/${id}`, "partner detail (experience)", { settle: "h1" });
    await waitForText(page, "Primary experience:");
    await choose(page, "Experience", "Reseller");
    await sleep(300);
    await tick(page, "Easy CRM", true, { legend: "Products it may sell" });
    await type(page, "Why the experience is changing", "Became a reseller too, in the browser check");
    await clickText(page, "Apply", { exact: true });
    await untilText(page, "Experience added.", 90000);
    const a = made.affiliate.page;
    await a.goto(`${BASE}/partner/affiliate/dashboard`, { waitUntil: "networkidle0" });
    await waitForText(a, "Welcome, Link Lane");
    const switcher = await a.$$eval('nav[aria-label="Workspace"] a', (links) => links.map((l) => l.innerText.trim()));
    expectThat(JSON.stringify(switcher) === JSON.stringify(["Reseller", "Affiliate"]), `switcher: ${JSON.stringify(switcher)}`);
    // The affiliate workspace still has everything, and the reseller one now opens.
    const affiliateMenu = await sidebarLabels(a);
    expectThat(JSON.stringify(affiliateMenu) === JSON.stringify(NAV.affiliate), `the affiliate menu changed: ${JSON.stringify(affiliateMenu)}`);
    await a.goto(`${BASE}/partner/reseller/dashboard`, { waitUntil: "networkidle0" });
    await waitForText(a, "Welcome, Link Lane");
    const labels = await sidebarLabels(a);
    expectThat(labels.includes("Customers") && labels.includes("Pricing & Margins") && !labels.includes("Referral Links"), `reseller menu of the second workspace: ${JSON.stringify(labels)}`);
    await audit(a, "multi-workspace: reseller view");
    // The new primary experience is where the chooser lands.
    await a.goto(`${BASE}/partner`, { waitUntil: "networkidle0" });
    await a.waitForFunction(() => /\/partner\/reseller\/dashboard$/.test(location.pathname), { timeout: 20000 });
    // Its referral history is still there.
    const overview = await apiGet(a, `/api/v1/partner/${id}/affiliate/overview`);
    expectThat(overview.status === 200 && overview.body.links.length === 1, `the affiliate history changed: ${overview.status}`);
  });

  // ---------------------------------------------------------------- I. reset
  await flow("I Reset password sends the person a link and reveals nothing; the current password keeps working", async () => {
    needs("reseller", "A");
    await staffReady();
    const page = staff.page;
    const spec = SPECS.reseller;
    await visit(page, `/admin/partners/${made.reseller.id}?tab=users`, "partner users tab", { settle: "h1" });
    await waitForText(page, spec.adminEmail);
    await clickText(page, "Send password reset link");
    await untilText(page, "A password reset link was sent.", 90000);
    const text = await bodyText(page);
    expectThat(!text.includes(spec.password), "the page shows the current password");
    expectThat(text.includes("Their current password keeps working until they use it."), "the page does not say the current password stays");
    // Nothing was changed: the typed password still signs in.
    const context = await fresh();
    const p = await newPage(context);
    await partnerSignIn(p, spec.adminEmail, spec.password);
    expectThat(new URL(p.url()).pathname === "/partner/reseller/dashboard", `signed in to ${p.url()}`);
    await p.close();
    await context.close();
  });

  // ---------------------------------------------------------------- reflow and the wizard's audit
  await flow("R no horizontal scroll at 1440, 1024, 768, 390 and 320 for the workspaces and the admin pages", async () => {
    needs("affiliate", "C");
    needs("whiteLabel", "B");
    const groups = [
      [made.affiliate.page, ["dashboard", "referral-links", "referral-codes", "campaigns", "marketing-materials", "leads", "conversions", "reports", "account"].map((r) => `/partner/affiliate/${r}`)],
      [reseller.page, ["dashboard", "users", "plans", "payments/commissions", "reports"].map((r) => `/partner/reseller/${r}`)],
      [made.whiteLabel.page, ["dashboard", "brand-settings", "brand-settings/url-manager", "reports"].map((r) => `/partner/white-label/${r}`)],
      [staff.page, ["/admin/partners/all", "/admin/partners/new", `/admin/partners/${made.affiliate.id}`]],
    ];
    for (const [page, urls] of groups) {
      for (const url of urls) {
        for (const width of WIDTHS) {
          await page.setViewport({ width, height: 900 });
          await page.goto(`${BASE}${url}`, { waitUntil: "networkidle0", timeout: 30000 });
          await sleep(300);
          const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
          report.reflow.push({ url, width, over });
        }
        await page.setViewport({ width: 1280, height: 900 });
      }
    }
    const bad = report.reflow.filter((x) => x.over > 1);
    expectThat(bad.length === 0, `overflow: ${bad.map((b) => `${b.url}@${b.width}+${b.over}`).join(", ")}`);
    return `${report.reflow.length} checks`;
  });

  await flow("S the Add Partner steps pass axe, CSP, focus and reflow checks", async () => {
    await staffReady();
    const page = staff.page;
    const spec = { type: "Affiliate", legal: "Audit Probe Ltd", display: "Audit Probe", contact: "Pat Probe", email: "hello@audit-probe.browser.test", adminEmail: "audit@audit-probe.browser.test" };
    await page.goto(`${BASE}/admin/partners/new`, { waitUntil: "networkidle0" });
    await waitForText(page, headingFor(0, spec.type));
    await audit(page, "add partner: business");
    await reflowHere(page, "add partner: business step");
    await fillBusiness(page, spec);
    await advance(page, "Save & continue", headingFor(1, spec.type));
    await type(page, "Partner admin email", spec.adminEmail);
    await type(page, "Password", GOOD);
    await type(page, "Confirm password", GOOD);
    await audit(page, "add partner: credentials (filled)");
    await reflowHere(page, "add partner: credentials step");
    await advance(page, "Check & continue", headingFor(2, spec.type));
    await audit(page, "add partner: type");
    await reflowHere(page, "add partner: type step");
    await tick(page, spec.type);
    await advance(page, "Save & continue", headingFor(3, spec.type));
    await audit(page, "add partner: promote");
    await reflowHere(page, "add partner: promote step");
    await advance(page, "Save & continue", headingFor(4, spec.type));
    await audit(page, "add partner: commission");
    await reflowHere(page, "add partner: commission step");
    await advance(page, "Save & continue", headingFor(5, spec.type));
    await waitForText(page, "Configured");
    await audit(page, "add partner: review");
    await reflowHere(page, "add partner: review (affiliate)");
    const bad = report.reflow.filter((x) => x.over > 1 && x.url.startsWith("add partner"));
    expectThat(bad.length === 0, `overflow: ${bad.map((b) => `${b.url}@${b.width}+${b.over}`).join(", ")}`);
  });

  await browser.close();

  // ---------------------------------------------------------------- report
  const badPages = report.pages.filter((p) => p.axe.length || p.noFocus.length || p.h1.length !== 1);
  if (badPages.length) report.notes.push(`pages with axe/focus/h1 findings: ${badPages.map((b) => b.label).join(", ")}`);
  const failed = report.flows.filter((f) => !f.ok);
  const reflowFailures = report.reflow.filter((r) => r.over > 1);
  const summary = {
    flows: `${report.flows.filter((f) => f.ok).length}/${report.flows.length} passed`,
    pages: `${report.pages.length} audited, ${badPages.length} with findings`,
    csp: report.csp.length,
    errors: report.errors.length,
    reflowChecks: report.reflow.length,
    reflowFailures: reflowFailures.length,
  };
  console.log(JSON.stringify(summary, null, 2));
  for (const f of report.flows) console.log(`${f.ok ? "PASS" : "FAIL"}  ${f.name}${f.ok ? "" : `\n      ${f.detail}`}`);
  for (const p of badPages) console.log(`FINDING  ${p.label}: axe=${JSON.stringify(p.axe)} noFocus=${JSON.stringify(p.noFocus)} h1=${JSON.stringify(p.h1)}`);
  for (const c of report.csp) console.log(`CSP  ${c}`);
  for (const e of report.errors.slice(0, 20)) console.log(`ERROR  ${e}`);
  for (const n of report.notes) console.log(`NOTE  ${n}`);
  if (OUT) fs.writeFileSync(OUT, JSON.stringify({ summary, ...report }, null, 2));
  process.exit(failed.length || badPages.length || report.csp.length || report.errors.length || reflowFailures.length ? 1 : 0);
})().catch((err) => {
  console.error(err);
  process.exit(2);
});
