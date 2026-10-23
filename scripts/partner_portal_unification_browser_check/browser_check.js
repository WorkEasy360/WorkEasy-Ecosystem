// Real-browser verification: partner.workeasy360.com is the WorkEasy360 partner
// portal (docs/partner-portal-unification/status.md). The production frontend
// build, a real backend in multi-portal mode, and a local copy of the production
// edge in front of both - deploy/edge/Caddyfile, which the single host now uses
// unchanged - so every request carries the portal's real host name (admin.,
// partner., app., auth.workeasy360.com, mapped to the local edge with Chrome's
// --host-resolver-rules). The plumbing is ../partner_management_browser_check's.
//
// On every audited page: CSP violations and console errors, axe WCAG 2.1 A/AA, a
// visible keyboard focus indicator, exactly one h1; reflow at 1440-320 px.
//
// Journeys, driven through the UI (the Super Admin's second factor is answered
// from the seed's TOTP secret; each partner administrator's password is
// generated per run and typed by the script):
//   H  host routing: partner. serves the partner portal only; its sign-in is the
//      central one on auth.; the admin and partner APIs answer only on their hosts
//   A  Reseller: Add Partner (name, email, password, type) -> the same credentials
//      at partner. open the Reseller workspace as a DRAFT ("isn't active yet") ->
//      Complete Your Profile -> Activate -> the Reseller workspace, its menu
//   B  White-label: ... -> the White-label workspace, its menu
//   C  Affiliate: ... -> the Affiliate workspace, its menu (no customer entries)
//   W  wrong workspace: an affiliate opening the reseller workspace, a reseller
//      opening the white-label one - refused in the UI and by the API
//   R  the short addresses partner./reseller|affiliate|white-label/... redirect
//   M  multi-capability: Affiliate added to the reseller - one account, the
//      primary workspace unchanged, a workspace switcher
//   G  Guest Login for all three: admin. -> partner. (the hand-off of the staff
//      member's own sign-in) -> the partner's workspace, the banner, read-only,
//      Exit Guest Session -> admin./admin/partners/<id>, no partner. session left
//   S  suspension and reactivation, seen by the signed-in partner
//   L  sign-out on partner.
//   P  the typed passwords travelled only to create and to auth.'s sign-in, and the
//      partner pages talked to no other system than the WorkEasy360 portals
// Not part of CI. A manual screen-reader pass is NOT part of it: NOT EXECUTED.
const puppeteer = require("puppeteer-core");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const AXE = fs.readFileSync(process.env.AXE_PATH, "utf8");
// Made fresh for every run (the scratch database is seeded with it).
const PASSWORD = process.env.CHECK_PASSWORD;
if (!PASSWORD) throw new Error("CHECK_PASSWORD is required");
const { STAFF_TOTP, OUT } = process.env;
const EDGE = process.env.EDGE || "127.0.0.1:8088";
const PORTALS = ["admin", "partner", "app", "auth", "api"];
const ORIGIN = Object.fromEntries(PORTALS.map((p) => [p, `http://${p}.workeasy360.com`]));
// The Super Admin console's pages (visit(), addPartner()) are on admin.
const BASE = ORIGIN.admin;
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
  report.pages.push({ label, url: page.url(), axe, tabbed, noFocus, h1 });
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

// ------------------------------------------------------------------ sign-in (multi-portal) --

const hostOf = (page) => new URL(page.url()).host;

/** The one password form: on auth. only, whatever portal sent the visitor there. */
async function enterPassword(page, email, password) {
  await page.waitForSelector('input[type="email"]', { visible: true, timeout: 30000 });
  expectThat(hostOf(page) === "auth.workeasy360.com", `a password form outside auth.: ${page.url()}`);
  await page.type('input[type="email"]', email);
  await page.type('input[type="password"]', password);
  await page.click('button[type="submit"]');
}

/** admin. -> auth. (password, then the TOTP on auth.) -> the console on admin. */
async function staffSignIn(page) {
  await page.goto(`${ORIGIN.admin}/admin`, { waitUntil: "networkidle0" });
  await until(page, () => location.host === "auth.workeasy360.com", null, 30000, "the central sign-in");
  await enterPassword(page, "staff@browser.test", PASSWORD);
  await until(page, () => location.host === "admin.workeasy360.com" && location.pathname.startsWith("/admin"), null, 90000, "the Super Admin console");
  await page.waitForNetworkIdle({ idleTime: 700, timeout: 20000 }).catch(() => {});
}

