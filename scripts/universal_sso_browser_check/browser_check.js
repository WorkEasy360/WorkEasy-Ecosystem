// Real-browser verification of universal, user-controlled product sign-in
// (ADR-044 and its 2026-10-12 addendum, docs/universal-sso/). Production builds of every frontend,
// real backends, scratch databases, the installed Chrome. Six applications on loopback, each with
// its own cookie jar (cookies are scoped by host name):
//
//   Workeasy360    http://127.0.0.1:3021  (Django 127.0.0.1:8021 is the issuer)
//   Easy CRM       http://localhost:4022   sso_optional - its password sign-in is on here
//   EasyBook       http://127.0.0.2:3023   sso_only
//   Future         http://127.0.0.3:5024   registered DURING the run, in the console
//   Easy Project   http://127.0.0.4:8024   sso_only (one jar: SPA + API)
//   Easy Help Desk http://127.0.0.5:3025   sso_only (Next; its API on :4025, same host)
//
// The scratch Workeasy360 answers revalidate_after = 30 s (w360_usso_browser_settings.py), so "an
// open product session loses access within the window" is observable in a run, for every product.
//
// Journeys (ONLY=1,...,13 runs a subset; 10 needs 6 first; 12 suspends Ben, so it runs last):
//   1  AUTO: Automatic -> all four products open signed in, no password, each with its own session
//   2  ASK: chooser on Workeasy360 -> Continue -> Easy CRM, Easy Project, Easy Help Desk open
//   3  REMEMBER: "Remember my choice for Easy CRM" -> no chooser next time; EasyBook still asks
//   4  CHANGE: settings -> Easy CRM "prefer its own sign-in" -> product login page; back -> SSO
//  11  OVERRIDE: global Ask; CRM, Book, Help Desk Automatic, Project Ask -> verified one by one
//  13  PRODUCT_LOGIN: "prefer each product's own sign-in" -> SSO-only products explain it, ask,
//      and never offer a sign-in they lack; Easy CRM opens its own page
//   5  FUTURE: Super Admin registers "Future Product" in the console; a customer's
//      default applies with no code change
//   6  TRIAL: all four trials -> each opens through Workeasy360 -> controlled clock past ends_at ->
//      no Open, launch refused, Explore offers Upgrade / Buy now; each product's open session is
//      cut off within the window; a new sign-in from each product is refused (not entitled)
//  10  PAID (after 6): Cara buys all four -> paid, Open -> SSO opens each, same federated account ->
//      each serves the organization again once it re-asks (within a minute)
//   7  SWITCH: A signs out of Workeasy360, B signs in -> Easy CRM, Easy Project and Easy Help Desk
//      hold B; and "Use another Workeasy360 account" on the chooser
//   8  LOGOUT: product-only sign-out keeps Workeasy360; "and Workeasy360" asks first, then both
//      (Easy CRM, Easy Project, Easy Help Desk)
//   9  REAUTH: Easy CRM "Confirm it's you" -> prompt=login for the current organization ->
//      another account's password is refused; Ana's password confirms
//  12  REMOVED: Ana suspends Ben in Workeasy360 -> each product refuses his open session within
//      the window (per-person entitlement, membership_inactive)
//  14  DIRECT (one-login brief, journey B): signed in to Workeasy360 only, Ana types each product's
//      address -> the product starts the flow itself, Workeasy360 reuses its session (default
//      Automatic: nothing stored), the product opens signed in - no product sign-in page, no
//      password; deep links survive; after "Sign out of <product>" it shows its sign-in page instead
//      of signing her back in; with no Workeasy360 session the central sign-in appears first.
//      Easy CRM auto-starts only when its own password sign-in is off: USSO_CRM_SSO_ONLY=1 (pass 2)
//  15  CROSS-ORG: Cara asks each product for Customer Company (not hers) -> refused; her own
//      sessions are bound to Trial Company and list none of Customer Company's people
// On every audited page: CSP violations, console errors, axe WCAG 2.1 A/AA,
// a visible focus indicator, one h1. Reflow at 1280, 768, 375 px.
// Not part of CI. A manual screen-reader pass is NOT part of it.
const puppeteer = require("puppeteer-core");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { execFileSync, spawn } = require("child_process");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const WORK = process.env.USSO_WORK;
const AXE = fs.readFileSync(process.env.AXE_PATH, "utf8");
const PY = process.env.PY;
const REPO = process.env.REPO;
const SEED = JSON.parse(fs.readFileSync(path.join(WORK, "seed.json"), "utf8"));
const PASSWORD = SEED.password;
const ONLY = (process.env.ONLY || "1,2,3,4,5,6,7,8,9,10,11,12,13,14,15").split(",");
// Pass 2 of journey 14: Easy CRM restarted with its own password sign-in off (production's mode).
const CRM_SSO_ONLY = process.env.USSO_CRM_SSO_ONLY === "1";
const W360 = "http://127.0.0.1:3021";
const CRM = "http://localhost:4022";
const BOOK = "http://127.0.0.2:3023";
const FUTURE = "http://127.0.0.3:5024";
const PROJECT = "http://127.0.0.4:8024";
const HELPDESK = "http://127.0.0.5:3025";
const HELPDESK_API = "http://127.0.0.5:4025";
const SLUG = SEED.company_slug;
const TRIAL_SLUG = SEED.trial_company_slug;
const WIDTHS = [1280, 768, 375];

const report = { flows: [], pages: [], csp: [], productCsp: [], errors: [], expected: [], reflow: [], notes: [] };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let browser;

/** A URL for the report: origin and path, and only the NAMES of its query
 * parameters (never a code, state or nonce value). */
function brief(url, base = undefined) {
  try {
    const u = new URL(url, base);
    const keys = [...u.searchParams.keys()];
    return `${u.origin}${u.pathname}${keys.length ? `?[${keys.join(",")}]` : ""}`;
  } catch {
    return String(url).slice(0, 80);
  }
}

