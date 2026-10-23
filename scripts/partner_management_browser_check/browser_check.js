// Real-browser verification of the simplified Super Admin partner management
// (ADR-048), against the production build and a real backend. Same harness and
// rules as ../partner_experience_browser_check:
//   - CSP violations and console errors on every audited page;
//   - axe WCAG 2.1 A/AA, a visible keyboard focus indicator, exactly one h1;
//   - reflow (no horizontal scroll) at 1440, 1024, 768, 390 and 320 px;
// and the brief's journeys, driven through the UI:
//   N. the Partners area holds only Dashboard, All Partners and Add Partner; the
//      old addresses redirect
//   A. Add Partner (name, email, password, type) -> Partner Detail -> Complete Your
//      Profile (reseller steps) -> activate -> the partner signs in -> Reseller workspace
//   B. ... White-label (brand, domain statement, communication) -> White-label workspace
//   C. ... Affiliate (referral code, commission) -> Affiliate workspace
//   F. All Partners: columns, badges, server-side filters, search by email
//   E. an existing email: Cancel / Link Existing User; its password is not changed
//   D. isolation; E2. where the typed passwords travelled
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

// ------------------------------------------------------------------ ADR-048 helpers --

/** innerText follows CSS text-transform (a title drawn in capitals reads in
 * capitals), so text is matched without regard to case. */
const untilTextCI = (page, text, timeout, what) =>
  until(page, (t) => document.body.textContent.toLowerCase().includes(t.toLowerCase()), text, timeout, what || `"${text}"`);

const textHas = async (page, text) => (await page.evaluate(() => document.body.textContent)).toLowerCase().includes(text.toLowerCase());

const centerNav = (page) =>
  page.evaluate(() => [...document.querySelectorAll('nav[aria-label="Partner Control Center"] a')].map((a) => a.innerText.replace(/\s+/g, " ").trim()));

const stepHeading = (page) => page.evaluate(() => (document.querySelector("main h2[tabindex='-1']") || {}).innerText || "");

/** Wait for the wizard's step heading to name `title`. */
const untilStep = (page, title) =>
  until(page, (t) => [...document.querySelectorAll("main h2")].some((h) => h.innerText.includes(t)), title, 60000, `the step "${title}"`);

/** A step with nothing of its own to save says "Continue"; a step that can save
 * says "Save & continue" and only moves on when nothing is unsaved. */
async function clickContinue(page, nextTitle) {
  const label = await page.evaluate(() =>
    [...document.querySelectorAll("main button")].some((b) => b.innerText.trim() === "Save & continue") ? "Save & continue" : "Continue",
  );
  await clickText(page, label, { exact: true, within: "main" });
  await untilStep(page, nextTitle);
}

/**
 * After Create Partner / Link Existing User, Add Partner lands on All Partners
 * (`?created=<id>`, ADR-048 addendum) with the success message and the new row
 * marked. Checks that, and returns the new partner's id.
 */
async function landOnAllPartners(page, what) {
  await until(page, () => location.pathname === "/admin/partners/all" && /^[0-9a-f-]{36}$/.test(new URLSearchParams(location.search).get("created") || ""), null, 120000, what);
  await untilTextCI(page, "Partner created successfully.", 30000);
  const id = new URL(page.url()).searchParams.get("created");
  expectThat((await bodyText(page)).toLowerCase().includes("profile is incomplete"), "All Partners does not say the new partner's profile is incomplete");
  return id;
}