const WORKSPACE_HOME = /^\/partner\/(reseller|white-label|affiliate)\/dashboard$/;

/**
 * The person opens https://partner.workeasy360.com/ signed out and signs in with
 * exactly what Super Admin typed for them. Returns where the sign-in form was.
 */
async function partnerSignIn(page, email, password) {
  await page.goto(`${ORIGIN.partner}/`, { waitUntil: "networkidle0" });
  await until(page, () => location.host === "auth.workeasy360.com", null, 30000, "the central sign-in");
  const form = page.url();
  expectThat(new URL(form).searchParams.get("mode") === "partner", `partner. sent the visitor to ${form}`);
  await enterPassword(page, email, password);
  await page.waitForFunction(
    (re) => location.host === "partner.workeasy360.com" && new RegExp(re).test(location.pathname),
    { timeout: 60000 },
    WORKSPACE_HOME.source,
  ).catch(async () => {
    throw new Error(`the partner did not reach a workspace: ${page.url()} :: ${(await bodyText(page)).slice(0, 300).replace(/\n+/g, " | ")}`);
  });
  await page.waitForNetworkIdle({ idleTime: 700, timeout: 20000 }).catch(() => {});
  return form;
}

async function signOut(page) {
  await page.click('button[aria-haspopup="menu"]');
  await clickText(page, "Sign out", { exact: true });
  await page.waitForFunction(() => location.pathname.startsWith("/login") || location.host === "auth.workeasy360.com", { timeout: 30000 });
}

/** A request straight to the local edge with a chosen Host (no browser, no cookie). */
function edgeRequest(host, path) {
  const [hostname, port] = EDGE.split(":");
  return new Promise((resolve, reject) => {
    const req = http.request({ hostname, port: Number(port), path, method: "GET", headers: { Host: host, Accept: "application/json" } }, (res) => {
      let data = "";
      res.on("data", (c) => (data += c));
      res.on("end", () => resolve({ status: res.statusCode, location: res.headers.location || "", body: data.slice(0, 300) }));
    });
    req.on("error", reject);
    req.end();
  });
}

/** Whether this browser context has a signed-in session on partner. (read on partner. itself). */
async function partnerSessionAuthenticated(ctx) {
  const probe = await ctx.newPage();
  await probe.goto(`${ORIGIN.partner}/api/v1/auth/session`, { waitUntil: "networkidle0" });
  const text = await probe.evaluate(() => document.body.innerText);
  await probe.close();
  try {
    return JSON.parse(text).authenticated === true;
  } catch {
    throw new Error(`partner. session answer was not JSON: ${text.slice(0, 200)}`);
  }
}