function flow(name, ok, detail = {}) {
  report.flows.push({ flow: name, ok: Boolean(ok), ...detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : ` :: ${JSON.stringify(detail).slice(-1200)}`}`);
}

async function newPage(width = 1280) {
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  await page.setViewport({ width, height: 900 });
  await page.evaluateOnNewDocument((w360) => {
    window.__csp = [];
    document.addEventListener("securitypolicyviolation", (e) =>
      window.__csp.push(`${e.violatedDirective} ${e.blockedURI} (${e.disposition}) from ${e.sourceFile || "?"}:${e.lineNumber}`),
    );
    // Journey 14: whether a product ever rendered its sign-in button, even for a moment before a
    // redirect (reported through the console, which survives the document being replaced).
    if (location.origin === w360) return;
    let told = false;
    const look = () => {
      if (told || !document.body) return;
      const hit = [...document.querySelectorAll("button, a")].find((el) => /^(Continue with Workeasy360|Access Easy CRM|Access EasyBook)/.test((el.innerText || "").trim()));
      if (hit) {
        told = true;
        console.log(`__SIGN_IN_PAGE__ ${location.href}`);
      }
    };
    new MutationObserver(look).observe(document, { childList: true, subtree: true });
  }, W360);
  page.__nav = [];
  page.on("framenavigated", (f) => f === page.mainFrame() && page.__nav.push(f.url()));
  page.__signInSeen = [];
  page.on("console", (m) => {
    if (m.type() === "log" && m.text().startsWith("__SIGN_IN_PAGE__ ")) page.__signInSeen.push(brief(m.text().slice(17)));
    if (m.type() !== "error") return;
    const line = `${page.url()} :: ${m.text()}`;
    if (/status of (400|401|402|403|404|409)/.test(m.text())) report.expected.push(line);
    else report.errors.push(line);
  });
  page.on("pageerror", (e) => report.errors.push(`${page.url()} :: pageerror ${e.message}`));
  page.__failed = [];
  page.on("requestfailed", (r) => {
    if (r.resourceType() === "document") page.__failed.push(`${r.url()} :: ${r.failure() && r.failure().errorText}`);
  });
  // Full redirect targets, for reading an sso_error a product passed through on its way elsewhere.
  // Never written to the report (they can carry a state value).
  page.__locations = [];
  page.on("response", (r) => {
    if (r.request().resourceType() === "document" && r.status() >= 300 && r.status() < 400) {
      page.__nav.push(`(${r.status()} ${brief(r.url())} -> ${brief(r.headers().location || "", r.url())})`);
      try {
        page.__locations.push(new URL(r.headers().location || "", r.url()).href);
      } catch {
        /* not a URL */
      }
    }
  });
  return page;
}

async function audit(page, label) {
  const csp = await page.evaluate(() => window.__csp || []);
  // Workeasy360's own pages are this change's CSP gate; a product's pages
  // are reported separately (their policies belong to the product).
  const own = page.url().startsWith(W360);
  csp.forEach((c) => (own ? report.csp : report.productCsp).push(`${label}: ${c}`));
  const axe = await page.evaluate(async (src) => {
    (0, eval)(src);
    const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
    return r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, sample: v.nodes[0] && v.nodes[0].html.slice(0, 160) }));
  }, AXE);
  const noFocus = [];
  await page.evaluate(() => document.activeElement && document.activeElement.blur());
  let tabbed = 0;
  for (let i = 0; i < 25; i++) {
    await page.keyboard.press("Tab");
    const info = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return null;
      const cs = getComputedStyle(el);
      const outline = cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0;
      const shadow = cs.boxShadow && cs.boxShadow !== "none";
      return { tag: el.tagName, text: (el.innerText || el.getAttribute("aria-label") || "").slice(0, 40), visible: outline || shadow || el.matches("input,select,textarea") };
    });
    if (info) {
      tabbed++;
      if (!info.visible) noFocus.push(`${info.tag} "${info.text}"`);
    }
  }
  await page.evaluate(() => window.scrollTo(0, 0));
  const h1 = await page.evaluate(() => [...document.querySelectorAll("h1")].map((h) => h.innerText));
  report.pages.push({ label, url: page.url(), axe, tabbed, noFocus, h1 });
  return { axe, noFocus, h1 };
}

async function reflow(page, label) {
  const original = page.viewport();
  for (const width of WIDTHS) {
    await page.setViewport({ width, height: 900 });
    await sleep(300);
    const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    report.reflow.push({ label, width, overflow: over });
  }
  await page.setViewport(original);
}

const bodyText = (page) => page.evaluate(() => document.body.innerText);

async function waitForText(page, text, timeout = 25000) {
  await page.waitForFunction((t) => document.body && document.body.innerText.includes(t), { timeout }, text).catch(async () => {
    const shown = (await bodyText(page).catch(() => "")).slice(0, 700).replace(/\n+/g, " | ");
    throw new Error(`text not found: "${text}" on ${page.url()} :: ${shown}`);
  });
}

async function waitForUrl(page, test, timeout = 40000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (test(page.url())) {
      await page.waitForNetworkIdle({ idleTime: 500, timeout: 15000 }).catch(() => {});
      return page.url();
    }
    await sleep(200);
  }
  throw new Error(`unexpected address ${page.url()} after ${page.__nav.slice(-6).join(" -> ")} ${JSON.stringify(page.__failed.slice(-3))}`);
}

async function clickText(page, text, selector = "button, a") {
  const handle = await page.waitForFunction(
    (t, s) => [...document.querySelectorAll(s)].find((el) => el.innerText.trim() === t && el.getBoundingClientRect().height > 0),
    { timeout: 25000 },
    text,
    selector,
  );
  await handle.asElement().click();
}

async function clickStarting(page, text, selector = "button, a") {
  const handle = await page.waitForFunction(
    (t, s) => [...document.querySelectorAll(s)].find((el) => el.innerText.trim().startsWith(t) && el.getBoundingClientRect().height > 0),
    { timeout: 25000 },
    text,
    selector,
  );
  await handle.asElement().click();
}

async function clickLabel(page, label) {
  // Enabled, too: a screen busy with the previous action disables its buttons.
  const el = await page.waitForSelector(`[aria-label="${label}"]:not([disabled])`, { visible: true, timeout: 25000 });
  await el.click();
}

async function fillLabelled(page, labelStart, value, { select = false } = {}) {
  const id = await page.waitForFunction(
    (t) => {
      const label = [...document.querySelectorAll("label")].find((l) => l.innerText.trim().startsWith(t));
      return label && label.htmlFor ? label.htmlFor : null;
    },
    { timeout: 20000 },
    labelStart,
  );
  const selector = `[id="${await id.jsonValue()}"]`;
  if (select) {
    await page.select(selector, value);
  } else {
    await page.click(selector, { clickCount: 3 });
    await page.$eval(selector, (el) => {
      el.value = "";
    });
    await page.type(selector, value);
  }
}

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
  return String((mac.readUInt32BE(offset) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}
const lastWindow = { [SEED.staff_totp]: Math.floor(Date.now() / 30000) };
async function freshCode(secret) {
  const window = () => Math.floor(Date.now() / 30000);
  while (lastWindow[secret] === window()) await sleep(1000);
  lastWindow[secret] = window();
  return totp(secret);
}

/** Sign in to Workeasy360 with a password, as `email`, and wait to be in. */
async function w360SignIn(page, email, { mode = "user", totpSecret = null } = {}) {
  await page.goto(`${W360}/login?mode=${mode}`, { waitUntil: "networkidle0", timeout: 40000 });
  await page.waitForSelector('input[type="email"]', { visible: true, timeout: 25000 });
  await page.type('input[type="email"]', email);
  await page.type('input[type="password"]', PASSWORD);
  await clickText(page, "Sign in");
  if (totpSecret) {
    await page.waitForSelector('input[autocomplete="one-time-code"]', { visible: true, timeout: 25000 });
    await page.type('input[autocomplete="one-time-code"]', await freshCode(totpSecret));
    await clickText(page, "Verify");
  }
  await waitForUrl(page, (u) => u.startsWith(W360) && !u.includes("/login"));
}

async function w360Api(page, p, method = "GET", body = undefined) {
  return page.evaluate(
    async (pth, m, b) => {
      const csrf = (document.cookie.match(/(?:^|; )w360_csrf=([^;]*)/) || [])[1];
      const r = await fetch(pth, {
        method: m,
        headers: { Accept: "application/json", "Content-Type": "application/json", ...(csrf ? { "X-CSRFToken": decodeURIComponent(csrf) } : {}) },
        credentials: "same-origin",
        body: b ? JSON.stringify(b) : undefined,
      });
      let json = null;
      try {
        json = await r.json();
      } catch {
        json = null;
      }
      return { status: r.status, body: json };
    },
    p,
    method,
    body,
  );
}

/** The person Easy CRM / EasyBook / Future Product has signed in. */
async function crmUser(page) {
  return page.evaluate(async () => {
    const r = await fetch("/api/v1/session/", { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (r.status !== 200) return { status: r.status };
    const s = await r.json();
    return { status: 200, email: s.user && s.user.email, id: s.user && s.user.id };
  });
}
/** Easy Project: one origin serves the app and its API (session cookie + CSRF cookie). */
async function projectUser(page) {
  return page.evaluate(async () => {
    const r = await fetch("/api/auth/session", { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (r.status !== 200) return { status: r.status };
    const s = await r.json();
    return { status: 200, email: s.user && s.user.email, id: s.user && s.user.id, organization: s.organization && s.organization.id };
  });
}
/** Easy Help Desk: the frontend keeps its access token in memory, so ask the API for a fresh one
 * with the refresh cookie (exactly what the app does on load) and read the person from it. */
async function helpdeskUser(page) {
  return page.evaluate(async (api) => {
    const r = await fetch(`${api}/api/v1/auth/refresh`, { method: "POST", credentials: "include", headers: { Accept: "application/json" } });
    if (r.status !== 200) return { status: r.status };
    const s = await r.json();
    const data = s.data || s;
    return { status: 200, email: data.user && data.user.email, id: data.user && data.user.id };
  }, HELPDESK_API);
}
async function bookUser(page) {
  return page.evaluate(async () => {
    const r = await fetch("/api/bff/auth/me", { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (r.status !== 200) return { status: r.status };
    const s = await r.json();
    return { status: 200, email: s.email || (s.data && s.data.email) || JSON.stringify(s).match(/[\w.+-]+@usso\.test/)?.[0] };
  });
}

/** Open a product from My Products, as the card's own button does. */
async function openFromMyProducts(page, slug, productName) {
  await page.goto(`${W360}/o/${slug}/apps`, { waitUntil: "networkidle0", timeout: 40000 });
  await waitForText(page, productName);
  const before = page.__nav.length;
  await clickText(page, `Open ${productName}`, "button");
  return before;
}

/** Whether any navigation since `from` showed Workeasy360's password form. */
const askedForPassword = (page, from) =>
  page.__nav.slice(from).some((u) => u.startsWith(W360) && new URL(u).pathname === "/login");

async function setPreferences(page, slug, { def = null, overrides = {} }) {
  await page.goto(`${W360}/o/${slug}/settings/sign-in-preferences`, { waitUntil: "networkidle0", timeout: 40000 });
  await waitForText(page, "Your default for every product");
  if (def) {
    const label = { auto_sso: "Automatically sign me in", ask: "Ask me each time", product_login: "Prefer each product" }[def];
    const radio = await page.waitForFunction(
      (t) => [...document.querySelectorAll('input[type="radio"]')].find((r) => document.querySelector(`label[for="${r.id}"]`)?.innerText.startsWith(t)),
      { timeout: 20000 },
      label,
    );
    await radio.asElement().click();
  }
  for (const [product, value] of Object.entries(overrides)) await fillLabelled(page, product, value, { select: true });
  const save = await page.waitForFunction(() => [...document.querySelectorAll("button")].find((b) => b.innerText.trim() === "Save changes"));
  const disabled = await save.asElement().evaluate((b) => b.disabled);
  if (!disabled) {
    await save.asElement().click();
    await waitForText(page, "Saved. This applies the next time you open a product.");
  }
}

// ------------------------------------------------------------------ journeys --

async function journeyAuto() {
  const page = await newPage();
  await w360SignIn(page, "ana@usso.test");
  await setPreferences(page, SLUG, { def: "auto_sso" });
  await audit(page, "Workeasy360 settings: sign-in preferences");
  await reflow(page, "sign-in preferences");
  await page.goto(`${W360}/o/${SLUG}/apps`, { waitUntil: "networkidle0" });
  await waitForText(page, "Opens with your Workeasy360 account");
  await audit(page, "Workeasy360 My Products (sign-in line on each card)");
  const from = await openFromMyProducts(page, SLUG, "Easy CRM");
  await waitForUrl(page, (u) => u.startsWith(CRM) && !u.includes("/login") && !u.includes("/api/"));
  const crm = await crmUser(page);
  flow("1 AUTO: Easy CRM opens signed in as Ana, with no second password", crm.email === "ana@usso.test" && !askedForPassword(page, from), { crm, nav: page.__nav.slice(from) });
  flow("1 AUTO: no chooser was shown", !page.__nav.slice(from).some((u) => u.includes("/product-access")), {});
  const from2 = await openFromMyProducts(page, SLUG, "EasyBook");
  await waitForUrl(page, (u) => u.startsWith(BOOK) && !u.includes("/login") && !u.includes("/api/"));
  const book = await bookUser(page);
  flow("1 AUTO: back on Workeasy360, EasyBook opens signed in as Ana, no password", book.email === "ana@usso.test" && !askedForPassword(page, from2), { book, at: page.url() });
  report.notes.push(`1: EasyBook landed on ${new URL(page.url()).pathname} (a first sign-in provisions the EasyBook organization).`);
  for (const product of [PRODUCTS.project, PRODUCTS.helpdesk]) {
    const opened = await openProduct(page, SLUG, product);
    flow(
      `1 AUTO: ${product.name} opens signed in as Ana - Workeasy360 sign-in once, no second password, no chooser`,
      opened.who.email === "ana@usso.test" && !opened.asked && !askedForPassword(page, opened.from),
      { who: opened.who, nav: opened.nav },
    );
    await audit(page, `${product.name} after Workeasy360 sign-in`);
  }
  // Each product keeps its own valid session: back on each origin, each still knows Ana.
  const sessions = {};
  for (const product of Object.values(PRODUCTS)) {
    await page.goto(`${product.origin}/`, { waitUntil: "networkidle0", timeout: 40000 });
    sessions[product.name] = (await product.user(page)).email;
  }
  flow(
    "1 AUTO: each of the four products holds its own session for Ana",
    Object.values(sessions).every((e) => e === "ana@usso.test"),
    { sessions },
  );
  return page;
}

async function journeyAsk() {
  const page = await newPage();
  await w360SignIn(page, "ben@usso.test");
  // "Reset all to default" first, whatever an earlier run left behind - and the reset button is
  // exercised. The default is Automatic (one-login brief), so Ben then chooses "Ask me each time".
  await page.goto(`${W360}/o/${SLUG}/settings/sign-in-preferences`, { waitUntil: "networkidle0", timeout: 40000 });
  await clickText(page, "Reset all to default", "button");
  await waitForText(page, "Reset. Workeasy360 will sign you in to every product automatically.");
  const afterReset = await w360Api(page, "/api/v1/auth/sign-in-preferences");
  flow(
    "2 ASK: Reset all to default leaves no stored choice - every product is Automatic, the platform default",
    afterReset.status === 200 && afterReset.body.default === "auto_sso" && !afterReset.body.default_is_set && afterReset.body.products.every((p) => p.override === "inherit" && p.effective === "auto_sso"),
    { afterReset: afterReset.body && { default: afterReset.body.default, set: afterReset.body.default_is_set } },
  );
  await setPreferences(page, SLUG, { def: "ask" });
  await openFromMyProducts(page, SLUG, "Easy CRM");
  await waitForUrl(page, (u) => u.startsWith(`${W360}/product-access`));
  await waitForText(page, "How would you like to access Easy CRM?");
  const text = await bodyText(page);
  flow(
    "2 ASK: the chooser names the account and organization and offers both ways in",
    text.includes("ben@usso.test") && text.includes("Customer Company") && text.includes("Continue with Workeasy360") && text.includes("Use Easy CRM sign-in") && text.includes("Use another Workeasy360 account"),
    { text: text.slice(0, 400) },
  );
  await audit(page, "Workeasy360 access chooser (Easy CRM, both ways in)");
  await reflow(page, "access chooser");
  const from = page.__nav.length;
  await clickStarting(page, "Continue with Workeasy360", "button");
  await waitForUrl(page, (u) => u.startsWith(CRM) && !u.includes("/login") && !u.includes("/api/"));
  const crm = await crmUser(page);
  flow("2 ASK: Continue with Workeasy360 opens Easy CRM signed in as Ben", crm.email === "ben@usso.test" && !askedForPassword(page, from), { crm });
  for (const product of [PRODUCTS.project, PRODUCTS.helpdesk]) {
    await openFromMyProducts(page, SLUG, product.name);
    await waitForUrl(page, (u) => u.startsWith(`${W360}/product-access`));
    await waitForText(page, `Use your Workeasy360 account to access ${product.name}.`);
    const shown = await bodyText(page);
    flow(
      `2 ASK: the chooser appears for ${product.name} and offers only Workeasy360 (it has no sign-in of its own)`,
      shown.includes(`Use your Workeasy360 account to access ${product.name}.`) && !shown.includes(`Use ${product.name} sign-in`) && shown.includes("Use another Workeasy360 account"),
      { shown: shown.slice(0, 400) },
    );
    await audit(page, `Workeasy360 access chooser (${product.name}, Workeasy360 sign-in only)`);
    const before = page.__nav.length;
    await clickStarting(page, product.chooser, "button");
    await waitForUrl(page, product.landed);
    const who = await product.user(page);
    flow(`2 ASK: Continue to ${product.name} opens it signed in as Ben, no second password`, who.email === "ben@usso.test" && !askedForPassword(page, before), { who });
  }
  return page;
}

async function journeyRemember(page) {
  await openFromMyProducts(page, SLUG, "Easy CRM");
  await waitForUrl(page, (u) => u.startsWith(`${W360}/product-access`));
  const remember = await page.waitForFunction(() => {
    const label = [...document.querySelectorAll("label")].find((l) => l.innerText.trim() === "Remember my choice for Easy CRM");
    return label && document.getElementById(label.htmlFor);
  });
  await remember.asElement().click();
  await clickStarting(page, "Continue with Workeasy360", "button");
  await waitForUrl(page, (u) => u.startsWith(CRM) && !u.includes("/login") && !u.includes("/api/"));
  const from = await openFromMyProducts(page, SLUG, "Easy CRM");
  await waitForUrl(page, (u) => u.startsWith(CRM) && !u.includes("/login") && !u.includes("/api/"));
  flow("3 REMEMBER: the next Easy CRM launch skips the chooser", !page.__nav.slice(from).some((u) => u.includes("/product-access")), { nav: page.__nav.slice(from) });
  await openFromMyProducts(page, SLUG, "EasyBook");
  await waitForUrl(page, (u) => u.startsWith(`${W360}/product-access`) || u.startsWith(BOOK));
  const stillAsks = page.url().startsWith(`${W360}/product-access`);
  if (stillAsks) await waitForText(page, "Continue to EasyBook");
  const text = await bodyText(page);
  flow(
    "3 REMEMBER: EasyBook still asks - only Workeasy360, since EasyBook has no sign-in of its own",
    stillAsks && text.includes("Use your Workeasy360 account to access EasyBook.") && !text.includes("Use EasyBook sign-in") && text.includes("Automatically use Workeasy360 next time"),
    { url: page.url() },
  );
  await audit(page, "Workeasy360 access chooser (EasyBook, Workeasy360 sign-in only)");
  await clickStarting(page, "Continue to EasyBook", "button");
  await waitForUrl(page, (u) => u.startsWith(BOOK) && !u.includes("/login") && !u.includes("/api/"));
  const book = await bookUser(page);
  flow("3 REMEMBER: EasyBook opens signed in as Ben after the question", book.email === "ben@usso.test", { book });
}

async function journeyChange(page) {
  // Leave Easy CRM first - its own session only - or its sign-in page would
  // (rightly) send a signed-in person straight on into the app.
  await page.goto(`${CRM}/`, { waitUntil: "networkidle0", timeout: 40000 });
  if (!page.url().startsWith(`${CRM}/login`)) {
    await clickLabel(page, "Account menu");
    await clickText(page, "Sign out of Easy CRM", '[role="menuitem"]');
    await waitForUrl(page, (u) => u.startsWith(`${CRM}/login`));
  }
  const crmAfter = await crmUser(page);
  await setPreferences(page, SLUG, { overrides: { "Easy CRM": "product_login" } });
  const w360After = await w360Api(page, "/api/v1/auth/session");
  flow(
    "4 CHANGE: 'Sign out of Easy CRM' ends only the product's session - Workeasy360 stays signed in",
    crmAfter.status !== 200 && w360After.body && w360After.body.authenticated === true,
    { crmAfter, w360: w360After.status },
  );
  const from = await openFromMyProducts(page, SLUG, "Easy CRM");
  await waitForUrl(page, (u) => u.startsWith(`${CRM}/login`));
  await waitForText(page, "Already a Workeasy360 user?");
  flow("4 CHANGE: preferring Easy CRM's own sign-in opens its sign-in page, not SSO", !page.__nav.slice(from).some((u) => u.includes("/oauth/authorize")) && !page.url().includes("organization="), { nav: page.__nav.slice(from) });
  await audit(page, "Easy CRM sign-in page (Already a Workeasy360 user?)");
  await setPreferences(page, SLUG, { overrides: { "Easy CRM": "auto_sso" } });
  const from2 = await openFromMyProducts(page, SLUG, "Easy CRM");
  await waitForUrl(page, (u) => u.startsWith(CRM) && !u.includes("/login") && !u.includes("/api/"));
  const crm = await crmUser(page);
  flow("4 CHANGE: changed back, the next launch signs in with Workeasy360 again", crm.email === "ben@usso.test" && page.__nav.slice(from2).some((u) => u.includes("/oauth/authorize")), { crm });
}

async function journeyOverride(page) {
  // Brief item 31: global Ask, with per-product overrides, each verified on its own.
  await setPreferences(page, SLUG, {
    def: "ask",
    overrides: { "Easy CRM": "auto_sso", EasyBook: "auto_sso", "Easy Project": "ask", "Easy Help Desk": "auto_sso" },
  });
  const stored = await w360Api(page, "/api/v1/auth/sign-in-preferences");
  const expected = { crm: false, book: false, project: true, helpdesk: false };
  const seen = {};
  for (const [key, product] of Object.entries(PRODUCTS)) {
    const opened = await openProduct(page, SLUG, product);
    seen[product.name] = { asked: opened.asked, who: opened.who.email };
    flow(
      `11 OVERRIDE: global Ask; ${product.name} override ${expected[key] ? "Ask - the chooser appears" : "Automatic - no chooser"}; it opens as Ben`,
      opened.asked === expected[key] && opened.who.email === "ben@usso.test" && !askedForPassword(page, opened.from),
      { opened: seen[product.name] },
    );
  }
  report.notes.push(`11: stored preferences ${JSON.stringify(stored.body && { default: stored.body.default, products: (stored.body.products || []).map((p) => [p.product_code, p.override, p.effective]) })}`);
}

async function journeyProductLogin(page) {
  // Brief item 11: "Prefer each product's own sign-in" where a product has none resolves to
  // NOT SUPPORTED - the settings page explains it and offers no broken option, and Workeasy360 asks.
  await setPreferences(page, SLUG, {
    def: "product_login",
    overrides: { "Easy CRM": "inherit", EasyBook: "inherit", "Easy Project": "inherit", "Easy Help Desk": "inherit" },
  });
  const rows = await page.evaluate(() =>
    Object.fromEntries(
      [...document.querySelectorAll("li")]
        .map((li) => {
          const label = li.querySelector("label");
          const select = label && document.getElementById(label.htmlFor);
          return label && select && select.tagName === "SELECT"
            ? [label.innerText.trim(), { options: [...select.options].map((o) => o.value), status: li.querySelector("p")?.innerText || "" }]
            : null;
        })
        .filter(Boolean),
    ),
  );
  const prefs = await w360Api(page, "/api/v1/auth/sign-in-preferences");
  const byCode = Object.fromEntries((prefs.body.products || []).map((p) => [p.product_code, p]));
  for (const product of [PRODUCTS.book, PRODUCTS.project, PRODUCTS.helpdesk]) {
    const row = rows[product.name] || {};
    const api = byCode[product.code] || {};
    flow(
      `13 PRODUCT_LOGIN: ${product.name} has no sign-in of its own - no 'Prefer ${product.name} sign-in' option, the page says it only uses Workeasy360, and the preference resolves to Ask`,
      row.options && !row.options.includes("product_login") && /only uses workeasy360 sign-in/i.test(row.status) && api.effective === "ask" && api.constraint === "product_requires_sso",
      { row, api: { effective: api.effective, constraint: api.constraint } },
    );
  }
  await audit(page, "Workeasy360 settings: product sign-in preferred, SSO-only products explained");
  for (const product of [PRODUCTS.project, PRODUCTS.helpdesk]) {
    const opened = await openProduct(page, SLUG, product);
    flow(`13 PRODUCT_LOGIN: ${product.name} is asked about (Workeasy360 only), never sent to a sign-in page it does not have`, opened.asked && opened.who.email === "ben@usso.test", { nav: opened.nav.slice(-4) });
  }
  // Easy CRM does have one (sso_optional in this run): the preference sends Ben there.
  const from = await openFromMyProducts(page, SLUG, "Easy CRM");
  await waitForUrl(page, (u) => u.startsWith(CRM));
  await sleep(1500);
  flow("13 PRODUCT_LOGIN: Easy CRM, which has its own sign-in, opens that page", page.__nav.slice(from).some((u) => u.startsWith(`${CRM}/login`)) && !page.__nav.slice(from).some((u) => u.includes("/oauth/authorize")), { nav: page.__nav.slice(from) });
  // Back to Ask: journey 7 needs Ben to be asked about EasyBook ("Use another Workeasy360 account").
  await setPreferences(page, SLUG, { def: "ask" });
}

/** The membership id of the directory row naming `email`, wherever the page puts its rows. */
function findMembershipId(node, email) {
  if (!node || typeof node !== "object") return null;
  if (!Array.isArray(node) && Object.values(node).some((v) => v === email)) {
    return node.membership_id || node.id || null;
  }
  for (const value of Object.values(node)) {
    const found = findMembershipId(value, email);
    if (found) return found;
  }
  return null;
}

async function journeyRemoved() {
  // ADR-044 addendum: removing a member in Workeasy360 ends their open product sessions within
  // the re-check window (30 s in this run, 15 minutes in production), in all four products.
  const ben = await newPage();
  await w360SignIn(ben, "ben@usso.test");
  await setPreferences(ben, SLUG, { def: "auto_sso", overrides: { "Easy CRM": "inherit", EasyBook: "inherit", "Easy Project": "inherit", "Easy Help Desk": "inherit" } });
  const before = {};
  for (const product of Object.values(PRODUCTS)) {
    await openProduct(ben, SLUG, product);
    before[product.name] = (await product.data(ben)).status;
  }
  const ana = await newPage();
  await w360SignIn(ana, "ana@usso.test");
  const members = await w360Api(ana, `/api/v1/orgs/${SLUG}/members?q=ben`);
  const membershipId = findMembershipId(members.body, "ben@usso.test");
  const suspended = await w360Api(ana, `/api/v1/orgs/${SLUG}/members/${membershipId}/suspend`, "POST", {});
  flow("12 REMOVED: Ana suspends Ben in Workeasy360", suspended.status === 200, { membershipId, suspended: { status: suspended.status, body: suspended.body } });
  await sleep(35000); // past this run's 30-second window
  const after = {};
  for (const product of Object.values(PRODUCTS)) {
    await ben.goto(`${product.origin}/`, { waitUntil: "networkidle0", timeout: 40000 });
    after[product.name] = await product.data(ben);
  }
  for (const product of Object.values(PRODUCTS)) {
    const a = after[product.name];
    flow(
      `12 REMOVED: ${product.name} served Ben before; his open session is refused after the window (access ended)`,
      before[product.name] === 200 && a.status === 403 && /workeasy360_access_ended/.test(a.code || ""),
      { before: before[product.name], after: a },
    );
  }
  await ben.goto(`${PROJECT}/`, { waitUntil: "networkidle0" });
  await waitForText(ben, PRODUCTS.project.ended);
  await audit(ben, "Easy Project access ended (member removed in Workeasy360)");
  await reflow(ben, "Easy Project access ended");
  await ben.goto(`${HELPDESK}/dashboard`, { waitUntil: "networkidle0" });
  await waitForText(ben, PRODUCTS.helpdesk.ended);
  await audit(ben, "Easy Help Desk access ended (member removed in Workeasy360)");
  await reflow(ben, "Easy Help Desk access ended");
  // And a new sign-in is refused at Workeasy360 itself.
  const launch = await w360Api(ben, "/api/v1/auth/session");
  report.notes.push(`12: Ben's Workeasy360 session after suspension: ${JSON.stringify(launch.body && launch.body.authenticated)}`);
}

/** Facts of one product's card on the Super Admin Products page. */
async function consoleFacts(staff, name) {
  return staff.evaluate((n) => {
    const section = [...document.querySelectorAll("section")].find((s) => s.querySelector("h2")?.innerText.trim() === n);
    if (!section) return {};
    const dd = (label) => [...section.querySelectorAll("dt")].find((d) => d.innerText.trim() === label)?.nextElementSibling?.innerText.trim();
    const credential = [...section.querySelectorAll("li span")].map((s) => s.innerText.trim()).find((t) => /^[A-Za-z0-9_-]{16,}$/.test(t));
    return { sso: dd("Workeasy360 SSO"), clientId: dd("Client ID"), mode: dd("Mode"), credential, text: section.innerText };
  }, name);
}

async function clickInSection(staff, name, label) {
  // The list re-renders after every action: wait for the card and an enabled button.
  const button = await staff.waitForFunction(
    (n, l) => {
      const section = [...document.querySelectorAll("section")].find((s) => s.querySelector("h2")?.innerText.trim() === n);
      return section && [...section.querySelectorAll("button")].find((b) => b.innerText.trim() === l && !b.disabled);
    },
    { timeout: 25000 },
    name,
    label,
  );
  await button.asElement().click();
}

async function journeyFuture() {
  const staff = await newPage();
  await w360SignIn(staff, SEED.staff_email, { mode: "admin", totpSecret: SEED.staff_totp });
  await staff.goto(`${W360}/admin/products`, { waitUntil: "networkidle0", timeout: 40000 });
  await waitForText(staff, "Add a product SKU");
  await fillLabelled(staff, "Code", "test_future_product");
  await fillLabelled(staff, "Name", "Future Product");
  await clickText(staff, "Create product", "button");
  await waitForText(staff, "Product created as a draft.");
  await clickInSection(staff, "Future Product", "Make available");
  await waitForText(staff, "Product is now available to sell.");
  await clickInSection(staff, "Future Product", "Register integration");
  await staff.waitForSelector('form[aria-label="Register the integration for Future Product"]', { timeout: 20000 });
  await fillLabelled(staff, "Launch URL (https)", `${FUTURE}/`);
  await fillLabelled(staff, "Sign-in mode", "sso_optional", { select: true });
  await fillLabelled(staff, "Product sign-in page", `${FUTURE}/login`);
  await fillLabelled(staff, "Redirect URIs", `${FUTURE}/callback`);
  await fillLabelled(staff, "Sign-out return URIs", `${FUTURE}/login`);
  await clickText(staff, "Register", "button");
  await waitForText(staff, "Integration registered as a draft.");
  await staff.waitForFunction(
    () => {
      const section = [...document.querySelectorAll("section")].find((s) => s.querySelector("h2")?.innerText.trim() === "Future Product");
      return section && [...section.querySelectorAll("dt")].some((d) => d.innerText.trim() === "Workeasy360 SSO");
    },
    { timeout: 15000 },
  );
  const draft = await consoleFacts(staff, "Future Product");
  flow("5 FUTURE: a registered draft says Integration required - the console never pretends SSO works", draft.sso === "Integration required", { sso: draft.sso });
  await audit(staff, "Super Admin Product Manager with the Authentication section");
  // Credentials: the product's public key (its private half never leaves it).
  await clickInSection(staff, "Future Product", "Show credentials");
  await waitForText(staff, "No credentials.");
  await fillLabelled(staff, "Product public key", fs.readFileSync(path.join(WORK, "future.pub"), "utf8"));
  await clickText(staff, "Add public key", "button");
  await waitForText(staff, "Public key added.");
  await clickInSection(staff, "Future Product", "Activate");
  await waitForText(staff, "Integration activated.");
  // The panel reloads after the action; read the facts once it has.
  await staff
    .waitForFunction(
      () => {
        const section = [...document.querySelectorAll("section")].find((s) => s.querySelector("h2")?.innerText.trim() === "Future Product");
        const dt = section && [...section.querySelectorAll("dt")].find((d) => d.innerText.trim() === "Workeasy360 SSO");
        return dt && dt.nextElementSibling && dt.nextElementSibling.innerText.trim() === "Enabled";
      },
      { timeout: 15000 },
    )
    .catch(() => {});
  const facts = await consoleFacts(staff, "Future Product");
  flow(
    "5 FUTURE: activated in the console - SSO Enabled, Optional SSO, client id shown, no secret",
    facts.sso === "Enabled" && /Optional SSO/.test(facts.mode || "") && /^[A-Za-z0-9_-]{20,}$/.test(facts.clientId || "") && !/PRIVATE KEY|secret_hash/.test(facts.text),
    { facts: { ...facts, text: undefined } },
  );
  fs.writeFileSync(path.join(WORK, "future.json"), JSON.stringify({ client_id: facts.clientId, key_id: facts.credential }));
  // The product team deploys its app with its client id and key id.
  // Started directly (what serve.py future runs), so killing it at the end really stops it.
  const rp = spawn(
    PY,
    [
      path.join(REPO, "scripts/universal_sso_browser_check/future_product.py"),
      "--issuer", "http://127.0.0.1:8021",
      "--client-id", facts.clientId,
      "--key-id", facts.credential,
      "--key", path.join(WORK, "future.key"),
      "--origin", FUTURE,
    ],
    { env: { ...process.env, PYTHONUNBUFFERED: "1" }, cwd: REPO, stdio: "ignore" },
  );
  report.notes.push(`5: Future Product relying party started (pid ${rp.pid}).`);
  // Commercial setup (a plan, a price, a 7-day trial): not sign-in, just something to start a trial of.
  execFileSync(PY, [path.join(REPO, "scripts/universal_sso_browser_check/future_plan.py"), "test_future_product"], { env: { ...process.env, ...JSON.parse(process.env.BACKEND_ENV) }, cwd: REPO, encoding: "utf8" });
  await sleep(1500);

  // Ana's default is Automatic (journey 1) - set long before this product existed.
  const page = await newPage();
  await w360SignIn(page, "ana@usso.test");
  await page.goto(`${W360}/o/${SLUG}/explore`, { waitUntil: "networkidle0" });
  await waitForText(page, "Future Product");
  await clickLabel(page, "Start 7-day free trial: Future Product");
  await waitForText(page, "Trial started: Future Product.");
  const from = await openFromMyProducts(page, SLUG, "Future Product");
  await waitForUrl(page, (u) => u.startsWith(`${FUTURE}/`) && !u.includes("/login") && !u.includes("/callback"));
  await waitForText(page, "Signed in as");
  const shown = await bodyText(page);
  flow("5 FUTURE: the new product signs Ana in with her existing default - no code change, no password", shown.includes("ana@usso.test") && shown.includes("test_future_product") && !askedForPassword(page, from), { shown: shown.slice(0, 300) });
  await setPreferences(page, SLUG, { def: "ask" });
  await openFromMyProducts(page, SLUG, "Future Product");
  await waitForUrl(page, (u) => u.startsWith(`${W360}/product-access`));
  await waitForText(page, "How would you like to access Future Product?");
  flow("5 FUTURE: switching the default to Ask applies to the new product too", (await bodyText(page)).includes("Use Future Product sign-in"));
  await clickStarting(page, "Continue with Workeasy360", "button");
  await waitForUrl(page, (u) => u.startsWith(`${FUTURE}/`) && !u.includes("/callback"));
  await waitForText(page, "Signed in as");
  await setPreferences(page, SLUG, { def: "auto_sso" });
  return rp;
}

/** Call a product's own API from its page (same origin, its own cookies). The products shape their
 * refusals differently (Easy CRM `type`, EasyBook `error.code`, Easy Project `code` + `reason`,
 * Easy Help Desk `error.code`, upper case); `code` is normalised to lower case. */
async function productApi(page, apiPath) {
  return page.evaluate(async (p) => {
    const r = await fetch(p, { credentials: "same-origin", headers: { Accept: "application/json" } });
    let body = null;
    try {
      body = await r.json();
    } catch {
      body = null;
    }
    const raw = body && (body.code || (body.error && body.error.code) || body.type);
    const reason = body && (body.reason || (body.details && body.details.reason) || (body.error && body.error.details && body.error.details.reason));
    return { status: r.status, code: raw ? String(raw).toLowerCase() : raw, reason };
  }, apiPath);
}

/** The Help Desk API with a bearer token from the refresh cookie (the frontend's own way in). */
async function helpdeskApi(page, apiPath) {
  return page.evaluate(
    async (api, p) => {
      const refreshed = await fetch(`${api}/api/v1/auth/refresh`, { method: "POST", credentials: "include", headers: { Accept: "application/json" } });
      if (refreshed.status !== 200) return { status: refreshed.status, code: "no_session" };
      const s = await refreshed.json();
      const token = (s.data || s).accessToken;
      const r = await fetch(`${api}/api/v1${p}`, { credentials: "include", headers: { Accept: "application/json", Authorization: `Bearer ${token}` } });
      let body = null;
      try {
        body = await r.json();
      } catch {
        body = null;
      }
      const raw = body && ((body.error && body.error.code) || body.code);
      const reason = body && ((body.error && body.error.details && body.error.details.reason) || body.reason);
      return { status: r.status, code: raw ? String(raw).toLowerCase() : raw, reason };
    },
    HELPDESK_API,
    apiPath,
  );
}

/** The four products, for the journeys that walk all of them. `chooser` is the access chooser's
 * Workeasy360 button: "Continue with Workeasy360" where the product also offers its own sign-in
 * (Easy CRM is sso_optional in this run), "Continue to <name>" where Workeasy360 is the only way in. */
const PRODUCTS = {
  crm: {
    name: "Easy CRM",
    origin: CRM,
    user: crmUser,
    data: (page) => productApi(page, "/api/v1/members/"),
    landed: (u) => u.startsWith(CRM) && !u.includes("/login") && !u.includes("/api/"),
    chooser: "Continue with Workeasy360",
    ended: "Easy CRM access has ended",
    code: "easy_crm",
  },
  book: {
    name: "EasyBook",
    origin: BOOK,
    user: bookUser,
    data: (page) => productApi(page, "/api/bff/organizations/members"),
    landed: (u) => u.startsWith(BOOK) && !u.includes("/login") && !u.includes("/api/"),
    chooser: "Continue to EasyBook",
    ended: "EasyBook access has ended",
    code: "easybook",
  },
  project: {
    name: "Easy Project",
    origin: PROJECT,
    user: projectUser,
    data: (page) => productApi(page, "/api/v1/dashboard"),
    landed: (u) => u.startsWith(PROJECT) && !u.includes("/api/") && !u.includes("sign-in"),
    chooser: "Continue to Easy Project",
    ended: "Easy Project access has ended",
    code: "easy_project",
  },
  helpdesk: {
    name: "Easy Help Desk",
    origin: HELPDESK,
    user: helpdeskUser,
    data: (page) => helpdeskApi(page, "/tickets"),
    landed: (u) => u.startsWith(HELPDESK) && !u.includes("/login") && !u.includes("/api/") && !u.includes("/access-ended"),
    chooser: "Continue to Easy Help Desk",
    ended: "Your access to Easy Help Desk has ended",
    code: "easy_helpdesk",
  },
};

/** Open a product from My Products and land in it, through the chooser if this person is asked.
 * Returns {asked, nav, who}. */
async function openProduct(page, slug, product) {
  const from = await openFromMyProducts(page, slug, product.name);
  await waitForUrl(page, (u) => u.startsWith(`${W360}/product-access`) || product.landed(u));
  const asked = page.url().startsWith(`${W360}/product-access`);
  if (asked) {
    // The chooser's heading depends on the product's mode ("How would you like to access ...?" where
    // it also has its own sign-in, "Continue to ..." where Workeasy360 is the only way in): its
    // Workeasy360 button is what both have.
    await clickStarting(page, product.chooser, "button");
    await waitForUrl(page, product.landed);
  }
  const who = await product.user(page);
  return { asked, from, nav: page.__nav.slice(from), who };
}

/** An Explore card's text and buttons, by product name. */
async function exploreCard(page, name) {
  return page.evaluate((n) => {
    const card = [...document.querySelectorAll("article")].find((a) => a.querySelector("h3")?.innerText.trim() === n);
    return card ? { text: card.innerText, buttons: [...card.querySelectorAll("button")].map((b) => b.getAttribute("aria-label") || b.innerText.trim()) } : null;
  }, name);
}

/** Where each product's own "start a Workeasy360 sign-in" lives (no organization hint). */
const START = {
  crm: `${CRM}/api/auth/workeasy360/start`,
  book: `${BOOK}/api/auth/workeasy360/start`,
  project: `${PROJECT}/api/auth/workeasy360/start`,
  helpdesk: `${HELPDESK_API}/api/auth/workeasy360/start`,
};

/** Start a sign-in from the product itself and return the sso_error the product's callback sent the
 * browser on with. Read from the redirect chain: a product that still holds a session for the person
 * (its own, opened during the trial) rightly forwards its sign-in page to its access-ended page. */
async function productSignInError(page, key) {
  const product = PRODUCTS[key];
  const from = page.__locations.length;
  const withError = (u) => u.startsWith(product.origin) && u.includes("sso_error=");
  const seen = () => page.__locations.slice(from).find(withError) || (withError(page.url()) ? page.url() : null);
  await page.goto(START[key], { waitUntil: "networkidle0", timeout: 40000 });
  const deadline = Date.now() + 40000;
  while (!seen() && Date.now() < deadline) {
    if (page.url().startsWith(`${W360}/organizations/select`)) {
      await clickStarting(page, "Trial Company", "button");
      await page.waitForNetworkIdle({ idleTime: 500, timeout: 15000 }).catch(() => {});
    }
    await sleep(300);
  }
  const landed = seen();
  if (!landed) throw new Error(`no sso_error from ${product.name}: ${page.url()} after ${page.__nav.slice(-6).join(" -> ")}`);
  return decodeURIComponent((landed.split("sso_error=")[1] || "").split(/[&#]/)[0]);
}

async function journeyTrial() {
  // Brief item 33, for each of the four products on its own: start the 7-day trial, sign in through
  // Workeasy360, open the product; the trial expires; the launch is denied; the open session loses
  // access within the re-check window; a new sign-in is refused. (Purchase: journey 10.)
  const page = await newPage();
  await w360SignIn(page, "cara@usso.test");
  for (const product of Object.values(PRODUCTS)) {
    // A fresh page each time: the list re-renders after a trial starts, replacing its buttons.
    await page.goto(`${W360}/o/${TRIAL_SLUG}/explore`, { waitUntil: "networkidle0" });
    await clickLabel(page, `Start 7-day free trial: ${product.name}`);
    await waitForText(page, `Trial started: ${product.name}.`);
  }
  // While the trials run, each opens through Workeasy360 and serves her organization. Cara never chose a
  // sign-in preference, so the platform default applies: Automatic, no chooser (one-login brief).
  const trialUsers = {};
  for (const [key, product] of Object.entries(PRODUCTS)) {
    const opened = await openProduct(page, TRIAL_SLUG, product);
    const data = await product.data(page);
    trialUsers[key] = opened.who;
    flow(
      `6 TRIAL: while the 7-day trial runs, ${product.name} opens through Workeasy360 with no question (nothing stored: Automatic) and serves the organization`,
      opened.who.email === "cara@usso.test" && data.status === 200 && !askedForPassword(page, opened.from) && !opened.asked,
      { who: opened.who, data, asked: opened.asked },
    );
  }
  fs.writeFileSync(path.join(WORK, "trial_users.json"), JSON.stringify(trialUsers));
  const lastServed = Date.now();

  // The controlled clock: a week passes.
  const env = { ...process.env, ...JSON.parse(process.env.BACKEND_ENV) };
  for (const product of Object.values(PRODUCTS)) {
    const moved = execFileSync(PY, [path.join(REPO, "scripts/portals_browser_check/time_travel.py"), TRIAL_SLUG, product.code], { env, cwd: REPO, encoding: "utf8" }).trim();
    report.notes.push(`6: controlled clock: ${moved.split("\n").pop()}`);
  }

  // Workeasy360: no way in, a way to buy.
  await page.goto(`${W360}/o/${TRIAL_SLUG}/apps`, { waitUntil: "networkidle0" });
  const apps = await bodyText(page);
  const launches = {};
  for (const product of Object.values(PRODUCTS)) {
    launches[product.name] = await w360Api(page, `/api/v1/orgs/${TRIAL_SLUG}/apps/${product.code}/launch`, "POST", {});
  }
  await page.goto(`${W360}/o/${TRIAL_SLUG}/explore`, { waitUntil: "networkidle0" });
  await waitForText(page, "Trial expired");
  for (const product of Object.values(PRODUCTS)) {
    const card = await exploreCard(page, product.name);
    const launch = launches[product.name];
    flow(
      `6 TRIAL: after ends_at Workeasy360 offers no Open ${product.name}, refuses its launch, and Explore says Trial expired with Upgrade / Buy now`,
      !apps.includes(`Open ${product.name}`) &&
        launch.status >= 400 &&
        !(launch.body && launch.body.redirect_url) &&
        card &&
        card.text.includes("Trial expired") &&
        card.buttons.includes(`Upgrade / Buy now: ${product.name}`),
      { launch: { status: launch.status, body: launch.body }, card },
    );
  }
  await audit(page, "Workeasy360 Explore after the trials ended");

  // The products: each session opened during the trial loses access at its next re-check - the
  // platform's window, 30 s in this run (w360_usso_browser_settings.py), honoured by every product.
  const wait = 35000 - (Date.now() - lastServed);
  if (wait > 0) await sleep(wait);
  const endedPages = { crm: "/", book: "/dashboard", project: "/", helpdesk: "/dashboard" };
  for (const [key, product] of Object.entries(PRODUCTS)) {
    await page.goto(`${product.origin}${endedPages[key]}`, { waitUntil: "networkidle0" });
    await waitForText(page, product.ended);
    const refused = await product.data(page);
    flow(
      `6 TRIAL: ${product.name}'s own session no longer reaches the data - '${product.ended}'`,
      refused.status === 403 && /workeasy360_access_ended/.test(refused.code || ""),
      { refused },
    );
    await audit(page, `${product.name} access ended (trial expired)`);
    await reflow(page, `${product.name} access ended`);
  }

  // A fresh sign-in from each product is refused by Workeasy360: not entitled.
  const errors = {};
  for (const key of Object.keys(PRODUCTS)) errors[PRODUCTS[key].name] = await productSignInError(page, key);
  flow(
    "6 TRIAL: a new sign-in from each of the four products is DENIED by Workeasy360 (not entitled)",
    Object.values(errors).every((e) => e === "not_entitled"),
    { errors },
  );
  await page.goto(`${PROJECT}/#/sign-in?sso_error=not_entitled`, { waitUntil: "networkidle0" });
  await audit(page, "Easy Project sign-in page with a Workeasy360 refusal");
  await page.goto(`${HELPDESK}/login?sso_error=not_entitled`, { waitUntil: "networkidle0" });
  await audit(page, "Easy Help Desk sign-in page with a Workeasy360 refusal");
}

async function journeyPaid() {
  // After journey 6: Cara's four trials have ended. She buys all four (item 33: no PASS by association).
  const page = await newPage();
  await w360SignIn(page, "cara@usso.test");
  for (const product of Object.values(PRODUCTS)) {
    await page.goto(`${W360}/o/${TRIAL_SLUG}/explore`, { waitUntil: "networkidle0" });
    await clickLabel(page, `Upgrade / Buy now: ${product.name}`);
    await waitForUrl(page, (u) => u.includes(`/o/${TRIAL_SLUG}/checkout`));
    await waitForText(page, "Place order and pay");
    await Promise.all([page.waitForNavigation({ timeout: 60000 }).catch(() => null), clickText(page, "Place order and pay")]);
    await waitForUrl(page, (u) => u.includes("/billing/test-checkout"));
    await waitForText(page, "Pay (succeed)");
    await clickText(page, "Pay (succeed)");
    await page.waitForNetworkIdle({ idleTime: 800, timeout: 30000 }).catch(() => {});
    await sleep(1500);
  }
  await page.goto(`${W360}/o/${TRIAL_SLUG}/apps`, { waitUntil: "networkidle0" });
  await waitForText(page, "Open EasyBook");
  const names = Object.values(PRODUCTS).map((p) => p.name);
  const cards = await page.evaluate(
    (ns) =>
      Object.fromEntries(
        ns.map((n) => {
          const article = [...document.querySelectorAll("article")].find((a) => a.querySelector("h2")?.innerText.trim() === n);
          return [n, article ? article.innerText : ""];
        }),
      ),
    names,
  );
  for (const name of names) {
    flow(`10 PAID: after checkout and payment, My Products shows ${name} paid (no trial) with Open`, cards[name].includes(`Open ${name}`) && !/Trial/i.test(cards[name]), { card: cards[name] });
  }
  let trialUsers = {};
  try {
    trialUsers = JSON.parse(fs.readFileSync(path.join(WORK, "trial_users.json"), "utf8"));
  } catch {
    trialUsers = {};
  }
  for (const [key, product] of Object.entries(PRODUCTS)) {
    const opened = await openProduct(page, TRIAL_SLUG, product);
    const before = trialUsers[key] || {};
    flow(
      `10 PAID: ${product.name} opens through Workeasy360 on the paid plan, no second password, the same federated account as during the trial`,
      opened.who.email === "cara@usso.test" && !askedForPassword(page, opened.from) && (!before.id || !opened.who.id || before.id === opened.who.id),
      { who: opened.who, trial: before },
    );
    // The product last heard "deny" (journey 6) and re-asks a denial after 60 s, in real time.
    const started = Date.now();
    let data = await product.data(page);
    while (data.status !== 200 && Date.now() - started < 95000) {
      await sleep(5000);
      if ((await bodyText(page).catch(() => "")).includes(product.ended)) {
        await clickText(page, "Try again", "button").catch(() => {});
        await page.waitForNetworkIdle({ idleTime: 500, timeout: 15000 }).catch(() => {});
      }
      data = await product.data(page);
    }
    const waited = Math.round((Date.now() - started) / 1000);
    report.notes.push(`10: ${product.name} served the renewed organization after ${waited} s (denials are re-asked after 60 s).`);
    flow(`10 PAID: once ${product.name} re-asks (within a minute), the renewed organization is served again`, data.status === 200 && waited <= 95, { data, waited });
  }
}

async function journeySwitch() {
  // A in Easy CRM, then signs out of Workeasy360; B signs in in the same browser.
  const page = await newPage();
  await w360SignIn(page, "ana@usso.test");
  await openFromMyProducts(page, SLUG, "Easy CRM");
  await waitForUrl(page, (u) => u.startsWith(CRM) && !u.includes("/login") && !u.includes("/api/"));
  const asAna = await crmUser(page);
  await page.goto(`${W360}/o/${SLUG}/apps`, { waitUntil: "networkidle0" });
  const out = await w360Api(page, "/api/v1/auth/logout", "POST");
  const session = await w360Api(page, "/api/v1/auth/session");
  await w360SignIn(page, "ben@usso.test");
  // Through the chooser if Ben's preference asks: what is under test is who Easy CRM holds.
  const asBen = (await openProduct(page, SLUG, PRODUCTS.crm)).who;
  flow("7 SWITCH: after A signs out and B signs in, Easy CRM holds B - nothing of A survives", asAna.email === "ana@usso.test" && out.status === 200 && session.body.authenticated === false && asBen.email === "ben@usso.test", { asAna, asBen });

  // "Use another Workeasy360 account" on the chooser (Ben chooses to be asked).
  await setPreferences(page, SLUG, { def: "ask", overrides: { EasyBook: "inherit" } });
  await openFromMyProducts(page, SLUG, "EasyBook");
  await waitForUrl(page, (u) => u.startsWith(`${W360}/product-access`) || u.startsWith(BOOK));
  if (!page.url().startsWith(`${W360}/product-access`)) {
    flow("7 SWITCH: the chooser was shown for EasyBook", false, { at: page.url() });
    return;
  }
  await clickText(page, "Use another Workeasy360 account", "button");
  await waitForUrl(page, (u) => u.startsWith(`${W360}/login`) && u.includes("resume="));
  const signedOut = await w360Api(page, "/api/v1/auth/session");
  await page.type('input[type="email"]', "ana@usso.test");
  await page.type('input[type="password"]', PASSWORD);
  await clickText(page, "Sign in");
  await waitForUrl(page, (u) => u.startsWith(BOOK) && !u.includes("/login") && !u.includes("/api/"));
  const book = await bookUser(page);
  flow("7 SWITCH: 'Use another Workeasy360 account' signs Ben out; EasyBook opens as Ana", signedOut.body.authenticated === false && book.email === "ana@usso.test", { book });
}

async function journeyLogout() {
  const page = await newPage();
  await w360SignIn(page, "ana@usso.test");
  await openFromMyProducts(page, SLUG, "Easy CRM");
  await waitForUrl(page, (u) => u.startsWith(CRM) && !u.includes("/login") && !u.includes("/api/"));
  await clickLabel(page, "Account menu");
  await clickStarting(page, "Sign out of Easy CRM and Workeasy360", '[role="menuitem"]');
  await waitForUrl(page, (u) => u.startsWith(`${W360}/sign-out`));
  await waitForText(page, "Sign out of Workeasy360 too?");
  await audit(page, "Workeasy360 sign-out confirmation (RP-initiated logout)");
  await reflow(page, "sign-out confirmation");
  const stillIn = await w360Api(page, "/api/v1/auth/session");
  await clickStarting(page, "Sign out of Workeasy360", "button");
  await waitForUrl(page, (u) => u.startsWith(`${CRM}/login`));
  const crmSession = await crmUser(page);
  const w360 = await newPageSameContext(page, `${W360}/api/v1/auth/session`);
  flow(
    "8 LOGOUT: Easy CRM ended its own session, Workeasy360 asked first, then both are signed out",
    stillIn.body.authenticated === true && crmSession.status !== 200 && w360.authenticated === false,
    { crmSession, w360 },
  );
}

/** A product's own sign-out, as its account menu offers it. `everywhere` picks "Sign out of <product>
 * and Workeasy360" (RP-initiated logout) over the product-only one. */
/** EasyBook's first sign-in for an organization asks for its fiscal year (the suggested dates cover
 * today): confirm it, as a person would, so the app with its account menu appears. */
async function finishBookOnboarding(page) {
  if (!page.url().startsWith(`${BOOK}/onboarding/`)) return;
  if (page.url().includes("/onboarding/fiscal-year")) {
    await clickText(page, "Confirm fiscal year", "button");
    await waitForUrl(page, (u) => u.startsWith(`${BOOK}/dashboard`));
    report.notes.push("EasyBook: first sign-in for the organization confirmed its suggested fiscal year (onboarding).");
  } else {
    throw new Error(`EasyBook onboarding step not handled by the check: ${brief(page.url())}`);
  }
}

async function productSignOut(page, key, everywhere) {
  const product = PRODUCTS[key];
  if (key === "book") await finishBookOnboarding(page);
  // Easy Help Desk's product-only item is plain "Sign out"; the others name the product.
  const label = everywhere ? `Sign out of ${product.name} and Workeasy360` : key === "helpdesk" ? "Sign out" : `Sign out of ${product.name}`;
  if (key === "project") {
    const trigger = await page.waitForSelector('button.identity-trigger[aria-haspopup="dialog"]', { visible: true, timeout: 25000 });
    await trigger.click();
    await clickText(page, label, "button");
  } else if (key === "book") {
    // A disclosure button named by screen-reader text ("Account menu for Ana Owner").
    const trigger = await page.waitForFunction(
      () => [...document.querySelectorAll("button[aria-expanded]")].find((b) => b.textContent.includes("Account menu for") && !b.disabled),
      { timeout: 25000 },
    );
    await trigger.asElement().click();
    await clickText(page, label, "button");
  } else if (key === "helpdesk") {
    // Its account button is named after the person ("Account menu for Ana Owner").
    const trigger = await page.waitForSelector('[aria-label^="Account menu for"]:not([disabled])', { visible: true, timeout: 25000 });
    await trigger.click();
    await clickText(page, label, '[role="menuitem"], button');
  } else {
    await clickLabel(page, "Account menu");
    await clickText(page, label, '[role="menuitem"]');
  }
}

async function journeySwitchProducts() {
  // Account switching for EasyBook, Easy Project and Easy Help Desk (Easy CRM: journeySwitch): A signs
  // in and opens the product; A signs out of Workeasy360; B signs in and opens the same product -
  // which must hold B, never A's session.
  const keys = ["book", "project", "helpdesk"];
  const page = await newPage();
  await w360SignIn(page, "ana@usso.test");
  const asAna = {};
  for (const key of keys) asAna[key] = (await openProduct(page, SLUG, PRODUCTS[key])).who;
  await page.goto(`${W360}/o/${SLUG}/apps`, { waitUntil: "networkidle0" });
  const out = await w360Api(page, "/api/v1/auth/logout", "POST");
  await w360SignIn(page, "ben@usso.test");
  for (const key of keys) {
    const product = PRODUCTS[key];
    const opened = await openProduct(page, SLUG, product);
    // Back on the product's own origin a moment later, it still holds B.
    await page.goto(`${product.origin}/`, { waitUntil: "networkidle0" });
    const again = await product.user(page);
    flow(
      `7 SWITCH: after A signs out of Workeasy360 and B signs in, ${product.name} holds B - nothing of A survives`,
      asAna[key].email === "ana@usso.test" && out.status === 200 && opened.who.email === "ben@usso.test" && again.email === "ben@usso.test" && (!asAna[key].id || asAna[key].id !== opened.who.id),
      { asAna: asAna[key], asBen: opened.who, again },
    );
  }
}

async function journeyLogoutProducts() {
  // EasyBook, Easy Project and Easy Help Desk (Easy CRM: journeyLogout): product-local sign-out
  // leaves Workeasy360 signed in; "and Workeasy360" asks at Workeasy360 first, then ends both and
  // the browser is back on the product's sign-in page - never signed straight back in.
  const postLogout = {
    book: (u) => u.startsWith(`${BOOK}/login`),
    project: (u) => u.startsWith(PROJECT) && !u.includes("/api/"),
    helpdesk: (u) => u.startsWith(`${HELPDESK}/login`),
  };
  for (const key of ["book", "project", "helpdesk"]) {
    const product = PRODUCTS[key];
    const page = await newPage();
    await w360SignIn(page, "ana@usso.test");
    await openProduct(page, SLUG, product);
    await audit(page, `${product.name} signed in (account menu reachable by keyboard)`);
    await productSignOut(page, key, false);
    await waitForUrl(page, (u) => u.startsWith(product.origin) && (u.includes("sign-in") || u.includes("/login")));
    const productAfter = await product.user(page);
    const w360After = await newPageSameContext(page, `${W360}/api/v1/auth/session`);
    flow(
      `8 LOGOUT: 'Sign out of ${product.name}' ends only the product's session - Workeasy360 stays signed in`,
      productAfter.status !== 200 && w360After.authenticated === true,
      { productAfter, w360: w360After.authenticated },
    );
    await audit(page, `${product.name} sign-in page (Continue with Workeasy360)`);
    await reflow(page, `${product.name} sign-in page`);

    await openProduct(page, SLUG, product);
    await productSignOut(page, key, true);
    await waitForUrl(page, (u) => u.startsWith(`${W360}/sign-out`));
    await waitForText(page, "Sign out of Workeasy360 too?");
    const stillIn = await w360Api(page, "/api/v1/auth/session");
    await clickStarting(page, "Sign out of Workeasy360", "button");
    await waitForUrl(page, postLogout[key]);
    // Still there a moment later: the product shows its sign-in page rather than starting a new
    // sign-in (Easy Project's sign-out address is its root - its sign-out marker is what holds).
    await sleep(2500);
    const settledAt = page.url();
    const productSession = await product.user(page);
    const w360 = await newPageSameContext(page, `${W360}/api/v1/auth/session`);
    flow(
      `8 LOGOUT: 'Sign out of ${product.name} and Workeasy360' - Workeasy360 asks first, then both are signed out and the browser is back at ${product.name}'s sign-in`,
      stillIn.body.authenticated === true && productSession.status !== 200 && w360.authenticated === false && postLogout[key](settledAt),
      { productSession, w360, settledAt: brief(settledAt) },
    );
  }
}

// ------------------------------------------------------------- one login (14, 15) --

/** A page deeper than each product's home, for "a deep link survives the sign-in". */
const DEEP = { crm: "/contacts", book: "/sales/invoices", project: "/#/settings", helpdesk: "/settings/profile" };

/** The product's own sign-in button, on screen now. */
const signInButtonShown = (page) =>
  page
    .evaluate(() =>
      [...document.querySelectorAll("button, a")].some(
        (el) => /^(Continue with Workeasy360|Access Easy CRM|Access EasyBook)/.test((el.innerText || "").trim()) && el.getBoundingClientRect().height > 0,
      ),
    )
    .catch(() => false);

const onW360Login = (u) => u.startsWith(W360) && new URL(u).pathname === "/login";

/** Where a browser settles after a product address was typed: signed in to the product (its own
 * session answers), on the product's sign-in page, or on Workeasy360's central sign-in. */
async function settle(page, key, timeout = 45000) {
  const product = PRODUCTS[key];
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const u = page.url();
    if (onW360Login(u)) return { where: "w360_login", at: u };
    if (u.startsWith(product.origin) && !u.includes("/api/")) {
      // Let the page do what it does on load (its own session check, a redirect) first.
      await page.waitForNetworkIdle({ idleTime: 800, timeout: 10000 }).catch(() => {});
      if (page.url() === u) {
        if (await signInButtonShown(page)) return { where: "product_sign_in", at: u };
        const who = await product.user(page).catch(() => ({ status: 0 }));
        if (who.status === 200) return { where: "signed_in", at: page.url(), who };
      }
    }
    await sleep(300);
  }
  return { where: "timeout", at: page.url() };
}

/** Type a product's address into the browser and see what happens. */
async function directVisit(page, key, pathname) {
  const navFrom = page.__nav.length;
  const seenFrom = page.__signInSeen.length;
  await page.goto(`${PRODUCTS[key].origin}${pathname}`, { waitUntil: "domcontentloaded", timeout: 40000 });
  const settled = await settle(page, key);
  const nav = page.__nav.slice(navFrom);
  return {
    ...settled,
    nav,
    started: nav.some((u) => u.includes("/api/auth/workeasy360/start")),
    viaIdp: nav.some((u) => u.includes("/oauth/authorize")),
    chooser: nav.some((u) => u.includes("/product-access")),
    password: askedForPassword(page, navFrom),
    signInShown: page.__signInSeen.slice(seenFrom),
  };
}

/** Whether `at` is `pathname` of the product (Easy Project's routes are hash routes). */
function landedOn(at, pathname) {
  const u = new URL(at);
  return pathname.startsWith("/#") ? u.hash.startsWith(pathname.slice(1)) : u.pathname.startsWith(pathname);
}

async function journeyDirect() {
  // The brief's journey B and DIRECT URL JOURNEY. Easy CRM starts by itself only when its own password
  // sign-in is off - production's mode, pass 2 (USSO_CRM_SSO_ONLY=1); in pass 1 it is sso_optional.
  const keys = Object.keys(PRODUCTS);
  const autoStarts = (key) => key !== "crm" || CRM_SSO_ONLY;
  const page = await newPage();
  await w360SignIn(page, "ana@usso.test");
  // Nothing stored: the platform default applies (Automatic, one-login brief).
  await page.goto(`${W360}/o/${SLUG}/settings/sign-in-preferences`, { waitUntil: "networkidle0", timeout: 40000 });
  await clickText(page, "Reset all to default", "button");
  await waitForText(page, "Reset. Workeasy360 will sign you in to every product automatically.");
  const prefs = await w360Api(page, "/api/v1/auth/sign-in-preferences");
  flow(
    "14 DIRECT: Ana has no stored sign-in preference - the platform default is Automatic",
    prefs.status === 200 && prefs.body.default === "auto_sso" && !prefs.body.default_is_set,
    { default: prefs.body && prefs.body.default, set: prefs.body && prefs.body.default_is_set },
  );

  // Signed in to Workeasy360 only; she types each product's address.
  for (const key of keys) {
    const product = PRODUCTS[key];
    const v = await directVisit(page, key, "/");
    if (autoStarts(key)) {
      flow(
        `14 DIRECT: typing ${product.name}'s address with only a Workeasy360 session - it starts sign-in itself, Workeasy360 reuses its session, ${product.name} opens signed in as Ana: no sign-in page, no password, no chooser`,
        v.where === "signed_in" && v.who.email === "ana@usso.test" && v.started && v.viaIdp && !v.password && !v.chooser && !v.signInShown.length,
        { where: v.where, at: brief(v.at), who: v.who, signInShown: v.signInShown, nav: v.nav.slice(-8) },
      );
    } else {
      const text = await bodyText(page);
      flow(
        "14 DIRECT: Easy CRM with its own password sign-in also on (pass 1) shows its sign-in page with both ways in - it cannot know which the person wants",
        v.where === "product_sign_in" && !v.viaIdp && text.includes("Already a Workeasy360 user?"),
        { where: v.where, at: brief(v.at), nav: v.nav.slice(-6) },
      );
    }
  }

  // "Sign out of <product>" (the product only): typing its address again must not sign her back in.
  for (const key of keys.filter(autoStarts)) {
    const product = PRODUCTS[key];
    await page.goto(`${product.origin}/`, { waitUntil: "networkidle0", timeout: 40000 });
    await settle(page, key);
    await productSignOut(page, key, false);
    // Signed out once the product shows its sign-in page (the browser was on its origin already).
    const signedOutBy = Date.now() + 25000;
    while (!(await signInButtonShown(page)) && Date.now() < signedOutBy) await sleep(300);
    const again = await directVisit(page, key, "/");
    const w360 = await newPageSameContext(page, `${W360}/api/v1/auth/session`);
    flow(
      `14 DIRECT: after 'Sign out of ${product.name}', typing its address shows its sign-in page instead of signing Ana straight back in (Workeasy360 stays signed in)`,
      again.where === "product_sign_in" && !again.viaIdp && w360.authenticated === true,
      { where: again.where, at: brief(again.at), w360: w360.authenticated, nav: again.nav.slice(-6) },
    );
    if (key === "project") await audit(page, "Easy Project sign-in page after a sign-out (no automatic sign-in)");
    // One click there, no password: signed in again (which also clears the sign-out marker).
    const before = page.__nav.length;
    await clickStarting(page, { crm: "Access Easy CRM", book: "Access EasyBook" }[key] || "Continue with Workeasy360", "button, a");
    const back = await settle(page, key);
    flow(
      `14 DIRECT: on ${product.name}'s sign-in page one click signs Ana in again - no password`,
      back.where === "signed_in" && back.who.email === "ana@usso.test" && !askedForPassword(page, before),
      { where: back.where, at: brief(back.at), who: back.who },
    );
  }

  // Deep links survive the sign-in: a fresh browser, signed in to Workeasy360 only.
  const deep = await newPage();
  await w360SignIn(deep, "ana@usso.test");
  for (const key of keys.filter(autoStarts)) {
    const product = PRODUCTS[key];
    const v = await directVisit(deep, key, DEEP[key]);
    flow(
      `14 DIRECT: a deep link (${DEEP[key]}) survives - ${product.name} signs Ana in with no password and opens that page`,
      v.where === "signed_in" && v.who.email === "ana@usso.test" && landedOn(v.at, DEEP[key]) && !v.password && !v.signInShown.length,
      { where: v.where, at: brief(v.at), signInShown: v.signInShown, nav: v.nav.slice(-6) },
    );
  }
  await deep.browserContext().close();

  // No Workeasy360 session at all: the product sends the browser to the central sign-in, then opens.
  let audited = false;
  for (const key of keys.filter(autoStarts)) {
    const product = PRODUCTS[key];
    const fresh = await newPage();
    const v = await directVisit(fresh, key, "/");
    let after = { where: "not reached" };
    if (v.where === "w360_login") {
      if (!audited) {
        await audit(fresh, "Workeasy360 central sign-in reached from a product's address");
        audited = true;
      }
      await fresh.waitForSelector('input[type="email"]', { visible: true, timeout: 25000 });
      await fresh.type('input[type="email"]', "ana@usso.test");
      await fresh.type('input[type="password"]', PASSWORD);
      await clickText(fresh, "Sign in");
      await waitForUrl(fresh, (u) => !onW360Login(u));
      after = await settle(fresh, key);
    }
    flow(
      `14 DIRECT: with no Workeasy360 session, typing ${product.name}'s address shows the central Workeasy360 sign-in (never a product password form); after it ${product.name} opens signed in`,
      v.where === "w360_login" && !v.signInShown.length && after.where === "signed_in" && after.who.email === "ana@usso.test",
      { first: v.where, signInShown: v.signInShown, after: { where: after.where, at: after.at && brief(after.at), who: after.who } },
    );
    await fresh.browserContext().close();
  }
  await page.browserContext().close();
}

/** Raw text of a listing a product shows its signed-in person: its people, or for Easy Project
 * (no people table) its session's organization. */
async function productListing(page, key) {
  const paths = { crm: "/api/v1/members/", book: "/api/bff/organizations/members", project: "/api/auth/session" };
  if (key === "helpdesk") {
    return page.evaluate(async (api) => {
      const refreshed = await fetch(`${api}/api/v1/auth/refresh`, { method: "POST", credentials: "include", headers: { Accept: "application/json" } });
      if (refreshed.status !== 200) return { status: refreshed.status, text: "" };
      const token = ((await refreshed.json()).data || {}).accessToken;
      const r = await fetch(`${api}/api/v1/users`, { credentials: "include", headers: { Accept: "application/json", Authorization: `Bearer ${token}` } });
      return { status: r.status, text: await r.text() };
    }, HELPDESK_API);
  }
  return page.evaluate(async (p) => {
    const r = await fetch(p, { credentials: "same-origin", headers: { Accept: "application/json" } });
    return { status: r.status, text: await r.text() };
  }, paths[key]);
}

async function journeyCrossOrg() {
  // Cara belongs to Trial Company only (after journey 10 it holds all four products). Neither the
  // launcher's organization hint nor anything else opens Customer Company's data to her.
  const page = await newPage();
  await w360SignIn(page, "cara@usso.test");
  for (const [key, product] of Object.entries(PRODUCTS)) {
    const from = page.__locations.length;
    const navFrom = page.__nav.length;
    await page.goto(`${product.origin}/?organization=${SEED.company_id}`, { waitUntil: "networkidle0", timeout: 40000 });
    const deadline = Date.now() + 40000;
    let seen = null;
    while (!seen && Date.now() < deadline) {
      seen = page.__locations.slice(from).find((u) => u.startsWith(product.origin) && u.includes("sso_error=")) || (page.url().includes("sso_error=") ? page.url() : null);
      if (!seen) await sleep(300);
    }
    await page.waitForNetworkIdle({ idleTime: 800, timeout: 15000 }).catch(() => {});
    const code = seen ? decodeURIComponent(seen.split("sso_error=")[1].split(/[&#]/)[0]) : null;
    const who = await product.user(page);
    flow(
      `15 CROSS-ORG: Cara asks ${product.name} for Customer Company, which is not hers - Workeasy360 refuses and ${product.name} opens no session`,
      Boolean(code) && who.status !== 200 && !askedForPassword(page, navFrom),
      { code, who, nav: page.__nav.slice(navFrom).slice(-6) },
    );
  }
  for (const [key, product] of Object.entries(PRODUCTS)) {
    const opened = await openProduct(page, TRIAL_SLUG, product);
    const listing = await productListing(page, key);
    const bound = key === "project" ? listing.text.includes(SEED.trial_company_id) && !listing.text.includes(SEED.company_id) : true;
    flow(
      `15 CROSS-ORG: Cara's ${product.name} session is bound to Trial Company - it serves her and shows no one from Customer Company`,
      opened.who.email === "cara@usso.test" && listing.status === 200 && !/(ana|ben)@usso\.test/.test(listing.text) && bound,
      { who: opened.who, listing: { status: listing.status, size: listing.text.length } },
    );
  }
}

async function newPageSameContext(page, url) {
  const other = await page.browserContext().newPage();
  await other.goto(url, { waitUntil: "networkidle0" });
  const body = JSON.parse(await other.evaluate(() => document.body.innerText));
  await other.close();
  return body;
}

async function journeyReauth() {
  const page = await newPage();
  await w360SignIn(page, "ana@usso.test");
  await openFromMyProducts(page, SLUG, "Easy CRM");
  await waitForUrl(page, (u) => u.startsWith(CRM) && !u.includes("/login") && !u.includes("/api/"));
  await sleep(23000); // past this run's 20-second re-authentication window (serve.py)
  const patch = (name) =>
    page.evaluate(async (n) => {
      const csrf = (document.cookie.match(/(?:^|; )(?:__Host-)?keel_csrftoken=([^;]*)/) || [])[1];
      const r = await fetch("/api/v1/organizations/current/", {
        method: "PATCH",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json", ...(csrf ? { "X-CSRFToken": decodeURIComponent(csrf) } : {}) },
        body: JSON.stringify({ name: n }),
      });
      let body = null;
      try {
        body = await r.json();
      } catch {
        body = null;
      }
      return { status: r.status, type: body && body.type };
    }, name);
  const refused = await patch("Renamed by Ana");
  const confirm = async (email) => {
    const from = page.__nav.length;
    // What "Confirm with Workeasy360" does (frontend reauth-provider).
    await page.goto(`${CRM}/api/auth/workeasy360/start?reauth=1&next=%2Fsettings`, { waitUntil: "networkidle0" });
    await waitForUrl(page, (u) => u.startsWith(`${W360}/login`) && u.includes("reauth=1"));
    await waitForText(page, "Confirm it's you");
    await page.type('input[type="email"]', email);
    await page.type('input[type="password"]', PASSWORD);
    await clickText(page, "Sign in");
    await waitForUrl(page, (u) => (u.startsWith(CRM) && !u.includes("/api/")) || u.startsWith(`${W360}/product-access`));
    // Only if that person's own preference asks (a full run leaves both on Automatic for Easy CRM).
    if (page.url().startsWith(`${W360}/product-access`)) {
      await clickStarting(page, "Continue with Workeasy360", "button");
      await waitForUrl(page, (u) => u.startsWith(CRM) && !u.includes("/api/"));
    }
    return page.__nav.slice(from);
  };
  // Somebody else's password confirms nothing about Ana, and switches nobody.
  const wrongNav = await confirm("ben@usso.test");
  const stillAna = await crmUser(page);
  const stillRefused = await patch("Renamed by Ben");
  flow(
    "9 REAUTH: 'Confirm it's you' answered by another Workeasy360 account is refused; Easy CRM keeps Ana, unconfirmed",
    wrongNav.some((u) => u.includes("sso_error=reauthentication_wrong_account")) && stillAna.email === "ana@usso.test" && stillRefused.type === "reauth_required",
    { stillAna, stillRefused, wrongNav },
  );
  const nav = await confirm("ana@usso.test");
  await audit(page, "Easy CRM after confirming with Workeasy360");
  const allowed = await patch("Renamed by Ana");
  flow(
    "9 REAUTH: a stale sign-in cannot confirm a sensitive Easy CRM action; Workeasy360 asks for the password (no organization chooser) and then it can",
    refused.status === 403 && refused.type === "reauth_required" && allowed.status === 200 && !nav.some((u) => u.includes("/organizations/select")),
    { refused, allowed, nav },
  );
}

// ---------------------------------------------------------------------- main --

(async () => {
  browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--no-first-run", "--no-default-browser-check"] });
  let rp = null;
  const run = async (key, name, fn) => {
    if (!ONLY.includes(key)) return undefined;
    try {
      return await fn();
    } catch (e) {
      // Where every open page was when it stopped (addresses as `brief` shows them, the start of
      // each page's text), so a stalled wait says what the browser was looking at.
      const where = [];
      for (const p of await browser.pages().catch(() => [])) {
        const text = await p.evaluate(() => (document.body ? document.body.innerText : "")).catch(() => "");
        where.push(`${brief(p.url())} :: ${text.slice(0, 200).replace(/\s+/g, " ")}`);
      }
      flow(`${name} (did not complete)`, false, { error: String(e && e.message ? e.message : e).slice(-1500), where: where.slice(-4) });
      return undefined;
    }
  };
  await run("1", "1 AUTO", journeyAuto);
  const ben = await run("2", "2 ASK", journeyAsk);
  if (ben) {
    await run("3", "3 REMEMBER", () => journeyRemember(ben));
    await run("4", "4 CHANGE", () => journeyChange(ben));
    await run("11", "11 OVERRIDE", () => journeyOverride(ben));
    await run("13", "13 PRODUCT_LOGIN", () => journeyProductLogin(ben));
  }
  await run("14", "14 DIRECT", journeyDirect);
  rp = await run("5", "5 FUTURE", journeyFuture);
  await run("6", "6 TRIAL", journeyTrial);
  await run("10", "10 PAID", journeyPaid);
  // After 10: Cara holds all four products in Trial Company.
  await run("15", "15 CROSS-ORG", journeyCrossOrg);
  await run("7", "7 SWITCH", journeySwitch);
  await run("7", "7 SWITCH (EasyBook, Easy Project, Easy Help Desk)", journeySwitchProducts);
  await run("8", "8 LOGOUT", journeyLogout);
  await run("8", "8 LOGOUT (EasyBook, Easy Project, Easy Help Desk)", journeyLogoutProducts);
  await run("9", "9 REAUTH", journeyReauth);
  // Last: it suspends Ben.
  await run("12", "12 REMOVED", journeyRemoved);
  await browser.close();
  if (rp) rp.kill();
  const bad = report.pages.filter((p) => p.axe.length || p.noFocus.length || p.h1.length !== 1);
  const over = report.reflow.filter((r) => r.overflow > 1);
  console.log(`\nflows: ${report.flows.filter((f) => f.ok).length}/${report.flows.length} passed`);
  console.log(`pages audited: ${report.pages.length}; with axe/focus/h1 findings: ${bad.length}`);
  console.log(`CSP violations: Workeasy360 ${report.csp.length}, product pages ${report.productCsp.length}; console errors: ${report.errors.length}; reflow overflow: ${over.length}`);
  report.productCsp.forEach((c) => console.log(`  product CSP: ${c}`));
  fs.writeFileSync(process.env.OUT || path.join(WORK, "report.json"), JSON.stringify(report, null, 2));
  process.exit(report.flows.every((f) => f.ok) && !bad.length && !report.csp.length && !over.length ? 0 : 1);
})();