/** Add Partner through the short form, land on All Partners, and open the partner's page. */
async function addPartner(page, spec, { audit: auditIt = false } = {}) {
  track(spec.password, `${spec.name} administrator`);
  await page.goto(`${BASE}/admin/partners/new`, { waitUntil: "networkidle0" });
  await page.waitForSelector("main h1");
  if (auditIt) {
    await audit(page, "Add Partner");
    await reflowHere(page, "Add Partner");
  }
  const labels = await page.evaluate(() => [...document.querySelectorAll("main label")].map((l) => l.textContent.replace(/\s+/g, " ").trim()));
  for (const absent of [/business email/i, /legal/i, /partner code/i, /products?\b/i, /commission/i, /price/i]) {
    expectThat(!labels.some((l) => absent.test(l)), `Add Partner asks for more than the account: ${labels.join(" | ")}`);
  }
  await type(page, "Partner name", spec.name);
  await type(page, "Admin email", spec.adminEmail);
  await type(page, "Password", spec.password);
  await type(page, "Confirm password", spec.password);
  await tick(page, spec.type, true, { legend: "Partner type" });
  if (spec.country) await choose(page, "Country", spec.country);
  await clickText(page, "Create Partner", { exact: true });
  const id = await landOnAllPartners(page, "the new partner");
  await page.goto(`${BASE}/admin/partners/${id}`, { waitUntil: "networkidle0" });
  await untilTextCI(page, "Profile incomplete", 30000);
  expectThat(!(await bodyText(page)).includes(spec.password), "the partner's page shows the password");
  expectThat((await page.$$('input[type="password"]')).length === 0, "a password input is on the partner's page");
  return id;
}

/** Fill the business step: business email (country came from Add Partner) and the primary contact. */
async function fillBusinessStep(page, spec) {
  await untilStep(page, spec.firstStep);
  await type(page, "Business email", spec.businessEmail);
  if (!spec.country) await choose(page, "Country", "GB");
  await clickText(page, "Save business details", { exact: true });
  await untilText(page, "Business details saved.", 90000);
  await type(page, "Primary contact name", spec.contact);
  await type(page, "Primary contact email", spec.businessEmail);
  await clickText(page, "Save contacts", { exact: true });
  await untilText(page, "Contacts saved.", 90000);
}

async function allowProduct(page, product) {
  await choose(page, "Allow a product", product);
  await clickText(page, "Allow product", { exact: true });
  await untilText(page, "Product allowed.", 90000);
}

async function assignRate(page, percent) {
  await type(page, "New rate (%)", percent);
  await type(page, "Reason (recorded in the audit log)", "Agreed partner rate for the browser check");
  await clickText(page, "Assign rate", { exact: true });
  await untilText(page, "Commission rate assigned.", 90000);
}

async function activateFromReview(page) {
  await untilTextCI(page, "Profile complete", 60000);
  await clickText(page, "Activate partner", { exact: true });
  await type(page, "Reason for", "Profile complete and checked in the browser");
  await clickText(page, "Confirm", { exact: true, within: "main" });
  await untilText(page, "Partner activated.", 90000);
}

async function stepNames(page) {
  return page.evaluate(() =>
    [...document.querySelectorAll('nav[aria-label="Profile steps"] button')].map((b) => {
      const visible = [...b.querySelectorAll("span")].filter((s) => !s.getAttribute("aria-hidden"))[0];
      return (visible ? visible.childNodes[0].textContent : b.innerText).replace(/\s+/g, " ").trim();
    }),
  );
}

async function auditStep(page, label) {
  if (!report.pages.some((p) => p.label === label)) {
    await audit(page, label);
    await reflowHere(page, label);
  }
}

const SPECS = {
  reseller: {
    type: "Reseller", name: "Acme Reseller", adminEmail: "owner@acme-reseller.browser.test", country: "GB",
    businessEmail: "hello@acme-reseller.browser.test", contact: "Rhea Seller", firstStep: "Business information",
  },
  whiteLabel: {
    type: "White-label", name: "Bright Brand", adminEmail: "owner@bright-brand.browser.test", country: "GB",
    businessEmail: "hello@bright-brand.browser.test", contact: "Wanda White", firstStep: "Business information",
  },
  affiliate: {
    type: "Affiliate", name: "Link Lane", adminEmail: "owner@link-lane.browser.test", country: "",
    businessEmail: "hello@link-lane.browser.test", contact: "Alma Affiliate", firstStep: "Profile",
  },
};
for (const [key, spec] of Object.entries(SPECS)) spec.password = PARTNER_PASSWORD(key);