/** Every host the partner's pages talked to. */
const partnerHosts = new Set();
const panelShapedCalls = [];
function watchHosts(page) {
  page.on("request", (req) => {
    const url = new URL(req.url());
    partnerHosts.add(url.host);
    // The old Partner Panel's API shape (/api/auth/login, /api/...) - never WorkEasy360's /api/v1/.
    if (url.pathname.startsWith("/api/") && !url.pathname.startsWith("/api/v1/")) panelShapedCalls.push(req.url());
  });
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

const menus = {}; // type -> the sidebar the partner saw
const contexts = {}; // key -> the partner's own browser context

async function openWorkspaceAsPartner(key, expectedPath, { audited }) {
  const spec = SPECS[key];
  const ctx = await browser.createBrowserContext();
  contexts[key] = ctx;
  const page = await newPage(ctx);
  watchPasswords(page);
  watchHosts(page);
  const form = await partnerSignIn(page, spec.adminEmail, spec.password);
  const path = new URL(page.url()).pathname;
  expectThat(path === expectedPath, `${spec.type} landed on ${page.url()}`);
  expectThat(!(await textHas(page, "isn't active yet")), `${spec.type}: still shown as not active`);
  if (audited) {
    await audit(page, `${spec.type} workspace dashboard (partner.)`);
    await reflowHere(page, `${spec.type} workspace dashboard (partner.)`);
  }
  menus[key] = await sidebarLabels(page);
  return { page, form, path };
}

let browser;

(async () => {
  const hosts = PORTALS.map((p) => `MAP ${p}.workeasy360.com ${EDGE}`).join(",");
  browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: "new",
    args: [
      "--no-sandbox",
      "--disable-gpu",
      `--host-resolver-rules=${hosts}`,
      "--disable-features=HttpsUpgrades,HttpsFirstBalancedModeAutoEnable",
      `--unsafely-treat-insecure-origin-as-secure=${["admin", "partner", "app", "auth"].map((p) => ORIGIN[p]).join(",")}`,
    ],
  });
  const fresh = () => browser.createBrowserContext();
  const staffContext = await fresh();
  const staff = await newPage(staffContext);
  watchPasswords(staff);
  await staffSignIn(staff);

  await flow("H host routing: partner. is the WorkEasy360 partner portal and nothing else", async () => {
    const root = await edgeRequest("partner.workeasy360.com", "/");
    expectThat(root.status === 302 && root.location.endsWith("/partner"), `partner. / answered ${root.status} ${root.location}`);
    const adminOnPartner = await edgeRequest("partner.workeasy360.com", "/api/v1/admin/partners/overview");
    expectThat(adminOnPartner.status === 404, `the admin API on partner.: ${adminOnPartner.status}`);
    const partnerOnAdmin = await edgeRequest("admin.workeasy360.com", "/api/v1/partner/portal");
    expectThat(partnerOnAdmin.status === 404, `the partner API on admin.: ${partnerOnAdmin.status}`);
    const consoleOnPartner = await edgeRequest("partner.workeasy360.com", "/admin/partners");
    expectThat(consoleOnPartner.status === 302 && consoleOnPartner.location.startsWith(ORIGIN.admin), `partner./admin: ${consoleOnPartner.status} ${consoleOnPartner.location}`);
    const customerOnPartner = await edgeRequest("partner.workeasy360.com", "/dashboard");
    expectThat(customerOnPartner.status === 302 && customerOnPartner.location.startsWith(ORIGIN.app), `partner./dashboard: ${customerOnPartner.status} ${customerOnPartner.location}`);
    const signIn = await edgeRequest("partner.workeasy360.com", "/partner/reseller/dashboard");
    const target = new URL(signIn.location, ORIGIN.partner);
    expectThat(
      signIn.status === 302 && target.host === "auth.workeasy360.com" && target.searchParams.get("mode") === "partner" && target.searchParams.get("next") === "/partner/reseller/dashboard",
      `a signed-out workspace visit: ${signIn.status} ${signIn.location}`,
    );
    const page = await newPage(await fresh());
    await page.goto(`${ORIGIN.partner}/`, { waitUntil: "networkidle0" });
    await until(page, () => location.host === "auth.workeasy360.com", null, 30000, "the central sign-in");
    await audit(page, "auth. sign-in for the Partner portal");
    await reflowHere(page, "auth. sign-in for the Partner portal");
    return "partner. -> /partner; /admin -> admin.; /dashboard -> app.; APIs on their own hosts; sign-in on auth. (mode=partner)";
  });

  await flow("A reseller: Add Partner -> same credentials at partner. (draft) -> profile -> activate -> Reseller workspace", async () => {
    const spec = SPECS.reseller;
    const id = await addPartner(staff, spec, { audit: true });
    made.reseller = id;

    // The draft: the credentials work at once; the workspace says it is not active yet.
    const draftCtx = await fresh();
    const draft = await newPage(draftCtx);
    watchPasswords(draft);
    watchHosts(draft);
    await partnerSignIn(draft, spec.adminEmail, spec.password);
    expectThat(new URL(draft.url()).pathname === "/partner/reseller/dashboard", `the draft reseller landed on ${draft.url()}`);
    await untilTextCI(draft, "This partner account isn't active yet", 30000);
    await audit(draft, "Reseller workspace while draft (partner.)");
    await reflowHere(draft, "Reseller workspace while draft (partner.)");
    await draftCtx.close();

    await staff.goto(`${ORIGIN.admin}/admin/partners/${id}`, { waitUntil: "networkidle0" });
    await clickText(staff, "Complete Your Profile", { exact: true });
    await untilStep(staff, "Step 1 of 8: Business information");
    await fillBusinessStep(staff, spec);
    await clickContinue(staff, "Step 2 of 8: Products");
    await allowProduct(staff, "Easy CRM");
    await clickContinue(staff, "Step 3 of 8: Pricing");
    await clickContinue(staff, "Step 4 of 8: Commission");
    await assignRate(staff, "15");
    await clickContinue(staff, "Step 5 of 8: Customer & sales configuration");
    await clickContinue(staff, "Step 6 of 8: Sub-reseller settings");
    await clickContinue(staff, "Step 7 of 8: Team");
    await clickContinue(staff, "Step 8 of 8: Review");
    await activateFromReview(staff);

    const { page, form } = await openWorkspaceAsPartner("reseller", "/partner/reseller/dashboard", { audited: true });
    made.resellerPage = page;
    const menu = menus.reseller;
    for (const want of ["Dashboard", "Customers", "Subscriptions", "Commissions", "Payouts", "Sub-Resellers", "Team & Permissions", "Reports & Analytics", "Support", "Settings"]) {
      expectThat(menu.includes(want), `the reseller menu lacks "${want}": ${menu.join(" | ")}`);
    }
    expectThat(!menu.includes("Brand Settings") && !menu.includes("Referral Links"), `the reseller sees another type's entries: ${menu.join(" | ")}`);
    return `signed in on ${new URL(form).host}, landed on ${ORIGIN.partner}/partner/reseller/dashboard; menu: ${menu.join(", ")}`;
  });

  await flow("B white-label: Add Partner -> profile -> activate -> same credentials at partner. -> White-label workspace", async () => {
    const spec = SPECS.whiteLabel;
    const id = await addPartner(staff, spec);
    made.whiteLabel = id;
    await clickText(staff, "Complete Your Profile", { exact: true });
    await untilStep(staff, "Step 1 of 11: Business information");
    await fillBusinessStep(staff, spec);
    await clickContinue(staff, "Brand identity");
    await type(staff, "Brand name", "Bright Cloud");
    await clickText(staff, "Save & continue", { exact: true });
    await untilStep(staff, "Products");
    await allowProduct(staff, "Easy CRM");
    await clickContinue(staff, "Pricing");
    await clickContinue(staff, "Commission");
    await assignRate(staff, "12.5");
    await clickContinue(staff, "Customer & sales configuration");
    await clickContinue(staff, "Custom domain");
    await clickContinue(staff, "Communication");
    await clickContinue(staff, "Customer portal (optional)");
    await clickContinue(staff, "Team");
    await clickContinue(staff, "Review");
    await activateFromReview(staff);

    const { page } = await openWorkspaceAsPartner("whiteLabel", "/partner/white-label/dashboard", { audited: true });
    made.whiteLabelPage = page;
    const menu = menus.whiteLabel;
    for (const want of ["Dashboard", "Brand Settings", "Custom Domain", "Website Branding", "Customers", "Subscriptions", "Team & Permissions", "Reports & Analytics", "Support", "Settings"]) {
      expectThat(menu.includes(want), `the white-label menu lacks "${want}": ${menu.join(" | ")}`);
    }
    return `landed on ${ORIGIN.partner}/partner/white-label/dashboard; menu: ${menu.join(", ")}`;
  });

  await flow("C affiliate: Add Partner -> profile -> activate -> same credentials at partner. -> Affiliate workspace", async () => {
    const spec = SPECS.affiliate;
    const id = await addPartner(staff, spec);
    made.affiliate = id;
    await clickText(staff, "Complete Your Profile", { exact: true });
    await untilStep(staff, "Step 1 of 7: Profile");
    await fillBusinessStep(staff, spec);
    await clickContinue(staff, "Step 2 of 7: Products promoted");
    await clickContinue(staff, "Step 3 of 7: Commission");
    await assignRate(staff, "20");
    await clickContinue(staff, "Step 4 of 7: Referral program");
    await type(staff, "Campaign name", "Launch");
    await clickText(staff, "Create referral code", { exact: true });
    await untilText(staff, "Referral code created.", 90000);
    await clickContinue(staff, "Step 5 of 7: Payout settings");
    await clickContinue(staff, "Step 6 of 7: Marketing information");
    await clickContinue(staff, "Step 7 of 7: Review");
    await activateFromReview(staff);

    const { page } = await openWorkspaceAsPartner("affiliate", "/partner/affiliate/dashboard", { audited: true });
    made.affiliatePage = page;
    const menu = menus.affiliate;
    const expected = ["Dashboard", "Referral Links", "Referral Codes", "Campaigns", "Marketing Materials", "Leads & Referrals", "Conversions", "Commissions", "Payouts", "Reports & Analytics", "Profile & Settings", "Support"];
    expectThat(JSON.stringify(menu) === JSON.stringify(expected), `the affiliate menu: ${menu.join(" | ")}`);
    return `landed on ${ORIGIN.partner}/partner/affiliate/dashboard; menu: ${menu.join(", ")}`;
  });

  await flow("W wrong workspace: refused in the page and by the API", async () => {
    needs("affiliatePage", "C");
    needs("resellerPage", "A");
    const aff = made.affiliatePage;
    await aff.goto(`${ORIGIN.partner}/partner/reseller/dashboard`, { waitUntil: "networkidle0" });
    await untilText(aff, "You don't have access to the Reseller workspace.", 30000);
    await audit(aff, "Affiliate refused the Reseller workspace");
    const affCustomers = await apiGet(aff, `/api/v1/partner/${made.affiliate}/customers`);
    expectThat(affCustomers.status === 403, `the affiliate read customers: ${affCustomers.status}`);
    const res = made.resellerPage;
    await res.goto(`${ORIGIN.partner}/partner/white-label/dashboard`, { waitUntil: "networkidle0" });
    await untilText(res, "You don't have access to the White-label workspace.", 30000);
    const domain = await post(res, `/api/v1/partner/${made.reseller}/domains`, { hostname: "app.acme-reseller-check.com" });
    expectThat([401, 403].includes(domain.status), `the reseller configured a domain: ${domain.status} ${JSON.stringify(domain.body).slice(0, 160)}`);
    const other = await apiGet(res, `/api/v1/partner/${made.affiliate}/referrals`);
    expectThat(other.status === 404, `partner A read partner B: ${other.status}`);
    await res.goto(`${ORIGIN.partner}/partner/reseller/dashboard`, { waitUntil: "networkidle0" });
    await aff.goto(`${ORIGIN.partner}/partner/affiliate/dashboard`, { waitUntil: "networkidle0" });
    return `UI refusals; API: affiliate customers ${affCustomers.status}, reseller domain ${domain.status}, cross-partner ${other.status}`;
  });

  await flow("R the short addresses on partner. redirect into the workspace", async () => {
    needs("resellerPage", "A");
    const res = made.resellerPage;
    const short = await edgeRequest("partner.workeasy360.com", "/reseller/customers");
    // A temporary redirect (permanent: false in next.config.ts), so a browser does not cache it.
    expectThat([307, 308].includes(short.status) && short.location === "/partner/reseller/customers", `partner./reseller/customers: ${short.status} ${short.location}`);
    await res.goto(`${ORIGIN.partner}/reseller/dashboard`, { waitUntil: "networkidle0" });
    await until(res, () => location.pathname === "/partner/reseller/dashboard", null, 30000, "the reseller dashboard");
    return `${ORIGIN.partner}/reseller/dashboard -> ${res.url()}`;
  });

  await flow("M multi-capability: Affiliate added to the reseller - one account, primary unchanged, a switcher", async () => {
    needs("reseller", "A");
    await staff.goto(`${ORIGIN.admin}/admin/partners/${made.reseller}?tab=users`, { waitUntil: "networkidle0" });
    await untilText(staff, "Primary experience", 30000);
    await choose(staff, "Experience", "Affiliate");
    // The primary workspace stays the reseller one: the landing is the administrator's choice.
    await tick(staff, "Make it the primary experience", false);
    await type(staff, "Why the experience is changing", "The reseller also refers customers now");
    await clickText(staff, "Apply", { exact: true, within: "main" });
    await untilText(staff, "Experience added.", 90000);

    const res = made.resellerPage;
    await res.goto(`${ORIGIN.partner}/`, { waitUntil: "networkidle0" });
    await until(res, () => location.pathname === "/partner/reseller/dashboard", null, 30000, "the primary workspace");
    const portal = await apiGet(res, "/api/v1/partner/portal");
    const entries = portal.body.partners || [];
    expectThat(entries.length === 1, `the person belongs to ${entries.length} partner accounts`);
    const keys = (entries[0].workspaces || []).map((w) => w.key);
    expectThat(JSON.stringify(keys) === JSON.stringify(["reseller", "affiliate"]), `workspaces: ${keys.join(",")}`);
    expectThat(entries[0].landing === "reseller", `landing: ${entries[0].landing}`);
    await untilText(res, "Workspace:", 30000);
    await audit(res, "Reseller + Affiliate: workspace switcher");
    await res.evaluate(() => [...document.querySelectorAll('nav[aria-label="Workspace"] a')].find((a) => a.innerText.trim() === "Affiliate").click());
    await until(res, () => location.pathname === "/partner/affiliate/dashboard", null, 30000, "the affiliate workspace");
    const affMenu = await sidebarLabels(res);
    expectThat(affMenu.includes("Referral Links"), `after switching: ${affMenu.join(" | ")}`);
    const customers = await apiGet(res, `/api/v1/partner/${made.reseller}/customers`);
    expectThat(customers.status === 200, `switching workspace changed what the server allows: customers ${customers.status}`);
    await res.goto(`${ORIGIN.partner}/partner/reseller/dashboard`, { waitUntil: "networkidle0" });
    return `one account; workspaces ${keys.join(" + ")}; primary reseller; switch -> affiliate; customers still ${customers.status}`;
  });

  for (const [key, slug] of [["reseller", "reseller"], ["whiteLabel", "white-label"], ["affiliate", "affiliate"]]) {
    await flow(`G guest login ${SPECS[key].type}: admin. -> partner./partner/${slug}/dashboard -> Exit -> admin.`, async () => {
      needs(key, "A-C");
      const spec = SPECS[key];
      const id = made[key];
      await staff.goto(`${ORIGIN.admin}/admin/partners/all`, { waitUntil: "networkidle0" });
      await clickLabelText(staff, `Guest login to ${spec.name}`);
      await type(staff, `Why are you opening the portal of ${spec.name}?`, "Browser check of the guest view");
      await clickText(staff, "Open guest view", { exact: true, within: "main" });
      await until(staff, (want) => location.host === "partner.workeasy360.com" && location.pathname === want, `/partner/${slug}/dashboard`, 90000, `the ${spec.type} workspace on partner.`);
      await untilText(staff, `Viewing Partner Portal as ${spec.name}.`, 30000);
      if (key === "reseller") {
        await audit(staff, "Guest view (partner.)");
        await reflowHere(staff, "Guest view (partner.)");
      }
      const portal = await apiGet(staff, "/api/v1/partner/portal");
      const entry = (portal.body.partners || []).find((p) => p.partner_id === id);
      expectThat(entry && entry.guest && entry.role === "viewer", `the guest entry: ${JSON.stringify(entry).slice(0, 200)}`);
      const write = await post(staff, `/api/v1/partner/${id}/referrals`, { label: "guest" });
      expectThat(write.status === 403 && write.body && write.body.reason === "guest_read_only", `a guest write answered ${write.status} ${JSON.stringify(write.body).slice(0, 160)}`);
      await clickText(staff, "Exit Guest Session", { exact: true });
      await until(staff, (want) => location.host === "admin.workeasy360.com" && location.pathname === want, `/admin/partners/${id}`, 60000, "the partner's page on admin.");
      const left = await partnerSessionAuthenticated(staffContext);
      expectThat(left === false, "a session stayed signed in on partner. after Exit Guest Session");
      return `landed on ${ORIGIN.partner}/partner/${slug}/dashboard; write ${write.status}; exit -> ${staff.url()}; partner. session left: ${left}`;
    });
  }

  await flow("S suspension: the signed-in partner sees it; reactivation restores it", async () => {
    needs("affiliatePage", "C");
    const spec = SPECS.affiliate;
    const toggle = async (button, done) => {
      await staff.goto(`${ORIGIN.admin}/admin/partners/all`, { waitUntil: "networkidle0" });
      await clickLabelText(staff, `${spec.name} active`);
      await type(staff, "Reason to", "Browser check of suspension and reactivation");
      await clickText(staff, button, { exact: true, within: "main" });
      await untilText(staff, done, 90000);
    };
    await toggle("Suspend", `${spec.name} suspended.`);
    const aff = made.affiliatePage;
    await aff.goto(`${ORIGIN.partner}/partner/affiliate/dashboard`, { waitUntil: "networkidle0" });
    await untilText(aff, "This partner account is suspended.", 30000);
    await audit(aff, "Suspended affiliate workspace");
    const write = await post(aff, `/api/v1/partner/${made.affiliate}/referrals`, { label: "while suspended" });
    expectThat([403, 409].includes(write.status), `a suspended partner's write answered ${write.status} ${JSON.stringify(write.body).slice(0, 160)}`);
    await toggle("Reactivate", `${spec.name} reactivated.`);
    await aff.goto(`${ORIGIN.partner}/partner/affiliate/dashboard`, { waitUntil: "networkidle0" });
    await sleep(1500);
    expectThat(!(await textHas(aff, "suspended")), "still shown as suspended after reactivation");
    return `suspended: banner + write ${write.status}; reactivated: workspace normal`;
  });

  await flow("L sign-out on partner. ends the partner's session", async () => {
    needs("whiteLabelPage", "B");
    const page = made.whiteLabelPage;
    await page.goto(`${ORIGIN.partner}/partner/white-label/dashboard`, { waitUntil: "networkidle0" });
    await signOut(page);
    expectThat((await partnerSessionAuthenticated(contexts.whiteLabel)) === false, "still signed in on partner. after sign-out");
    await page.goto(`${ORIGIN.partner}/partner/white-label/dashboard`, { waitUntil: "networkidle0" });
    await until(page, () => location.host === "auth.workeasy360.com", null, 30000, "the central sign-in");
    return "signed out; the workspace asks for the central sign-in again";
  });

  await flow("P one authentication system: passwords and requests stayed inside WorkEasy360", async () => {
    const allowed = new Set(["/api/v1/admin/partners/create", "/api/v1/auth/login"]);
    const strays = carried.filter((c) => !allowed.has(c.path));
    expectThat(strays.length === 0, `a password travelled to ${JSON.stringify(strays)}`);
    expectThat(echoed.length === 0, `an answer repeated a password: ${JSON.stringify(echoed)}`);
    const hostsSeen = [...partnerHosts];
    const outside = hostsSeen.filter((h) => !PORTALS.some((p) => h === `${p}.workeasy360.com`));
    expectThat(outside.length === 0, `the partner pages called ${outside.join(", ")}`);
    expectThat(panelShapedCalls.length === 0, `Partner Panel-shaped API calls: ${panelShapedCalls.join(", ")}`);
    return `${carried.length} password-carrying requests (create + auth. sign-in only); hosts: ${hostsSeen.join(", ")}`;
  });

  report.menus = menus;
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
  for (const f of report.flows) console.log(f.ok ? "PASS" : "FAIL", f.name, "::", f.detail);
  for (const p of axeBad) console.log("AXE", p.label, JSON.stringify(p.axe));
  for (const p of focusBad) console.log("FOCUS", p.label, p.noFocus.join(", "));
  for (const p of h1Bad) console.log("H1", p.label, JSON.stringify(p.h1));
  for (const r of reflowBad) console.log("REFLOW", r.url, r.width, r.over);
  for (const c of report.csp) console.log("CSP", c);
  for (const e of report.errors) console.log("ERROR", e);
  process.exit(failed.length || axeBad.length || report.csp.length || report.errors.length || reflowBad.length || h1Bad.length ? 1 : 0);
})();

/** Click the control whose aria-label is exactly `label`. */
async function clickLabelText(page, label) {
  const el = await page.waitForSelector(`[aria-label="${label}"]`, { visible: true, timeout: 30000 });
  await el.evaluate((e) => e.scrollIntoView({ block: "center" }));
  await el.click();
}