const made = {}; // key -> partner id
const needs = (key, journey) => {
  if (!made[key]) throw new Error(`needs journey ${journey} to have created the ${key} partner`);
};

// ------------------------------------------------------------------ main --

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--no-sandbox"] });
  const fresh = () => browser.createBrowserContext();
  const staffContext = await fresh();
  const staff = await newPage(staffContext);
  watchPasswords(staff);
  await staffSignIn(staff);

  await flow("N navigation: Partners holds only Dashboard, All Partners and Add Partner; old addresses redirect", async () => {
    await visit(staff, "/admin/partners", "Partners dashboard");
    await reflowHere(staff, "Partners dashboard");
    const nav = await centerNav(staff);
    expectThat(JSON.stringify(nav) === JSON.stringify(["Dashboard", "All Partners", "Add Partner"]), `partner navigation: ${nav.join(" | ")}`);
    const text = await bodyText(staff);
    for (const card of ["Total partners", "Active partners", "Resellers", "White-label partners", "Affiliates", "Suspended partners", "Profiles incomplete", "Recent partners"]) {
      expectThat(text.toLowerCase().includes(card.toLowerCase()), `the dashboard has no "${card}"`);
    }
    expectThat(!/\bMRR\b|\bARR\b/.test(text), "the dashboard shows MRR/ARR");
    const redirects = {
      "/admin/partners/resellers": "/admin/partners/all?type=reseller",
      "/admin/partners/white-label": "/admin/partners/all?type=white_label",
      "/admin/partners/affiliates": "/admin/partners/all?type=affiliate",
      "/admin/partners/customers": "/admin/partners/all",
      "/admin/partners/products": "/admin/partners/all",
      "/admin/partners/commissions": "/admin/partners/all",
      "/admin/partners/payouts": "/admin/partners/all",
      "/admin/commissions": "/admin/partners/all",
      "/admin/payouts": "/admin/partners/all",
      "/admin/partners/resellers/new": "/admin/partners/new?type=reseller",
      "/admin/partners/white-label/new": "/admin/partners/new?type=white_label",
    };
    for (const [from, to] of Object.entries(redirects)) {
      await staff.goto(`${BASE}${from}`, { waitUntil: "networkidle0" });
      const landed = staff.url().replace(BASE, "");
      expectThat(landed === to, `${from} went to ${landed}, not ${to}`);
    }
    return `${Object.keys(redirects).length} old addresses redirect`;
  });

  await flow("A reseller: Add Partner -> Complete Your Profile -> activate -> sign in -> Reseller workspace", async () => {
    const spec = SPECS.reseller;
    const id = await addPartner(staff, spec, { audit: true });
    made.reseller = id;
    await audit(staff, "Partner detail (new reseller)");
    await reflowHere(staff, "Partner detail (new reseller)");
    await clickText(staff, "Complete Your Profile", { exact: true });
    await untilStep(staff, "Step 1 of 8: Business information");
    const steps = await stepNames(staff);
    const expected = ["Business information", "Products", "Pricing", "Commission", "Customer & sales configuration", "Sub-reseller settings", "Team", "Review"];
    expectThat(JSON.stringify(steps) === JSON.stringify(expected), `reseller steps: ${steps.join(" | ")}`);
    await auditStep(staff, "Profile step: Business information");
    await fillBusinessStep(staff, spec);
    await clickContinue(staff, "Step 2 of 8: Products");
    await auditStep(staff, "Profile step: Products");
    await allowProduct(staff, "Easy CRM");
    await clickContinue(staff, "Step 3 of 8: Pricing");
    await auditStep(staff, "Profile step: Pricing");
    await clickContinue(staff, "Step 4 of 8: Commission");
    await auditStep(staff, "Profile step: Commission");
    await assignRate(staff, "15");
    await clickContinue(staff, "Step 5 of 8: Customer & sales configuration");
    await auditStep(staff, "Profile step: Customer & sales configuration");
    for (const s of ["May create customers", "May sell products", "May manage subscriptions and renewals", "May view pricing"]) {
      const on = await staff.evaluate((t) => {
        const l = [...document.querySelectorAll("label")].find((x) => x.textContent.includes(t));
        return l ? l.querySelector("input").checked : null;
      }, s);
      expectThat(on === true, `"${s}" is not on for a reseller`);
    }
    await clickContinue(staff, "Step 6 of 8: Sub-reseller settings");
    await auditStep(staff, "Profile step: Sub-reseller settings");
    await clickContinue(staff, "Step 7 of 8: Team");
    await auditStep(staff, "Profile step: Team");
    await clickContinue(staff, "Step 8 of 8: Review");
    await auditStep(staff, "Profile step: Review");
    await activateFromReview(staff);

    const partnerContext = await fresh();
    const partner = await newPage(partnerContext);
    watchPasswords(partner);
    await partnerSignIn(partner, spec.adminEmail, spec.password);
    const path = new URL(partner.url()).pathname;
    expectThat(path === "/partner/reseller/dashboard", `the reseller landed on ${path}`);
    await audit(partner, "Reseller workspace dashboard");
    const menu = await sidebarLabels(partner);
    expectThat(menu.includes("Customers") && menu.includes("Sub-Resellers"), `reseller menu: ${menu.join(" | ")}`);
    expectThat(!menu.includes("Brand Settings") && !menu.includes("Referral Links"), `the reseller sees another type's entries: ${menu.join(" | ")}`);
    made.resellerPage = partner;
    return `landed on ${path}; ${menu.length} entries`;
  });

  await flow("B white-label: Add Partner -> brand, products, commission -> activate -> White-label workspace", async () => {
    const spec = SPECS.whiteLabel;
    const id = await addPartner(staff, spec);
    made.whiteLabel = id;
    await clickText(staff, "Complete Your Profile", { exact: true });
    await untilStep(staff, "Step 1 of 11: Business information");
    const steps = await stepNames(staff);
    for (const s of ["Brand identity", "Products", "Pricing", "Commission", "Customer & sales configuration", "Custom domain", "Communication", "Customer portal (optional)", "Team", "Review"]) {
      expectThat(steps.includes(s), `white-label steps lack "${s}": ${steps.join(" | ")}`);
    }
    await fillBusinessStep(staff, spec);
    await clickContinue(staff, "Brand identity");
    await auditStep(staff, "Profile step: Brand identity");
    await type(staff, "Brand name", "Bright Cloud");
    await clickText(staff, "Save & continue", { exact: true });
    await untilStep(staff, "Products");
    await allowProduct(staff, "Easy CRM");
    await clickContinue(staff, "Pricing");
    await clickContinue(staff, "Commission");
    await assignRate(staff, "12.5");
    await clickContinue(staff, "Customer & sales configuration");
    await clickContinue(staff, "Custom domain");
    await auditStep(staff, "Profile step: Custom domain");
    const domainText = (await bodyText(staff)).toLowerCase();
    expectThat(domainText.includes("custom-domain branding") && domainText.includes("workeasy360 secure sign-in"), "the domain step does not state the honest D-035 position");
    await clickContinue(staff, "Communication");
    await auditStep(staff, "Profile step: Communication");
    await clickContinue(staff, "Customer portal (optional)");
    await auditStep(staff, "Profile step: Customer portal");
    await clickContinue(staff, "Team");
    await clickContinue(staff, "Review");
    await activateFromReview(staff);

    const partnerContext = await fresh();
    const partner = await newPage(partnerContext);
    watchPasswords(partner);
    await partnerSignIn(partner, spec.adminEmail, spec.password);
    const path = new URL(partner.url()).pathname;
    expectThat(path === "/partner/white-label/dashboard", `the white-label partner landed on ${path}`);
    await audit(partner, "White-label workspace dashboard");
    const menu = await sidebarLabels(partner);
    expectThat(menu.includes("Brand Settings") && menu.includes("Custom Domain"), `white-label menu: ${menu.join(" | ")}`);
    return `landed on ${path}`;
  });

  await flow("C affiliate: Add Partner -> referral code, commission -> activate -> Affiliate workspace", async () => {
    const spec = SPECS.affiliate;
    const id = await addPartner(staff, spec);
    made.affiliate = id;
    await clickText(staff, "Complete Your Profile", { exact: true });
    await untilStep(staff, "Step 1 of 7: Profile");
    const steps = await stepNames(staff);
    const expected = ["Profile", "Products promoted", "Commission", "Referral program", "Payout settings", "Marketing information", "Review"];
    expectThat(JSON.stringify(steps) === JSON.stringify(expected), `affiliate steps: ${steps.join(" | ")}`);
    await fillBusinessStep(staff, spec);
    await clickContinue(staff, "Step 2 of 7: Products promoted");
    await auditStep(staff, "Profile step: Products promoted");
    expectThat((await bodyText(staff)).includes("It is granted no product"), "the affiliate products step does not say it is granted no product");
    await clickContinue(staff, "Step 3 of 7: Commission");
    await assignRate(staff, "20");
    await clickContinue(staff, "Step 4 of 7: Referral program");
    await auditStep(staff, "Profile step: Referral program");
    await type(staff, "Campaign name", "Launch");
    await clickText(staff, "Create referral code", { exact: true });
    await untilText(staff, "Referral code created.", 90000);
    expectThat((await bodyText(staff)).includes("/signup?ref="), "no referral link is shown");
    await clickContinue(staff, "Step 5 of 7: Payout settings");
    await auditStep(staff, "Profile step: Payout settings");
    await clickContinue(staff, "Step 6 of 7: Marketing information");
    await auditStep(staff, "Profile step: Marketing information");
    await clickContinue(staff, "Step 7 of 7: Review");
    await activateFromReview(staff);

    const partnerContext = await fresh();
    const partner = await newPage(partnerContext);
    watchPasswords(partner);
    await partnerSignIn(partner, spec.adminEmail, spec.password);
    const path = new URL(partner.url()).pathname;
    expectThat(path === "/partner/affiliate/dashboard", `the affiliate landed on ${path}`);
    await audit(partner, "Affiliate workspace dashboard");
    const menu = await sidebarLabels(partner);
    expectThat(menu.includes("Referral Links") && !menu.includes("Customers"), `affiliate menu: ${menu.join(" | ")}`);
    made.affiliatePage = partner;
    return `landed on ${path}`;
  });

  await flow("F All Partners: badges, completion, server-side type / status / profile filters and search", async () => {
    needs("reseller", "A");
    needs("affiliate", "C");
    await visit(staff, "/admin/partners/all", "All Partners");
    await reflowHere(staff, "All Partners");
    const heads = await staff.evaluate(() => [...document.querySelectorAll("main table th[scope=col], main thead th")].map((th) => th.innerText.trim()));
    // The agency-style table of the ADR-048 addendum: type badges and setup state live in the Name cell.
    const want = ["Status", "Action", "Panel", "Partner ID", "Name", "Email", "Phone", "Country", "Currency", "Wallet", "Last Login", "Joining Date"];
    expectThat(want.every((w) => heads.some((h) => h.toLowerCase() === w.toLowerCase())), `columns: ${heads.join(" | ")}`);
    const nameCell = await staff.evaluate(() => (document.querySelector("main tbody tr th[scope=row]") || {}).innerText || "");
    expectThat(/setup required|ready|active/i.test(nameCell), `the Name cell shows no setup state: ${nameCell}`);
    const rows = async () => staff.evaluate(() => [...document.querySelectorAll("main tbody tr th[scope=row] a")].map((a) => a.innerText.trim()));
    const filter = async (label, value) => {
      const requests = [];
      const listen = (req) => req.url().includes("/api/v1/admin/partners/resellers") && requests.push(req.url());
      staff.on("request", listen);
      await choose(staff, label, value);
      await until(staff, () => !document.body.innerText.includes("Loading partners"), null, 30000);
      await staff.waitForNetworkIdle({ idleTime: 500, timeout: 15000 }).catch(() => {});
      staff.off("request", listen);
      return requests;
    };
    const sent = await filter("Partner type", "Affiliate");
    expectThat(sent.some((u) => u.includes("type=affiliate")), `the type filter did not reach the server: ${sent.join(" ")}`);
    let names = await rows();
    expectThat(JSON.stringify(names) === JSON.stringify(["Link Lane"]), `affiliates: ${names.join(", ")}`);
    await filter("Partner type", "Reseller");
    names = await rows();
    expectThat(names.includes("Acme Reseller") && names.includes("Bright Brand") && !names.includes("Link Lane"), `resellers: ${names.join(", ")}`);
    await filter("Partner type", "White-label");
    names = await rows();
    expectThat(JSON.stringify(names) === JSON.stringify(["Bright Brand"]), `white-label: ${names.join(", ")}`);
    await filter("Partner type", "All Partners");
    await filter("Profile", "Complete");
    names = await rows();
    expectThat(names.length === 3, `complete profiles: ${names.join(", ")}`);
    await filter("Profile", "All");
    await type(staff, "Search by partner name or email", "owner@link-lane");
    await clickText(staff, "Search", { exact: true, within: "main" });
    await until(staff, () => document.querySelectorAll("main tbody tr").length === 1, null, 30000, "one search result");
    names = await rows();
    expectThat(JSON.stringify(names) === JSON.stringify(["Link Lane"]), `search by email: ${names.join(", ")}`);
    const badge = await staff.evaluate(() => [...document.querySelectorAll("main tbody tr ul li")].map((li) => li.textContent.trim()));
    expectThat(badge.includes("Affiliate"), `badges: ${badge.join(", ")}`);
    return "type, profile and search filters answered by the server";
  });

  await flow("E an existing email: never overwritten; Link Existing User or Cancel", async () => {
    await page_goto_new(staff);
    await type(staff, "Partner name", "Linked Co");
    await type(staff, "Admin email", "existing.customer@browser.test");
    const typed = PARTNER_PASSWORD("taken");
    track(typed, "the taken address");
    await type(staff, "Password", typed);
    await type(staff, "Confirm password", typed);
    await tick(staff, "Reseller", true, { legend: "Partner type" });
    await clickText(staff, "Create Partner", { exact: true });
    await untilText(staff, "This email already belongs to an existing Workeasy360 account.", 90000);
    await audit(staff, "Add Partner: existing email");
    await clickText(staff, "Cancel", { exact: true, within: "main form" });
    const cleared = await staff.evaluate(() => [...document.querySelectorAll("input[type=email]")].every((i) => i.value === ""));
    expectThat(cleared, "Cancel did not clear the email");
    await type(staff, "Admin email", "existing.customer@browser.test");
    await type(staff, "Password", typed);
    await type(staff, "Confirm password", typed);
    await clickText(staff, "Create Partner", { exact: true });
    await untilText(staff, "This email already belongs to an existing Workeasy360 account.", 90000);
    await clickText(staff, "Link Existing User as Partner Administrator", { exact: true });
    await landOnAllPartners(staff, "the linked partner");
    // The person keeps the password they already had; the one typed for the address never worked.
    const check = await fresh();
    const p = await newPage(check);
    await signInMode(p, "partner", "existing.customer@browser.test", typed);
    await sleep(2500);
    expectThat(new URL(p.url()).pathname.startsWith("/login"), "the typed password signed the existing person in");
    return "linked without changing the existing password";
  });

  await flow("D isolation: capabilities, not the type, decide - and partners cannot reach each other", async () => {
    needs("affiliatePage", "C");
    needs("resellerPage", "A");
    const aff = made.affiliatePage;
    const affCustomers = await apiGet(aff, `/api/v1/partner/${made.affiliate}/customers`);
    expectThat(affCustomers.status === 403 || affCustomers.status === 404, `the affiliate read customers: ${affCustomers.status}`);
    const res = made.resellerPage;
    const domain = await post(res, `/api/v1/partner/${made.reseller}/domains/request`, { hostname: "app.acme-reseller-check.com" });
    expectThat(domain.status === 403 || domain.status === 404 || domain.status === 401, `the reseller configured a domain: ${domain.status}`);
    const other = await apiGet(res, `/api/v1/partner/${made.affiliate}/referrals`);
    expectThat(other.status === 403 || other.status === 404, `partner A read partner B: ${other.status}`);
    const staffOnly = await post(res, `/api/v1/admin/partners/${made.reseller}/referral-codes`, { label: "x" });
    expectThat(staffOnly.status === 403 || staffOnly.status === 401 || staffOnly.status === 404, `a partner used a staff command: ${staffOnly.status}`);
    return "refused as expected";
  });

  await flow("E2 the typed passwords went only to the create request and the sign-in", async () => {
    const allowed = new Set(["/api/v1/admin/partners/create", "/api/v1/auth/login"]);
    const strays = carried.filter((c) => !allowed.has(c.path));
    expectThat(strays.length === 0, `a password travelled to ${JSON.stringify(strays)}`);
    expectThat(echoed.length === 0, `an answer repeated a password: ${JSON.stringify(echoed)}`);
    const held = await storageHolds(staff, [...secrets.keys()]);
    expectThat(held.length === 0, "a password is in browser storage, a cookie or an address");
    expectThat(/no-store/.test(cacheControl["/api/v1/admin/partners/create"] || ""), "the create answer may be cached");
    return `${carried.length} carrying requests, all to create or sign-in`;
  });

  await browser.close();
  fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
  const failed = report.flows.filter((f) => !f.ok);
  const axeBad = report.pages.filter((p) => p.axe.length);
  const focusBad = report.pages.filter((p) => p.noFocus.length);
  const h1Bad = report.pages.filter((p) => p.h1.length !== 1);
  const reflowBad = report.reflow.filter((r) => r.over > 0);
  console.log(
    JSON.stringify({
      flows: `${report.flows.length - failed.length}/${report.flows.length}`,
      pages: report.pages.length,
      axe: axeBad.length,
      focus: focusBad.length,
      h1: h1Bad.length,
      csp: report.csp.length,
      errors: report.errors.length,
      reflow: `${report.reflow.length - reflowBad.length}/${report.reflow.length}`,
    }),
  );
  for (const f of failed) console.log("FAILED", f.name, "::", f.detail);
  for (const p of axeBad) console.log("AXE", p.label, JSON.stringify(p.axe));
  for (const p of focusBad) console.log("FOCUS", p.label, p.noFocus.join(", "));
  for (const p of h1Bad) console.log("H1", p.label, JSON.stringify(p.h1));
  for (const r of reflowBad) console.log("REFLOW", r.url, r.width, r.over);
  for (const c of report.csp) console.log("CSP", c);
  for (const e of report.errors) console.log("ERROR", e);
  process.exit(failed.length || axeBad.length || report.csp.length || report.errors.length || reflowBad.length || h1Bad.length ? 1 : 0);
})();

async function page_goto_new(page) {
  await page.goto(`${BASE}/admin/partners/new`, { waitUntil: "networkidle0" });
  await page.waitForSelector("main h1");
}
