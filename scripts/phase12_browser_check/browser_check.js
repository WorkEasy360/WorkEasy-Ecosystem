// Real-browser verification of Phase 12 against the production-shaped STAGING
// stack (Caddy edge with TLS from its internal CA, production settings, the
// TEST payment provider, Mailpit for email, CoreDNS authoritative for the test
// partner domain). Same harness and rules as ../phase10_browser_check:
//   - CSP violations (every page, every journey, including the product launch),
//     counted through a binding so a violation is recorded even if the page
//     navigates away immediately afterwards;
//   - console errors (expected refusals inside a journey listed separately);
//   - axe WCAG 2.1 A/AA, a visible keyboard focus indicator, exactly one h1.
// Journeys, driven through the UI of the production build in real Chrome:
//   Step 8   one independent customer per product: store -> trial -> signup ->
//            verify (Mailpit) -> trial entitlement -> launch (navigation
//            intercepted, never loaded) -> SSO status -> pay (TEST provider) ->
//            Active -> staff suspend -> read-only, launch refused -> staff
//            reactivate -> launch again -> customer cancel.
//   Step 9   one organization holding a mix (paid, paid, trial, not purchased),
//            then all four.
//   Step 10  reseller (Add Reseller wizard, 2 of 4 products), provisioning,
//            owner activation, sales, a refused sale, commission, termination.
//   Step 11  white-label (brand, logo, colour, preview, domain, DNS TXT via the
//            CoreDNS zone file, verify, activate) and the D-035 decision B
//            behaviour on the partner's own hostname.
//   Step 12  accessibility; keyboard-only checks.
//   Step 37  CSRF from a real signed-in page; Step 38 CSP count.
// The browser is never allowed to load a real product site: every request to a
// host other than the staging hosts is aborted and recorded.
// A manual screen-reader pass is NOT part of this check: reported NOT EXECUTED.
// Not part of the repository's CI.
const path = require("path");
const fs = require("fs");
const https = require("https");
const crypto = require("crypto");
const zlib = require("zlib");
const puppeteer = require("puppeteer-core");

// ------------------------------------------------------------------ configuration --

const SP = process.env.SP || "C:/Users/aqw03/AppData/Local/Temp/claude/c--Users-aqw03-OneDrive-Desktop-Workeasy360/dc40f0f0-5bf4-4d49-b7df-de1e3e87133b/scratchpad";
const STG = `${SP}/stg`;
const CANONICAL_HOST = "app.staging.workeasy360.com";
const BASE = `https://${CANONICAL_HOST}`;
const EDGE_PORT = Number(process.env.EDGE_PORT || 58443);
const MAILPIT = process.env.MAILPIT || "http://127.0.0.1:58026";
const ZONE_FILE = process.env.ZONE_FILE || `${STG}/coredns/wl-brand.com.zone`;
const CHROME = process.env.CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const AXE = fs.readFileSync(process.env.AXE_PATH || `${SP}/pptr/axe.min.js`, "utf8");
const OUT = process.env.OUT || `${STG}/browser-report.json`;
const SHOTS = process.env.SHOTS || `${STG}/shots`;
const RUN = process.env.RUN_ID || new Date().toISOString().replace(/\D/g, "").slice(4, 14);
const PASSWORD = "Browser-Test-Password-1!";
const NEW_PASSWORD = "Another-Browser-Password-2!";
const STAFF_EMAIL = "superadmin@staging.workeasy360.com";
const STAFF_PASSWORD = fs.readFileSync(`${STG}/superadmin_password`, "utf8").trim();
const STAFF_TOTP_FILE = `${STG}/superadmin_totp`;
const SPKI = [`${STG}/caddy-intermediate.crt.spki`, `${STG}/caddy-root.crt.spki`].map((f) => fs.readFileSync(f, "utf8").trim()).join(",");
const CA = fs.readFileSync(`${STG}/caddy-root.crt`);
const WL_A = "app.wl-brand.com";
const WL_B = "other.wl-brand.com";
const WL_UNAPPROVED = "shop.wl-brand.com";
const ALLOWED_HOSTS = new Set([CANONICAL_HOST, WL_A, WL_B, WL_UNAPPROVED]);
const CHROME_ARGS = [
  "--no-sandbox",
  `--host-resolver-rules=${[...ALLOWED_HOSTS].map((h) => `MAP ${h} 127.0.0.1:${EDGE_PORT}`).join(", ")}`,
  // Pins the staging CA's keys (intermediate and root). Not a blanket disable.
  `--ignore-certificate-errors-spki-list=${SPKI}`,
];
const REGISTRY = JSON.parse(fs.readFileSync(path.join(__dirname, "../../infra/ecosystem/products.json"), "utf8"));
const KEYS = { easy_crm: "CRM", easybook: "BOOK", easy_project: "PROJECT", easy_helpdesk: "HELPDESK" };
const PRODUCTS = REGISTRY.products.map((p) => ({ key: KEYS[p.code], code: p.code, slug: p.slug, name: p.name, launchUrl: p.integration.launch_url }));
const byKey = Object.fromEntries(PRODUCTS.map((p) => [p.key, p]));
const ONLY = (process.env.ONLY || "").split(",").filter(Boolean); // e.g. ONLY=8,9 to run a subset while fixing the harness
const runs = (step) => ONLY.length === 0 || ONLY.includes(String(step));

const report = {
  run: RUN,
  started: new Date().toISOString(),
  base: BASE,
  chrome_args: CHROME_ARGS.map((a) => (a.startsWith("--ignore-certificate-errors-spki-list") ? "--ignore-certificate-errors-spki-list=<staging intermediate + root SPKI>" : a)),
  pages: [],
  csp: [],
  errors: [],
  expected: [],
  flows: [],
  notes: [],
  launches: [],
  blocked_requests: [],
  keyboard: [],
  bugs: [],
  matrix: {},
  not_executed: [
    "Manual screen-reader pass (NVDA/JAWS/VoiceOver): NOT EXECUTED - axe and keyboard checks are automated proxies only.",
    "Loading any real product site: NOT EXECUTED by design - every navigation to a product's launch URL is intercepted and aborted; only the target URL is recorded.",
    "Product-side single sign-on: NOT EXECUTED - no product has an OIDC client (SSO integration_required); no credential was registered.",
    "D-037 enrolment path (a staff member with NO second factor sees the enrolment form, then Continue, then the code field): NOT EXECUTED - the only staff account on staging already has a TOTP factor, and creating staff accounts is outside this check.",
  ],
};
const MATRIX_ROWS = ["Catalog", "Trial", "Purchase", "Entitlement", "Dashboard", "Launch", "SSO", "Suspend", "Resume", "Cancel", "Reseller", "Commission", "White-label", "Browser E2E"];
for (const row of MATRIX_ROWS) report.matrix[row] = Object.fromEntries(PRODUCTS.map((p) => [p.key, { status: "NOT EXERCISED", evidence: "" }]));
function mark(productKey, row, status, evidence = "") {
  report.matrix[row][productKey] = { status, evidence: String(evidence).slice(0, 600) };
}
function bug(title, evidence) {
  report.bugs.push({ title, ...evidence });
  console.log("  BUG:", title);
}

let expectingFailure = 0;
let inJourney = 0;
let currentFlow = "(setup)";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
fs.mkdirSync(SHOTS, { recursive: true });

// ------------------------------------------------------------------ plumbing --

// Idempotent and race-free: the page created by newPage() and the same page seen
// through "targetcreated" share one instrumentation promise.
function instrument(page) {
  if (!page.__w360) page.__w360 = doInstrument(page);
  return page.__w360.then(() => page);
}

async function doInstrument(page) {
  page.setDefaultTimeout(30000);
  await page.exposeFunction("__w360Csp", (line) => {
    report.csp.push({ flow: currentFlow, url: page.url(), violation: line });
    console.log("  CSP VIOLATION:", page.url(), line);
  });
  await page.evaluateOnNewDocument(() => {
    window.__csp = [];
    document.addEventListener("securitypolicyviolation", (e) => {
      const line = `${e.violatedDirective} blocked=${e.blockedURI} source=${e.sourceFile || ""}:${e.lineNumber || ""}`;
      window.__csp.push(line);
      try {
        window.__w360Csp(line);
      } catch {
        /* the binding is always there; never let the listener throw */
      }
    });
  });
  await page.setRequestInterception(true);
  page.on("request", (req) => {
    const url = req.url();
    let host = "";
    try {
      host = new URL(url).hostname;
    } catch {
      host = "";
    }
    if (url.startsWith("data:") || url.startsWith("blob:") || ALLOWED_HOSTS.has(host)) {
      req.continue().catch(() => {});
      return;
    }
    report.blocked_requests.push({ flow: currentFlow, url, from: page.url(), navigation: req.isNavigationRequest(), at: new Date().toISOString() });
    req.abort("blockedbyclient").catch(() => {});
  });
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const text = m.text();
    const where = (m.location() && m.location().url) || "";
    const line = `${page.url()} :: ${text}${where ? ` [${where}]` : ""}`;
    const failedLoad = text.startsWith("Failed to load resource");
    const refusal = /status of (401|402|403|409)\b/.test(text);
    if ((expectingFailure > 0 && (failedLoad || /ERR_BLOCKED_BY_CLIENT|ERR_FAILED/.test(text))) || (inJourney > 0 && failedLoad && refusal)) {
      report.expected.push({ flow: currentFlow, line });
    } else {
      report.errors.push({ flow: currentFlow, line });
      console.log("  CONSOLE ERROR:", line.slice(0, 300));
    }
  });
  page.on("pageerror", (e) => {
    report.errors.push({ flow: currentFlow, line: `${page.url()} :: pageerror ${e.message}` });
    console.log("  PAGE ERROR:", page.url(), e.message.slice(0, 200));
  });
}

async function newPage(browserOrContext, width = 1280) {
  const page = await browserOrContext.newPage();
  await page.setViewport({ width, height: 900 });
  return instrument(page);
}

async function newBrowser() {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: CHROME_ARGS });
  // A page opened by the app itself (a target=_blank link) is instrumented too.
  browser.on("targetcreated", async (t) => {
    if (t.type() !== "page") return;
    const p = await t.page().catch(() => null);
    if (p) await instrument(p).catch(() => {});
  });
  return browser;
}

async function withExpectedFailures(fn) {
  expectingFailure++;
  try {
    return await fn();
  } finally {
    await sleep(300);
    expectingFailure--;
  }
}

let lastPage = null;
async function goto(page, url, { wait = "networkidle0", timeout = 30000 } = {}) {
  lastPage = page;
  const target = url.startsWith("http") ? url : `${BASE}${url}`;
  try {
    return await page.goto(target, { waitUntil: wait, timeout });
  } catch (err) {
    if (/timeout/i.test(err.message)) {
      report.notes.push(`network not idle within ${timeout} ms (continued): ${target}`);
      return null;
    }
    throw err;
  }
}

async function shot(page, name) {
  const file = `${SHOTS}/${RUN}-${name.replace(/[^a-z0-9]+/gi, "_").slice(0, 80)}.png`;
  await page.screenshot({ path: file, fullPage: true }).catch(() => {});
  return file;
}

async function signIn(page, email, password = PASSWORD) {
  await goto(page, "/login");
  await page.waitForSelector('input[type="email"]', { timeout: 20000 });
  await page.type('input[type="email"]', email);
  await page.type('input[type="password"]', password);
  await Promise.all([page.click('button[type="submit"]'), page.waitForNetworkIdle({ idleTime: 800, timeout: 20000 }).catch(() => {})]);
  await page
    .waitForFunction(() => !location.pathname.startsWith("/login") && location.pathname !== "/dashboard" && !/^\/o\/[^/]+\/?$/.test(location.pathname), { timeout: 25000 })
    .catch(() => {});
  await page.waitForNetworkIdle({ idleTime: 500, timeout: 15000 }).catch(() => {});
  const session = await page.evaluate(() => fetch("/api/v1/auth/session").then((r) => r.json()));
  if (!session.authenticated) {
    const text = await bodyText(page).catch(() => "");
    throw new Error(`sign-in failed for ${email}: ${text.slice(0, 200).replace(/\n+/g, " | ")}`);
  }
  return page;
}

async function login(browser, email, password = PASSWORD) {
  const context = await browser.createBrowserContext();
  const page = await newPage(context);
  await signIn(page, email, password);
  return { context, page };
}

const audited = new Set();
async function audit(page, label) {
  audited.add(label);
  const cspHere = await page.evaluate(() => (window.__csp || []).length).catch(() => 0);
  const axe = await page.evaluate(async (src) => {
    (0, eval)(src); // Runtime.evaluate is not subject to the page CSP
    const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
    return r.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length, sample: v.nodes[0] && v.nodes[0].html.slice(0, 160), target: v.nodes[0] && String(v.nodes[0].target) }));
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
  const h1 = await page.evaluate(() => [...document.querySelectorAll("h1")].map((h) => h.innerText.trim()));
  const url = page.url().replace(/#.*$/, "#<fragment>");
  report.pages.push({ label, url, axe, tabbed, noFocus: [...new Set(noFocus)], h1, csp_on_page: cspHere });
  if (axe.length) console.log(`  AXE ${label}:`, axe.map((v) => `${v.id}x${v.nodes}`).join(", "));
}

async function visit(page, url, label, { auditIt = true } = {}) {
  console.log("visit", label);
  await goto(page, url);
  await page.waitForSelector("main", { timeout: 15000 }).catch(() => {});
  await sleep(500);
  if (auditIt) await audit(page, label);
}

// ----------------------------------------------------------- page interactions --

async function bodyText(page) {
  return page.evaluate(() => (document.body ? document.body.innerText : ""));
}

async function waitForText(page, text, timeout = 20000) {
  await page.waitForFunction((t) => document.body && document.body.innerText.includes(t), { timeout }, text).catch(async () => {
    const shown = (await bodyText(page).catch(() => "")).slice(0, 500).replace(/\n+/g, " | ");
    throw new Error(`text not found: "${text}" on ${page.url()} :: ${shown}`);
  });
}

async function textVisible(page, text) {
  return page.evaluate((t) => document.body.innerText.includes(t), text);
}

// Click the first visible button/link/label whose text or aria-label contains (or equals) ``text``.
async function clickText(page, text, { exact = false, within = "body", timeout = 10000 } = {}) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const clicked = await page.evaluate(
      (text, exact, within) => {
        const root = document.querySelector(within) || document.body;
        const candidates = [...root.querySelectorAll("button, a, [role=tab], [role=radio], summary, label")];
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
  const shown = (await bodyText(page)).slice(0, 400).replace(/\n+/g, " | ");
  throw new Error(`nothing to click for "${text}" on ${page.url()} :: ${shown}`);
}

async function clickSelector(page, selector, timeout = 10000) {
  await page.waitForSelector(selector, { visible: true, timeout });
  await page.evaluate((s) => {
    const el = document.querySelector(s);
    el.scrollIntoView({ block: "center" });
    el.click();
  }, selector);
}

async function fieldId(page, labelText) {
  return page.evaluate((text) => {
    const label = [...document.querySelectorAll("label")].find((l) => (l.innerText || "").replace(/\s+/g, " ").trim().startsWith(text));
    if (!label) return null;
    if (label.htmlFor) return label.htmlFor;
    const inner = label.querySelector("input, select, textarea");
    return inner ? inner.id || null : null;
  }, labelText);
}

async function fill(page, labelText, value) {
  let id = null;
  for (let i = 0; i < 40 && !id; i++) {
    id = await fieldId(page, labelText);
    if (!id) await sleep(250);
  }
  if (!id) throw new Error(`no field labelled "${labelText}" on ${page.url()}`);
  const el = await page.$(`[id="${id}"]`);
  await el.click({ clickCount: 3 });
  await page.keyboard.press("Backspace");
  await el.type(value);
}

async function choose(page, labelText, value) {
  let id = null;
  for (let i = 0; i < 40 && !id; i++) {
    id = await fieldId(page, labelText);
    if (!id) await sleep(250);
  }
  if (!id) throw new Error(`no select labelled "${labelText}" on ${page.url()}`);
  await page.select(`[id="${id}"]`, value);
}

// The AppCard (My products / dashboard) for a product, by its display name.
async function appCard(page, name) {
  return page.evaluate((name) => {
    const art = [...document.querySelectorAll("article")].find((a) => {
      const h = a.querySelector("h2");
      return h && h.innerText.trim() === name;
    });
    if (!art) return null;
    return {
      badges: [...art.querySelectorAll('ul[aria-label="Status"] li')].map((l) => l.innerText.trim()),
      text: art.innerText.replace(/\s+/g, " ").trim(),
      buttons: [...art.querySelectorAll("button, a")].map((b) => b.innerText.trim()),
    };
  }, name);
}

async function exploreCards(page) {
  return page.evaluate(() => {
    const sec = document.querySelector('section[aria-labelledby="explore-products-title"]');
    if (!sec) return [];
    return [...sec.querySelectorAll("article")].map((a) => ({
      name: (a.querySelector("h3") || {}).innerText,
      text: a.innerText.replace(/\s+/g, " ").trim(),
      actions: [...a.querySelectorAll("button, a")].map((b) => b.getAttribute("aria-label") || b.innerText.trim()),
    }));
  });
}

// ------------------------------------------------------------------ TOTP / API --

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

// The server refuses a code reused inside its 30-second window: wait for the next.
const lastWindow = {};
async function freshCode(secret) {
  const window = () => Math.floor(Date.now() / 30000);
  while (lastWindow[secret] === window()) await sleep(1000);
  lastWindow[secret] = window();
  return totp(secret);
}

// A request the page itself makes, with its session and (unless told not to) its CSRF token.
async function api(page, method, url, body, { csrf = true, idempotency = true } = {}) {
  return page.evaluate(
    async ({ method, url, body, csrf, idempotency }) => {
      const token = document.cookie.split("; ").find((c) => c.startsWith("w360_csrf="));
      const headers = { "content-type": "application/json", accept: "application/json" };
      if (csrf) headers["x-csrftoken"] = token ? token.split("=")[1] : "";
      if (idempotency) headers["idempotency-key"] = Math.random().toString(16).slice(2) + Date.now().toString(16);
      const r = await fetch(url, { method, headers, credentials: "same-origin", body: body === undefined ? undefined : JSON.stringify(body) });
      let json = null;
      const text = await r.text();
      try {
        json = JSON.parse(text);
      } catch {
        // Not JSON (e.g. Django's own 403 page): keep what identifies it.
        const doc = new DOMParser().parseFromString(text, "text/html");
        json = {
          non_json: true,
          content_type: r.headers.get("content-type"),
          title: doc.title,
          h1: (doc.querySelector("h1") || {}).textContent,
          first_paragraph: ((doc.querySelector("p") || {}).textContent || "").trim().slice(0, 200),
        };
      }
      return { status: r.status, body: json };
    },
    { method, url, body, csrf, idempotency },
  );
}

async function answerPrompt(page, secret, wait = 3500) {
  const selector = 'input[autocomplete="one-time-code"]';
  const shown = await page.waitForSelector(selector, { timeout: wait, visible: true }).then(() => true).catch(() => false);
  if (!shown) return false;
  await page.type(selector, await freshCode(secret));
  await clickText(page, "Verify", { exact: true });
  await page.waitForFunction((s) => !document.querySelector(s), { timeout: 20000 }, selector).catch(() => {});
  return true;
}

async function enrollTotp(page) {
  const started = await api(page, "POST", "/api/v1/auth/mfa/totp/enroll", {});
  if (started.status !== 200) throw new Error(`enrolment refused: ${started.status} ${JSON.stringify(started.body)}`);
  const secret = started.body.secret;
  const confirmed = await api(page, "POST", "/api/v1/auth/mfa/totp/confirm", { factor_id: started.body.factor_id, code: await freshCode(secret) });
  if (confirmed.status !== 200) throw new Error(`enrolment not confirmed: ${confirmed.status} ${JSON.stringify(confirmed.body)}`);
  return secret;
}

// --------------------------------------------------------------- email (Mailpit) --

const seenMail = new Set();
async function mailFor(email) {
  const r = await fetch(`${MAILPIT}/api/v1/search?query=${encodeURIComponent(`to:${email}`)}&limit=50`);
  const j = await r.json();
  return (j.messages || []).sort((a, b) => new Date(b.Created) - new Date(a.Created));
}

// The newest not-yet-used email to ``email`` carrying a ``kind`` link (e.g. "verify-email").
async function waitForMail(email, kind, timeout = 90000) {
  const re = new RegExp(`https://([a-z0-9.-]+)/${kind.replace(/\//g, "\\/")}#token=([A-Za-z0-9_.\\-]+)`);
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    for (const m of await mailFor(email)) {
      if (seenMail.has(m.ID)) continue;
      const full = await (await fetch(`${MAILPIT}/api/v1/message/${m.ID}`)).json();
      const text = `${full.Text || ""}\n${full.HTML || ""}`;
      const match = text.match(re);
      if (match) {
        seenMail.add(m.ID);
        return { host: match[1], token: match[2], link: match[0], subject: full.Subject, id: m.ID, text: full.Text || "" };
      }
    }
    await sleep(1500);
  }
  throw new Error(`no ${kind} email reached ${email} within ${timeout / 1000}s`);
}

// ------------------------------------------------------------------------ DNS --

// "Publishes" a TXT record in the CoreDNS zone the platform really queries, and
// bumps the SOA serial so the file plugin reloads it (every 2 s).
function publishTxt(recordName, value) {
  const fqdn = recordName.replace(/\.$/, "");
  const label = fqdn.replace(/\.wl-brand\.com$/, "");
  const zone = fs.readFileSync(ZONE_FILE, "utf8");
  const lines = zone.split(/\r?\n/).filter((l) => l.trim() !== "" && !l.startsWith(`${label} `) && !l.startsWith(`${label}\t`));
  lines.push(`${label} IN TXT "${value}"`);
  let text = lines.join("\n") + "\n";
  let serial = null;
  text = text.replace(/(\(\s*)(\d{10})/, (_, open, s) => {
    serial = String(Number(s) + 1);
    return `${open}${serial}`;
  });
  fs.writeFileSync(ZONE_FILE, text);
  report.notes.push(`DNS: published ${fqdn} TXT "${value}" (zone serial ${serial})`);
  return serial;
}

// -------------------------------------------------------- raw HTTPS (evidence) --

// One request through the edge with the right SNI, verified against the staging
// root CA - so a status, a Location header or a refused handshake is recorded
// exactly as the server produced it.
function rawGet(host, pathName) {
  return new Promise((resolve) => {
    const req = https.request(
      {
        host: "127.0.0.1",
        port: EDGE_PORT,
        servername: host,
        path: pathName,
        method: "GET",
        headers: { Host: host, Accept: "text/html" },
        ca: CA,
        checkServerIdentity: (_h, cert) => require("tls").checkServerIdentity(host, cert),
        timeout: 15000,
      },
      (res) => {
        res.resume();
        resolve({ status: res.statusCode, location: res.headers.location || null });
      },
    );
    req.on("error", (e) => resolve({ error: `${e.code || ""} ${e.message}`.trim() }));
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.end();
  });
}

// ------------------------------------------------------------------- flow record --

async function flow(name, fn) {
  console.log("FLOW", name);
  currentFlow = name;
  inJourney++;
  const cspBefore = report.csp.length;
  const t0 = Date.now();
  try {
    const detail = (await fn()) || {};
    const ok = detail.ok !== false;
    const entry = { flow: name, ok, seconds: Math.round((Date.now() - t0) / 1000), csp_during: report.csp.length - cspBefore, ...detail };
    report.flows.push(entry);
    if (!ok) console.log("  FAILED", JSON.stringify(detail).slice(0, 800));
    return entry;
  } catch (err) {
    console.log("  ERROR", err.message.slice(0, 800));
    const screenshot = lastPage ? await shot(lastPage, `fail-${name.split(" ")[0]}`) : null;
    const entry = { flow: name, ok: false, seconds: Math.round((Date.now() - t0) / 1000), csp_during: report.csp.length - cspBefore, error: err.message.slice(0, 900), screenshot, page: lastPage ? lastPage.url() : null };
    report.flows.push(entry);
    return entry;
  } finally {
    inJourney--;
    currentFlow = "(between flows)";
  }
}

// A real 32x32 PNG (a solid square), written without a library.
function makePng(file, [r, g, b]) {
  const width = 32;
  const height = 32;
  const raw = Buffer.alloc((width * 3 + 1) * height);
  for (let y = 0; y < height; y++) {
    const row = y * (width * 3 + 1);
    raw[row] = 0;
    for (let x = 0; x < width; x++) {
      raw[row + 1 + x * 3] = r;
      raw[row + 2 + x * 3] = g;
      raw[row + 3 + x * 3] = b;
    }
  }
  const crcTable = Array.from({ length: 256 }, (_, n) => {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    return c >>> 0;
  });
  const crc = (buf) => {
    let c = 0xffffffff;
    for (const byte of buf) c = crcTable[(c ^ byte) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  };
  const chunk = (type, data) => {
    const len = Buffer.alloc(4);
    len.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type), data]);
    const sum = Buffer.alloc(4);
    sum.writeUInt32BE(crc(body));
    return Buffer.concat([len, body, sum]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 2;
  fs.writeFileSync(file, Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", ihdr), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]));
}

// =============================================================== shared steps ==

async function orgOf(page) {
  const orgs = await api(page, "GET", "/api/v1/organizations");
  return (orgs.body && orgs.body.organizations) || [];
}

async function appsOf(page, slug) {
  const r = await api(page, "GET", `/api/v1/orgs/${slug}/apps`);
  return (r.body && r.body.apps) || [];
}

async function waitEntitlement(page, slug, code, wanted, timeout = 90000) {
  const deadline = Date.now() + timeout;
  let app = null;
  while (Date.now() < deadline) {
    app = (await appsOf(page, slug)).find((a) => a.product_code === code);
    if (app && wanted.includes(app.entitlement_status)) return app;
    await sleep(2000);
  }
  return app;
}

// Clicks "Open" on a product's card; the app fetches its launch decision and
// then navigates the top-level page there. That navigation is intercepted and
// aborted (the real product is never loaded); its URL is the evidence.
async function launchFromCard(page, slug, product, orgId, label) {
  await goto(page, `/o/${slug}/apps`);
  await waitForText(page, product.name);
  const before = report.blocked_requests.length;
  const cspBefore = report.csp.length;
  const result = await withExpectedFailures(async () => {
    const clicked = await page.evaluate((name) => {
      const art = [...document.querySelectorAll("article")].find((a) => (a.querySelector("h2") || {}).innerText === name);
      if (!art) return "no card";
      const btn = [...art.querySelectorAll("button")].find((b) => b.innerText.trim() === "Open");
      if (!btn) return `no Open button (buttons: ${[...art.querySelectorAll("button,a")].map((b) => b.innerText.trim()).join("/")})`;
      btn.click();
      return "clicked";
    }, product.name);
    if (clicked !== "clicked") return { clicked };
    const deadline = Date.now() + 15000;
    let nav = null;
    let alert = null;
    while (Date.now() < deadline) {
      nav = report.blocked_requests.slice(before).find((b) => b.navigation);
      if (nav) break;
      alert = await page
        .evaluate((name) => {
          const art = [...document.querySelectorAll("article")].find((a) => (a.querySelector("h2") || {}).innerText === name);
          const a = art && art.querySelector('[role="alert"]');
          return a ? a.innerText.trim() : null;
        }, product.name)
        .catch(() => null);
      if (alert) break;
      await sleep(250);
    }
    await sleep(800);
    return { clicked, nav, alert };
  });
  const target = result.nav ? result.nav.url : null;
  let organization = null;
  try {
    organization = target ? new URL(target).searchParams.get("organization") : null;
  } catch {
    organization = null;
  }
  const entry = {
    label,
    product: product.key,
    clicked: result.clicked,
    target,
    refused_with: result.alert,
    starts_with_registered_launch_url: !!target && target.startsWith(product.launchUrl),
    organization_param: organization,
    organization_matches: !!organization && organization === orgId,
    csp_violations_during_launch: report.csp.length - cspBefore,
    page_after: page.url(),
  };
  report.launches.push(entry);
  entry.ok = entry.starts_with_registered_launch_url && entry.organization_matches && entry.csp_violations_during_launch === 0;
  return entry;
}

// Checkout for whatever the page's URL names: billing details first when there
// is no billing account, the server's quote, then the TEST provider's hosted page.
async function checkoutAndPay(page, { auditLabel = null, keyboard = false } = {}) {
  await page.waitForFunction(() => location.pathname.endsWith("/checkout"), { timeout: 20000 });
  await page.waitForFunction(() => /Billing details|Order summary|Nothing was chosen|Only the organization owner/.test(document.body.innerText), { timeout: 25000 });
  const kb = {};
  if (await textVisible(page, "Billing details")) {
    if (auditLabel) await audit(page, `${auditLabel} (billing details first)`);
    if (keyboard) {
      await page.evaluate(() => document.activeElement && document.activeElement.blur());
      kb.tabs_to_currency = await tabUntil(page, focusedLabelStarts, "Currency");
      await page.keyboard.type("GBP");
      let value = await page.evaluate(() => document.activeElement && document.activeElement.value);
      for (let i = 0; i < 12 && value !== "GBP"; i++) {
        await page.keyboard.press("ArrowDown");
        value = await page.evaluate(() => document.activeElement && document.activeElement.value);
      }
      kb.currency_chosen_by_keyboard = value;
      kb.tabs_to_open_account = await tabUntil(page, focusedButtonIs, "Open billing account");
      await page.keyboard.press("Enter");
    } else {
      await choose(page, "Currency", "GBP");
      await clickText(page, "Open billing account", { exact: true });
    }
    await waitForText(page, "Order summary", 25000);
  }
  await waitForText(page, "Order summary", 25000);
  await page.waitForFunction(() => [...document.querySelectorAll("button")].some((b) => /Place order|Start free trial/.test(b.innerText) && !b.disabled), { timeout: 20000 });
  const quote = await page.evaluate(() => {
    const dl = document.querySelector("main dl");
    return dl ? dl.innerText.replace(/\n+/g, " | ") : document.querySelector("main").innerText.slice(0, 300);
  });
  const action = await page.evaluate(() => ([...document.querySelectorAll("button")].find((b) => /Place order|Start free trial/.test(b.innerText)) || {}).innerText);
  if (auditLabel) await audit(page, `${auditLabel} (server quote)`);
  if (keyboard) {
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    kb.tabs_to_place_order = await tabUntil(page, focusedButtonIs, action);
    await page.keyboard.press("Enter");
  } else {
    await clickText(page, action, { exact: true });
  }
  if (action !== "Place order and pay") return { quote, action, paid: false, kb };
  await page.waitForFunction(() => location.pathname.startsWith("/billing/test-checkout"), { timeout: 45000 });
  await waitForText(page, "Pay (succeed)");
  const hostedUrl = page.url().replace(/#.*/, "#<reference>");
  if (auditLabel) await audit(page, "billing/test-checkout (TEST provider hosted page)");
  if (keyboard) {
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    kb.tabs_to_pay = await tabUntil(page, focusedButtonIs, "Pay (succeed)");
    await page.keyboard.press("Enter");
  } else {
    await clickText(page, "Pay (succeed)", { exact: true });
  }
  await waitForText(page, "Test payment succeeded", 20000);
  return { quote, action, paid: true, hosted_page: hostedUrl, kb };
}

// Settings -> Product access: the owner assigns themselves one of the plan's seats
// (every product is per-seat; holding the product does not give anyone a seat).
async function assignOwnSeat(page, slug, product, email, { viaCardLink = false } = {}) {
  let via = "went to Settings -> Product access directly";
  const selector = `[aria-label="Assign a seat: ${product.name}"]`;
  if (viaCardLink) {
    await goto(page, `/o/${slug}/apps`);
    await waitForText(page, product.name);
    const href = await page.$eval(selector, (a) => a.getAttribute("href")).catch(() => null);
    if (href) {
      await clickSelector(page, selector);
      await page.waitForFunction(() => location.pathname.endsWith("/settings/products"), { timeout: 20000 });
      via = `card link "Assign a seat: ${product.name}" -> ${href}`;
    } else {
      via = "the card had no 'Assign a seat' link; went to Settings -> Product access directly";
      await goto(page, `/o/${slug}/settings/products`);
    }
  } else {
    await goto(page, `/o/${slug}/settings/products`);
  }
  await waitForText(page, "Product access");
  await page.waitForFunction(() => !document.body.innerText.includes("Loading"), { timeout: 20000 }).catch(() => {});
  const hasSwitch = await page.evaluate((name) => {
    const b = [...document.querySelectorAll('[role="group"][aria-label="Product"] button')].find((x) => x.innerText.trim() === name);
    if (b) b.click();
    return !!b;
  }, product.name);
  if (hasSwitch) await sleep(800);
  await page.waitForFunction((e) => [...document.querySelectorAll("tr")].some((tr) => tr.innerText.includes(e)), { timeout: 20000 }, email);
  const before = await page.evaluate((e) => {
    const tr = [...document.querySelectorAll("tr")].find((r) => r.innerText.includes(e));
    return tr.querySelector("button").innerText.trim();
  }, email);
  if (!before.includes("Assigned")) {
    await page.evaluate((e) => [...document.querySelectorAll("tr")].find((r) => r.innerText.includes(e)).querySelector("button").click(), email);
    await page.waitForFunction((e) => {
      const tr = [...document.querySelectorAll("tr")].find((r) => r.innerText.includes(e));
      return tr && tr.querySelector("button").innerText.includes("Assigned");
    }, { timeout: 20000 }, email);
  }
  const seats = await page.evaluate(() => (document.body.innerText.match(/Seats:\s*(\d+)\s*\/\s*(\d+)/) || [])[0]);
  const app = (await appsOf(page, slug)).find((a) => a.product_code === product.code) || {};
  return { via, before, seats, state_after: app.state, can_launch: app.can_launch };
}

// Explore -> "Product packages": the Team plan card of one product (its caption
// is the product name); clicks its action and returns the action's label.
async function clickPackageAction(page, product) {
  await waitForText(page, "Product packages");
  return page.evaluate((name) => {
    const sec = [...document.querySelectorAll("section")].find((s) => (s.querySelector("h2") || {}).innerText === "Product packages");
    if (!sec) return "no Product packages section";
    const art = [...sec.querySelectorAll("article")].find((a) => [...a.querySelectorAll("p")].some((x) => x.innerText.trim() === name));
    if (!art) return `no package for ${name}`;
    const btn = [...art.querySelectorAll("button")].find((b) => /^(Buy|Start free trial)/.test(b.innerText.trim()));
    if (!btn) return "no action";
    btn.scrollIntoView({ block: "center" });
    btn.click();
    return btn.getAttribute("aria-label");
  }, product.name);
}

// Staff: find the organization in Customers (search), open it, suspend or reactivate it.
async function staffOrgAction(staff, orgName, action) {
  const page = staff.page;
  await goto(page, "/admin/customers");
  await fill(page, "Search by name or slug", orgName);
  await clickText(page, "Search", { exact: true });
  await page.waitForFunction((n) => [...document.querySelectorAll("table a")].some((a) => a.innerText.trim() === n), { timeout: 20000 }, orgName);
  const row = await page.evaluate((n) => {
    const a = [...document.querySelectorAll("table a")].find((x) => x.innerText.trim() === n);
    const tr = a.closest("tr");
    return tr ? tr.innerText.replace(/\s+/g, " ") : "";
  }, orgName);
  await Promise.all([
    page.waitForFunction(() => /\/admin\/customers\/[0-9a-f-]{36}/.test(location.pathname), { timeout: 20000 }),
    page.evaluate((n) => [...document.querySelectorAll("table a")].find((x) => x.innerText.trim() === n).click(), orgName),
  ]);
  await waitForText(page, orgName);
  await clickText(page, action, { exact: true });
  await fill(page, "Reason for", `${action} - Phase 12 browser check ${RUN}`);
  await clickText(page, "Confirm", { exact: true });
  const prompted = await answerPrompt(page, staff.secret, 5000);
  const done = action === "Suspend organization" ? "Organization suspended." : "Organization reactivated.";
  await waitForText(page, done, 25000);
  await sleep(800);
  const heading = await page.evaluate(() => (document.querySelector("h1") || {}).innerText);
  return { prompted_for_second_factor: prompted, list_row: row, heading };
}

// ================================================================ the journeys ==

const ctx = { customers: {} };

// ---- public pages and the staff product console (SSO / launch URL facts) ------
async function journeyPublicAndStaffFacts(browser, staff) {
  await flow("0. public site (signed out): home, /products, each product page, /pricing, sign-in and self-service pages", async () => {
    const context = await browser.createBrowserContext();
    const page = await newPage(context);
    ctx.anon = { context, page };
    const pages = [["/", "public /"], ["/products", "public /products"], ...PRODUCTS.map((p) => [`/products/${p.slug}`, `public /products/${p.slug}`]), ["/pricing", "public /pricing"], ["/login", "public /login"], ["/signup", "public /signup"], ["/verify-email", "public /verify-email (no link)"], ["/forgot-password", "public /forgot-password"]];
    for (const [url, label] of pages) await visit(page, url, label);
    await goto(page, "/");
    const home = await bodyText(page);
    const claim = home.includes("Every product signs you in with the same Workeasy360 account.");
    if (claim) {
      bug("Copy: the public home page promises single sign-on into every product, which none of the four products can do today", {
        url: `${BASE}/ ("How it works", step 4)`,
        text: "Every product signs you in with the same Workeasy360 account.",
        fact: "every product's SSO is integration_required: no OIDC client is registered (Super Admin -> Products shows 'Single sign-on: Integration required')",
        severity: "truthfulness of copy (the customer's product cards themselves make no SSO claim)",
      });
      report.notes.push('Public home page ("How it works", step 4) states: "Every product signs you in with the same Workeasy360 account." - while every product\'s SSO is integration_required (no OIDC client).');
    }
    const howItWorks = await page.evaluate(() => {
      const sec = document.querySelector('section[aria-labelledby="how-title"]');
      return sec ? sec.innerText.split(String.fromCharCode(10)).filter(Boolean).join(" | ") : null;
    });
    return { ok: !claim, home_claims_single_sign_on: claim, how_it_works: howItWorks, new_copy_present: home.includes("Every product your organization holds opens from the same dashboard.") };
  });

  await flow("0b. Super Admin product console: each product's registered launch URL and single sign-on status", async () => {
    const page = staff.page;
    await visit(page, "/admin/products", "admin /admin/products");
    const facts = await page.evaluate(() =>
      [...document.querySelectorAll('section[aria-labelledby^="product-"]')].map((s) => {
        const dd = (name) => {
          const dt = [...s.querySelectorAll("dt")].find((d) => d.innerText.trim() === name);
          return dt && dt.nextElementSibling ? dt.nextElementSibling.innerText.trim() : null;
        };
        return { name: (s.querySelector("h2") || {}).innerText, sso: dd("Single sign-on"), launch_url: dd("Launch URL"), integration: dd("Integration status") };
      }),
    );
    ctx.staffProductFacts = facts;
    const rows = PRODUCTS.map((p) => {
      const f = facts.find((x) => x.name === p.name) || {};
      return { product: p.key, registry_launch_url: p.launchUrl, console_launch_url: f.launch_url, same: f.launch_url === p.launchUrl, sso: f.sso };
    });
    return { ok: rows.every((r) => r.same && r.sso === "Integration required"), rows };
  });
}

// ---- Step 8: one independent customer per product -------------------------------
async function productJourney(browser, staff, p, first) {
  const tag = p.key.toLowerCase();
  const email = `p12-${RUN}-${tag}@browser.test`;
  const company = `P12 ${p.key} Customer ${RUN}`;
  const context = await browser.createBrowserContext();
  const page = await newPage(context);
  const st = { email, company, context, page, product: p, steps: {} };
  ctx.customers[p.key] = st;
  const pre = `8.${p.key}`;
  const need = (...names) => names.every((n) => st.steps[n]);

  const catalog = await flow(`${pre}.1 catalog: ${p.name} on the public store /products and its page /products/${p.slug}`, async () => {
    await goto(page, "/products");
    await waitForText(page, p.name);
    const card = await page.evaluate((name) => {
      const art = [...document.querySelectorAll("article")].find((a) => (a.querySelector("h2") || {}).innerText === name);
      return art ? { text: art.innerText.replace(/\s+/g, " "), actions: [...art.querySelectorAll("a,button")].map((b) => b.getAttribute("aria-label") || b.innerText) } : null;
    }, p.name);
    // What the store card's own trial button remembers (an observation; the journey uses the product page's plan card).
    let storeIntent = null;
    if (card && card.actions.includes(`Start free trial: ${p.name}`)) {
      await clickSelector(page, `[aria-label="Start free trial: ${p.name}"]`);
      await page.waitForFunction(() => location.pathname === "/signup", { timeout: 15000 });
      storeIntent = await page.evaluate(() => localStorage.getItem("w360.purchase-intent"));
      await goto(page, "/products");
      await waitForText(page, p.name);
    }
    await clickSelector(page, `[aria-label="Learn more about ${p.name}"]`);
    await page.waitForFunction((slug) => location.pathname === `/products/${slug}`, { timeout: 15000 }, p.slug);
    await waitForText(page, "Plans");
    await page.waitForNetworkIdle({ idleTime: 500, timeout: 10000 }).catch(() => {});
    const detail = await bodyText(page);
    const h1 = await page.evaluate(() => (document.querySelector("h1") || {}).innerText);
    st.storeIntent = storeIntent;
    const ok = !!card && card.text.includes("Free trial available") && h1 === p.name && detail.includes("Team") && detail.includes("Free trial available");
    return { ok, store_card: card, store_card_trial_intent: storeIntent, detail_h1: h1, detail_has_team_plan: detail.includes("Team") };
  });
  mark(p.key, "Catalog", catalog.ok ? "PASS" : "FAIL", catalog.ok ? `listed on /products (${(catalog.store_card || {}).text?.slice(0, 120)}); /products/${p.slug} h1 "${catalog.detail_h1}" with the Team plan` : catalog.error || JSON.stringify(catalog).slice(0, 300));
  st.steps.catalog = catalog.ok;

  const signup = await flow(`${pre}.2 start the free trial on the product page -> sign up -> verify the email (Mailpit link) -> organization exists`, async () => {
    if (!(await page.evaluate(() => location.pathname)).startsWith("/products/")) await goto(page, `/products/${p.slug}`);
    await clickSelector(page, '[aria-label="Start free trial: Team"]');
    await page.waitForFunction(() => location.pathname === "/signup", { timeout: 15000 });
    const intent = await page.evaluate(() => localStorage.getItem("w360.purchase-intent"));
    await page.waitForSelector("::-p-aria(First name)");
    await fill(page, "First name", `P12${p.key}`);
    await fill(page, "Last name", "Customer");
    await fill(page, "Work email", email);
    await fill(page, "Password", PASSWORD);
    await fill(page, "Company name (optional)", company);
    await clickText(page, "Create account", { exact: true });
    await waitForText(page, "Check your email", 20000);
    if (first) await audit(page, "signup: check your email");
    const mail = await waitForMail(email, "verify-email");
    await goto(page, `${BASE}/verify-email#token=${mail.token}`);
    await waitForText(page, "Confirm and sign in");
    const fragmentGone = !(await page.evaluate(() => location.hash));
    if (first) await audit(page, "verify-email (link opened)");
    await page.type('input[type="password"]', PASSWORD);
    await clickText(page, "Confirm and sign in", { exact: true });
    await page.waitForFunction(() => /^\/o\/[^/]+\/(apps|dashboard|checkout)/.test(location.pathname), { timeout: 40000 });
    await page.waitForNetworkIdle({ idleTime: 600, timeout: 15000 }).catch(() => {});
    const landed = page.url().replace(BASE, "");
    const orgs = await orgOf(page);
    const org = orgs.find((o) => o.display_name === company);
    if (org) {
      st.slug = org.slug;
      st.orgId = org.organization_id;
    }
    return {
      ok: !!org && org.is_owner === true && landed.includes(`trial_started=${p.code}`),
      intent_stored_by_plan_card: intent,
      email_subject: mail.subject,
      email_link_host: mail.host,
      link_fragment_removed: fragmentGone,
      landed,
      organization: org,
    };
  });
  st.steps.signup = !!st.slug;

  const trial = await flow(`${pre}.3 the trial entitlement shows (My products badge "Trial") and the dashboard lists it`, async () => {
    if (!need("signup")) return { ok: false, blocked: "no organization" };
    const app = await waitEntitlement(page, st.slug, p.code, ["trial"], 30000);
    await goto(page, `/o/${st.slug}/apps`);
    await waitForText(page, p.name);
    const card = await appCard(page, p.name);
    const seatLink = await page.$eval(`[aria-label="Assign a seat: ${p.name}"]`, (a) => a.getAttribute("href")).catch(() => null);
    if (first) await audit(page, "org My products (trial)");
    await goto(page, `/o/${st.slug}/dashboard`);
    await waitForText(page, p.name);
    const dash = await appCard(page, p.name);
    if (first) await audit(page, "org dashboard (trial)");
    const noSeat = !!card && card.badges.includes("No seat");
    const newHint = !!card && card.text.includes("you don't hold a seat yet. Assign one - to yourself or a teammate - in Product access.");
    const oldHint = !!card && (card.text.includes("Ask an administrator") || card.buttons.includes("Ask an admin"));
    if (noSeat && (oldHint || !seatLink) && first) {
      bug("The owner who starts a trial (or buys) holds no seat, and the product card does not lead the owner to Product access", {
        url: `${BASE}/o/<slug>/apps`,
        card: card.text,
        assign_seat_link: seatLink,
        api: app && { state: app.state, entitlement_status: app.entitlement_status, can_launch: app.can_launch, seats: app.seats },
        screenshot: await shot(page, `${p.key}-trial-no-seat`),
        severity: "UX",
      });
    }
    return {
      ok: !!app && app.entitlement_status === "trial" && ["active", "no_seat"].includes(app.state) && !!card && card.badges.includes("Trial") && !!dash && dash.badges.includes("Trial"),
      api: app && { state: app.state, entitlement_status: app.entitlement_status, trial: app.trial, sso: app.sso, can_launch: app.can_launch, plan: app.plan, seats: app.seats },
      my_products_card: card,
      dashboard_card: dash && dash.badges,
      owner_has_no_seat_yet: noSeat,
      seat_hint_points_to_product_access: newHint,
      old_ask_an_administrator_copy: oldHint,
      assign_seat_link: seatLink,
    };
  });
  mark(p.key, "Trial", trial.ok ? "PASS" : signup.ok ? "FAIL" : "BLOCKED", trial.ok ? `product trial started from the product page; /apps entitlement_status=trial, ${JSON.stringify((trial.api || {}).trial)}; My products badges ${JSON.stringify((trial.my_products_card || {}).badges)}` : trial.error || JSON.stringify(trial).slice(0, 300));
  st.steps.trial = trial.ok;

  await flow(`${pre}.3b the owner follows the card's "Assign a seat: ${p.name}" link to Product access and assigns themselves a seat`, async () => {
    if (!need("signup")) return { ok: false, blocked: "no organization" };
    const r = await assignOwnSeat(page, st.slug, p, email, { viaCardLink: true });
    if (!/^card link/.test(r.via)) return { ok: false, ...r };
    if (first) await audit(page, "org settings: Product access (seat assigned)");
    return { ok: r.state_after === "active" && r.can_launch === true, ...r };
  });

  const launch1 = await flow(`${pre}.4 My products -> Open: the navigation target is the registered launch URL with organization=<org uuid> (aborted, never loaded)`, async () => {
    if (!need("signup")) return { ok: false, blocked: "no organization" };
    const l = await launchFromCard(page, st.slug, p, st.orgId, `${p.key} trial launch`);
    return { ok: l.ok, launch: l };
  });
  st.steps.launch = launch1.ok;

  const sso = await flow(`${pre}.5 SSO: what the platform and the UI say (no OIDC client registered)`, async () => {
    if (!need("signup")) return { ok: false, blocked: "no organization" };
    await goto(page, `/o/${st.slug}/apps`);
    const app = (await appsOf(page, st.slug)).find((a) => a.product_code === p.code) || {};
    const card = await appCard(page, p.name);
    const claims = card ? /single sign-on|signs you in|SSO/i.test(card.text) : false;
    const staffFact = (ctx.staffProductFacts || []).find((f) => f.name === p.name) || {};
    return { ok: app.sso === "integration_required" && !claims, api_sso: app.sso, card_mentions_sso: claims, card_text: card && card.text, staff_console_sso: staffFact.sso };
  });
  mark(p.key, "SSO", "BLOCKED", `/apps sso="${sso.api_sso}"; staff console "Single sign-on: ${sso.staff_console_sso}"; the customer's product card ${sso.card_mentions_sso ? "MENTIONS" : "does not mention"} sign-on; no OIDC client exists, so SSO cannot complete`);

  await flow(`${pre}.6 return to the dashboard`, async () => {
    if (!need("signup")) return { ok: false, blocked: "no organization" };
    await goto(page, `/o/${st.slug}/dashboard`);
    await waitForText(page, "My products");
    const h1 = await page.evaluate(() => (document.querySelector("h1") || {}).innerText);
    return { ok: /^Welcome/.test(h1 || ""), h1 };
  });

  const purchase = await flow(`${pre}.7 upgrade: Explore -> Buy -> checkout (GBP) -> TEST provider hosted page -> Active`, async () => {
    if (!need("signup")) return { ok: false, blocked: "no organization" };
    await goto(page, `/o/${st.slug}/explore`);
    await waitForText(page, p.name);
    const cards = await exploreCards(page);
    const mine = cards.find((c) => c.name === p.name);
    if (first) await audit(page, "org Explore products (one product on trial)");
    let path = `Explore product card "Buy: ${p.name}"`;
    if (mine && mine.actions.includes(`Buy: ${p.name}`)) {
      await clickSelector(page, `[aria-label="Buy: ${p.name}"]`);
    } else if (mine && mine.actions.includes(`See plans: ${p.name}`)) {
      // No billing currency yet: the card says so and points at the packages below it.
      await clickSelector(page, `[aria-label="See plans: ${p.name}"]`);
      await sleep(600);
      const anchor = await page.evaluate(() => {
        const target = document.getElementById("product-packages");
        const r = target ? target.getBoundingClientRect() : null;
        return { hash: location.hash, target_exists: !!target, target_in_view: !!r && r.top < window.innerHeight && r.bottom > 0 };
      });
      const clicked = await clickPackageAction(page, p);
      path = `Explore product card "${(mine.text.match(/Priced in your billing currency at checkout|Contact sales for pricing/) || ["(no price note)"])[0]}" -> "See plans: ${p.name}" (${JSON.stringify(anchor)}) -> Product packages "${clicked}"`;
      if (!/^Buy/.test(clicked || "") || !anchor.target_exists) return { ok: false, explore_card: mine, reason: path };
    } else {
      if (first) {
        const explore = await api(page, "GET", `/api/v1/orgs/${st.slug}/explore`);
        const row = ((explore.body || {}).products || []).find((x) => x.code === p.code) || {};
        bug(`Explore: for an organization without a billing account yet, a product on trial offers neither Buy nor "See plans"`, {
          url: `${BASE}/o/<slug>/explore`,
          explore_card: mine && mine.text,
          explore_api_row: { trial_state: row.trial_state, starting_price: row.starting_price, availability: row.availability },
          screenshot: await shot(page, `${p.key}-explore-trial-card`),
          severity: "UX (the Team plan can still be bought from the 'Product packages' section of the same page)",
        });
      }
      const clicked = await clickPackageAction(page, p);
      path = `Explore product card had no Buy / See plans (actions: ${JSON.stringify(mine && mine.actions)}); used "Product packages" -> "${clicked}"`;
      if (!/^Buy/.test(clicked || "")) return { ok: false, explore_card: mine, reason: path };
    }
    if (first && mine && mine.text.includes("Contact sales for pricing")) {
      bug(`Explore still says "Contact sales for pricing" for a priced product on trial (no billing account yet)`, { url: `${BASE}/o/<slug>/explore`, explore_card: mine.text, screenshot: await shot(page, `${p.key}-explore-contact-sales`) });
    }
    const checkoutUrl = await page.waitForFunction(() => location.pathname.endsWith("/checkout") && location.href, { timeout: 20000 }).then((h) => h.jsonValue());
    const paid = await checkoutAndPay(page, { auditLabel: first ? "org checkout" : null });
    const app = await waitEntitlement(page, st.slug, p.code, ["active"], 90000);
    await goto(page, `/o/${st.slug}/apps`);
    await waitForText(page, p.name);
    const card = await appCard(page, p.name);
    const orders = await api(page, "GET", `/api/v1/orgs/${st.slug}/orders`);
    return {
      ok: paid.paid && !!app && app.entitlement_status === "active" && !!card && card.badges.includes("Active"),
      explore_card: mine && mine.text,
      path,
      checkout: checkoutUrl.replace(BASE, ""),
      quote: paid.quote,
      hosted_page: paid.hosted_page,
      entitlement_status: app && app.entitlement_status,
      card_badges: card && card.badges,
      card_facts: card && card.text,
      orders: ((orders.body || {}).results || []).map((o) => `${o.number} ${o.status} ${o.amount_due_minor} ${o.currency}`),
    };
  });
  mark(p.key, "Purchase", purchase.ok ? "PASS" : need("signup") ? "FAIL" : "BLOCKED", purchase.ok ? `${purchase.path} -> checkout quote "${(purchase.quote || "").slice(0, 90)}" -> TEST hosted page "Pay (succeed)" -> order ${JSON.stringify(purchase.orders)}` : purchase.error || JSON.stringify(purchase).slice(0, 300));
  mark(p.key, "Entitlement", trial.ok && purchase.ok ? "PASS" : trial.ok || purchase.ok ? "FAIL" : "BLOCKED", `trial: ${trial.ok ? "entitlement_status=trial" : "not shown"}; after payment: entitlement_status=${purchase.entitlement_status}, card badges ${JSON.stringify(purchase.card_badges)}`);
  st.steps.purchase = purchase.ok;

  const dashboard = await flow(`${pre}.8 the dashboard shows the product Active`, async () => {
    if (!need("signup")) return { ok: false, blocked: "no organization" };
    await goto(page, `/o/${st.slug}/dashboard`);
    await waitForText(page, p.name);
    const card = await appCard(page, p.name);
    return { ok: !!card && card.badges.includes("Active") && card.buttons.includes("Open"), card };
  });
  mark(p.key, "Dashboard", trial.ok && dashboard.ok ? "PASS" : need("signup") ? "FAIL" : "BLOCKED", `trial badge on the dashboard: ${JSON.stringify(trial.dashboard_card)}; after payment: ${JSON.stringify((dashboard.card || {}).badges)} with Open`);

  const suspend = await flow(`${pre}.9 Super Admin suspends the organization (Customers console, step-up); the customer sees read-only and launch is refused`, async () => {
    if (!need("signup")) return { ok: false, blocked: "no organization" };
    const staffSide = await staffOrgAction(staff, company, "Suspend organization");
    if (first) await audit(staff.page, "admin /admin/customers/<org> (suspended)");
    await goto(page, `/o/${st.slug}/apps`);
    await waitForText(page, p.name);
    const text = await bodyText(page);
    const banner = text.includes("This organization is read-only (suspended)");
    const l = await launchFromCard(page, st.slug, p, st.orgId, `${p.key} launch while suspended`);
    const direct = await withExpectedFailures(() => api(page, "POST", `/api/v1/orgs/${st.slug}/apps/${p.code}/launch`, undefined, { idempotency: false }));
    return {
      ok: banner && !l.target && !!l.refused_with && /read-only/.test(l.refused_with) && direct.status >= 400,
      staff: staffSide,
      read_only_banner: banner,
      launch_refused_in_ui_with: l.refused_with,
      navigation_attempted: l.target,
      launch_api: direct,
    };
  });
  mark(p.key, "Suspend", suspend.ok ? "PASS" : need("signup") ? "FAIL" : "BLOCKED", suspend.ok ? `staff "Suspend organization" (step-up asked: ${(suspend.staff || {}).prompted_for_second_factor}); banner "read-only (suspended)"; Open refused: "${suspend.launch_refused_in_ui_with}"; launch API ${(suspend.launch_api || {}).status} ${JSON.stringify((suspend.launch_api || {}).body).slice(0, 120)}` : suspend.error || JSON.stringify(suspend).slice(0, 300));

  const resume = await flow(`${pre}.10 Super Admin reactivates the organization; launch works again`, async () => {
    if (!need("signup")) return { ok: false, blocked: "no organization" };
    const staffSide = await staffOrgAction(staff, company, "Reactivate organization");
    await goto(page, `/o/${st.slug}/apps`);
    const banner = await textVisible(page, "read-only");
    const l = await launchFromCard(page, st.slug, p, st.orgId, `${p.key} launch after reactivation`);
    return { ok: !banner && l.ok, staff: staffSide, read_only_banner_gone: !banner, launch: l };
  });
  mark(p.key, "Resume", resume.ok ? "PASS" : need("signup") ? "FAIL" : "BLOCKED", resume.ok ? `staff "Reactivate organization"; banner gone; Open -> ${(resume.launch || {}).target}` : resume.error || JSON.stringify(resume).slice(0, 300));
  const launchOk = launch1.ok && resume.ok && (resume.launch || {}).ok;
  mark(p.key, "Launch", launchOk ? "PASS" : need("signup") ? "FAIL" : "BLOCKED", `trial launch -> ${((launch1.launch || {}).target) || "none"}; after reactivation -> ${((resume.launch || {}).target) || "none"}; registered ${p.launchUrl}; organization param matches org uuid ${st.orgId}: ${(launch1.launch || {}).organization_matches}`);

  const cancel = await flow(`${pre}.11 the customer cancels in Subscription & billing ("Cancel subscription" -> "Yes, cancel at the end of the period"); the product stays launchable until the period ends`, async () => {
    if (!need("signup")) return { ok: false, blocked: "no organization" };
    await goto(page, `/o/${st.slug}/settings/billing`);
    await waitForText(page, "Subscription");
    await page.waitForNetworkIdle({ idleTime: 500, timeout: 10000 }).catch(() => {});
    if (first) await audit(page, "org Subscription & billing");
    const subs = await api(page, "GET", `/api/v1/orgs/${st.slug}/subscriptions`);
    const sub = ((subs.body || {}).results || []).find((s) => s.status !== "expired" && s.items.some((i) => i.product_code === p.code && !i.removed_at));
    const hasButton = () => page.evaluate(() => [...document.querySelectorAll("main button")].some((b) => b.innerText.trim() === "Cancel subscription"));
    if (!(await hasButton())) {
      const controls = await page.evaluate(() => [...document.querySelectorAll("main button, main a")].map((b) => (b.getAttribute("aria-label") || b.innerText).trim()));
      return { ok: false, reason: 'no "Cancel subscription" button', controls, screenshot: await shot(page, `${p.key}-billing-no-cancel`) };
    }
    const confirmText = () => page.evaluate(() => {
      const g = [...document.querySelectorAll('[role="group"]')].find((x) => x.innerText.includes("Yes, cancel at the end of the period"));
      return g ? g.innerText.split("\n").filter(Boolean).join(" | ") : null;
    });
    let kb = null;
    let prompt;
    if (first) {
      // Keyboard only: open the in-place confirm, try Escape, close it with its own Cancel, reopen, confirm with Enter.
      await page.evaluate(() => document.activeElement && document.activeElement.blur());
      kb = { tabs_to_cancel_subscription: await tabUntil(page, focusedButtonIs, "Cancel subscription") };
      await page.keyboard.press("Enter");
      await sleep(400);
      prompt = await confirmText();
      kb.focus_on_confirm_after_opening = await page.evaluate(focusedButtonIs, "Yes, cancel at the end of the period");
      await page.keyboard.press("Escape");
      await sleep(400);
      kb.escape_closes_the_confirm = !(await confirmText());
      if (!kb.escape_closes_the_confirm) {
        kb.tabs_to_its_cancel_button = await tabUntil(page, focusedButtonIs, "Cancel", 10);
        await page.keyboard.press("Enter");
        await sleep(400);
      }
      kb.closed = !(await confirmText());
      kb.focus_returned_to_trigger = await page.evaluate(focusedButtonIs, "Cancel subscription");
      await page.keyboard.press("Enter");
      await sleep(400);
      kb.reopened_with_focus_on_confirm = !!(await confirmText()) && (await page.evaluate(focusedButtonIs, "Yes, cancel at the end of the period"));
      await audit(page, "org Subscription & billing (cancel confirm open)");
      await page.evaluate(() => {
        const b = [...document.querySelectorAll("button")].find((x) => x.innerText.trim() === "Yes, cancel at the end of the period");
        if (b) b.focus();
      });
      await page.keyboard.press("Enter");
      report.keyboard.push({
        check: "in-place confirm (Cancel subscription): Enter opens it with focus on the confirm button; its Cancel closes it and returns focus; Enter confirms",
        ok: kb.focus_on_confirm_after_opening && kb.closed && kb.focus_returned_to_trigger && kb.reopened_with_focus_on_confirm,
        detail: kb,
        note: kb.escape_closes_the_confirm ? "Escape also closes it" : "Escape does not close this inline (non-modal) confirm; its Cancel button does",
      });
    } else {
      await clickText(page, "Cancel subscription", { exact: true });
      await waitForText(page, "Yes, cancel at the end of the period");
      prompt = await confirmText();
      await clickText(page, "Yes, cancel at the end of the period", { exact: true });
    }
    await waitForText(page, "Subscription cancelled.", 25000);
    await sleep(1200);
    const after = await page.evaluate(() => {
      const dt = [...document.querySelectorAll("dt")].find((d) => d.innerText.trim() === "Ends on");
      return {
        ends_on: dt && dt.nextElementSibling ? dt.nextElementSibling.innerText.trim() : null,
        renews_on_shown: [...document.querySelectorAll("dt")].some((d) => d.innerText.trim() === "Renews on"),
        cancel_button_still_there: [...document.querySelectorAll("main button")].some((b) => b.innerText.trim() === "Cancel subscription"),
        message: ([...document.querySelectorAll('[role="status"]')].find((x) => x.innerText.includes("Subscription cancelled")) || {}).innerText,
      };
    });
    const subsAfter = await api(page, "GET", `/api/v1/orgs/${st.slug}/subscriptions`);
    const subAfter = ((subsAfter.body || {}).results || []).find((s) => sub && s.id === sub.id) || {};
    await goto(page, `/o/${st.slug}/apps`);
    await waitForText(page, p.name);
    const card = await appCard(page, p.name);
    const app = (await appsOf(page, st.slug)).find((a) => a.product_code === p.code) || {};
    const l = await launchFromCard(page, st.slug, p, st.orgId, `${p.key} launch after cancelling (before the period ends)`);
    return {
      ok:
        !!prompt &&
        /stay available until/.test(prompt) &&
        !!after.ends_on &&
        !after.cancel_button_still_there &&
        !!subAfter.cancel_at &&
        !!card &&
        card.badges.includes("Active") &&
        card.text.includes("Ending - won't renew.") &&
        l.ok,
      subscription_before: sub && { id: sub.id, status: sub.status, cancel_at: sub.cancel_at, current_period_end: sub.current_period_end },
      confirm_prompt: prompt,
      billing_page_after: after,
      subscription_after: { status: subAfter.status, cancel_at: subAfter.cancel_at, current_period_end: subAfter.current_period_end },
      entitlement_after: { state: app.state, entitlement_status: app.entitlement_status, cancel_at: app.cancel_at, renews_at: app.renews_at },
      card_after: card && { badges: card.badges, text: card.text },
      launch_after_cancel: l.target || l.refused_with || l.clicked,
      keyboard: kb,
    };
  });
  mark(
    p.key,
    "Cancel",
    cancel.ok ? "PASS" : need("signup") ? "FAIL" : "BLOCKED",
    cancel.ok
      ? `UI: "Cancel subscription" -> "${(cancel.confirm_prompt || "").slice(0, 110)}" -> "Yes, cancel at the end of the period"; page then shows "Ends on ${cancel.billing_page_after.ends_on}", button gone; subscription ${cancel.subscription_after.status} cancel_at ${cancel.subscription_after.cancel_at}; card ${JSON.stringify(cancel.card_after.badges)} "Ending - won't renew."; Open still -> ${cancel.launch_after_cancel}`
      : cancel.error || JSON.stringify(cancel).slice(0, 400),
  );

  const stepFlows = report.flows.filter((f) => f.flow.startsWith(`${pre}.`));
  const failed = stepFlows.filter((f) => !f.ok && !/\.5 SSO/.test(f.flow)).map((f) => f.flow.split(" ")[0]);
  const csp = stepFlows.reduce((n, f) => n + (f.csp_during || 0), 0);
  mark(p.key, "Browser E2E", failed.length === 0 && csp === 0 ? "PASS" : "FAIL", failed.length ? `steps not passing: ${failed.join(", ")}; CSP violations: ${csp}` : `all steps passed in Chrome; CSP violations: ${csp}`);
  return st;
}

// The storefront card's own "Start free trial" (it remembers {plan, trial:true},
// not the product trial) followed end to end by a separate visitor.
async function journeyStoreCardTrial(browser) {
  const p = byKey.HELPDESK;
  await flow(`8x. the /products store card "Start free trial: ${p.name}" followed end to end by a new visitor`, async () => {
    const email = `p12-${RUN}-storecard@browser.test`;
    const company = `P12 StoreCard ${RUN}`;
    const context = await browser.createBrowserContext();
    const page = await newPage(context);
    await goto(page, "/products");
    await waitForText(page, p.name);
    await clickSelector(page, `[aria-label="Start free trial: ${p.name}"]`);
    await page.waitForFunction(() => location.pathname === "/signup", { timeout: 15000 });
    const intent = await page.evaluate(() => localStorage.getItem("w360.purchase-intent"));
    await fill(page, "First name", "Store");
    await fill(page, "Last name", "Card");
    await fill(page, "Work email", email);
    await fill(page, "Password", PASSWORD);
    await fill(page, "Company name (optional)", company);
    await clickText(page, "Create account", { exact: true });
    await waitForText(page, "Check your email", 20000);
    const mail = await waitForMail(email, "verify-email");
    await goto(page, `${BASE}/verify-email#token=${mail.token}`);
    await waitForText(page, "Confirm and sign in");
    await page.type('input[type="password"]', PASSWORD);
    await clickText(page, "Confirm and sign in", { exact: true });
    await page.waitForFunction(() => /^\/o\/[^/]+\/(apps|dashboard|checkout)/.test(location.pathname), { timeout: 40000 });
    await page.waitForNetworkIdle({ idleTime: 800, timeout: 15000 }).catch(() => {});
    const landed = page.url().replace(BASE, "");
    let shown = "";
    if (landed.includes("/checkout")) {
      await page.waitForFunction(() => /Billing details|Order summary|could not|isn't|not /.test(document.querySelector("main").innerText), { timeout: 20000 }).catch(() => {});
      if (await textVisible(page, "Billing details")) {
        await choose(page, "Currency", "GBP");
        await clickText(page, "Open billing account", { exact: true });
        await page.waitForFunction(() => /Order summary|alert/.test(document.querySelector("main").innerText) || document.querySelector('[role="alert"]'), { timeout: 20000 }).catch(() => {});
        await sleep(1500);
      }
      shown = await page.evaluate(() => document.querySelector("main").innerText.replace(/\n+/g, " | ").slice(0, 700));
    }
    const slug = (landed.match(/^\/o\/([^/?#]+)/) || [])[1];
    const app = slug ? (await appsOf(page, slug)).find((a) => a.product_code === p.code) : null;
    const file = await shot(page, "storecard-trial-landing");
    await context.close();
    const isFreeTrial = /Free trial/.test(shown) && /nothing to pay now/.test(shown);
    const promisesTrialButCharges = /Due now/.test(shown) && !isFreeTrial;
    const productTrialIntent = /"productTrial":\{"product":"[a-z_]+","plan":"[a-z_]+"\}/.test(intent || "");
    const trialStarted = landed.includes(`trial_started=${p.code}`) && !landed.includes("/checkout") && !!app && app.entitlement_status === "trial";
    if (!trialStarted) {
      bug(`The storefront card's "Start free trial" (/products) does not start the product's free trial`, {
        url: `${BASE}/products -> [aria-label="Start free trial: ${p.name}"] -> signup -> verify`,
        remembered_intent: intent,
        landed,
        checkout_shows: shown,
        entitlement_after: app && { state: app.state, entitlement_status: app.entitlement_status },
        screenshot: file,
      });
    }
    return {
      ok: productTrialIntent && trialStarted,
      remembered_intent: intent,
      remembers_the_product_trial: productTrialIntent,
      landed,
      went_through_a_checkout: landed.includes("/checkout"),
      checkout_shows: shown || null,
      asks_for_payment_instead: promisesTrialButCharges,
      entitlement_after: app && { state: app.state, entitlement_status: app.entitlement_status, trial: app.trial },
      screenshot: file,
    };
  });
}

// ---- Step 9: one organization, a mix of products ------------------------------------
async function journeyOrganizationA(browser) {
  const email = `p12-${RUN}-orga@browser.test`;
  const company = `Organization A ${RUN}`;
  const context = await browser.createBrowserContext();
  const page = await newPage(context);
  const A = { email, company, context, page };
  ctx.orgA = A;

  await flow("9.1 Organization A: sign up (no product chosen), verify, land on the dashboard", async () => {
    await goto(page, "/signup");
    await fill(page, "First name", "Alex");
    await fill(page, "Last name", "Owner");
    await fill(page, "Work email", email);
    await fill(page, "Password", PASSWORD);
    await fill(page, "Company name (optional)", company);
    await clickText(page, "Create account", { exact: true });
    await waitForText(page, "Check your email", 20000);
    const mail = await waitForMail(email, "verify-email");
    await goto(page, `${BASE}/verify-email#token=${mail.token}`);
    await waitForText(page, "Confirm and sign in");
    await page.type('input[type="password"]', PASSWORD);
    await clickText(page, "Confirm and sign in", { exact: true });
    await page.waitForFunction(() => /^\/o\/[^/]+\/dashboard/.test(location.pathname), { timeout: 40000 });
    const org = (await orgOf(page)).find((o) => o.display_name === company);
    A.slug = org && org.slug;
    A.orgId = org && org.organization_id;
    return { ok: !!org, landed: page.url().replace(BASE, ""), organization: org };
  });
  if (!A.slug) return;

  // Buys one product's Team plan outright from Explore -> "Product packages".
  const buyPackage = async (p, { keyboard = false, auditLabel = null } = {}) => {
    await goto(page, `/o/${A.slug}/explore`);
    const clicked = await clickPackageAction(page, p);
    if (!/^Buy/.test(clicked || "")) return { ok: false, clicked };
    const paid = await checkoutAndPay(page, { keyboard, auditLabel });
    const app = await waitEntitlement(page, A.slug, p.code, ["active"], 90000);
    return { ok: paid.paid && !!app && app.entitlement_status === "active", clicked, quote: paid.quote, entitlement_status: app && app.entitlement_status, keyboard: paid.kb };
  };

  const crm = await flow("9.2 buy Easy CRM (Team) from Explore -> Product packages, paying with the TEST provider - keyboard only", async () => {
    const r = await buyPackage(byKey.CRM, { keyboard: true, auditLabel: "org checkout (Organization A)" });
    report.keyboard.push({
      check: "checkout form operated with the keyboard alone (Currency select, Open billing account, Place order and pay, the hosted page's Pay)",
      ok: r.ok && !!r.keyboard && r.keyboard.currency_chosen_by_keyboard === "GBP" && Object.values(r.keyboard).every((v) => v !== 0),
      detail: r.keyboard,
    });
    return r;
  });
  const book = await flow("9.3 buy EasyBook (Team) the same way", async () => buyPackage(byKey.BOOK));
  const project = await flow("9.4 start the Easy Project free trial from Explore", async () => {
    await goto(page, `/o/${A.slug}/explore`);
    await waitForText(page, byKey.PROJECT.name);
    await clickSelector(page, `[aria-label="Start free trial: ${byKey.PROJECT.name}"]`);
    await waitForText(page, `Trial started: ${byKey.PROJECT.name}.`, 20000);
    const app = await waitEntitlement(page, A.slug, byKey.PROJECT.code, ["trial"], 30000);
    return { ok: !!app && app.entitlement_status === "trial", entitlement_status: app && app.entitlement_status, trial: app && app.trial };
  });

  await flow("9.5 My products / dashboard: CRM Active, EasyBook Active, Project Trial; Help Desk only under Explore", async () => {
    await goto(page, `/o/${A.slug}/apps`);
    await waitForText(page, byKey.PROJECT.name);
    const cards = {};
    for (const p of PRODUCTS) cards[p.key] = await appCard(page, p.name);
    await audit(page, "org My products (Organization A: mixed)");
    await goto(page, `/o/${A.slug}/dashboard`);
    await waitForText(page, byKey.PROJECT.name);
    const dash = {};
    for (const p of PRODUCTS) dash[p.key] = await appCard(page, p.name);
    const available = await page.evaluate(() => {
      const panel = [...document.querySelectorAll("section, div")].find((s) => (s.querySelector("h2") || {}).innerText === "Available products");
      return panel ? panel.innerText.replace(/\n+/g, " | ") : null;
    });
    await audit(page, "org dashboard (Organization A: mixed)");
    await goto(page, `/o/${A.slug}/explore`);
    await waitForText(page, "Explore products");
    await page.waitForNetworkIdle({ idleTime: 500, timeout: 10000 }).catch(() => {});
    const explore = await exploreCards(page);
    await audit(page, "org Explore products (Organization A: mixed)");
    const badges = (c) => (c ? c.badges : null);
    const ok =
      badges(cards.CRM)?.includes("Active") &&
      badges(cards.BOOK)?.includes("Active") &&
      badges(cards.PROJECT)?.includes("Trial") &&
      cards.HELPDESK === null &&
      badges(dash.CRM)?.includes("Active") &&
      badges(dash.BOOK)?.includes("Active") &&
      badges(dash.PROJECT)?.includes("Trial") &&
      dash.HELPDESK === null &&
      explore.some((c) => c.name === byKey.HELPDESK.name) &&
      !explore.some((c) => c.name === byKey.CRM.name || c.name === byKey.BOOK.name);
    return {
      ok: !!ok,
      my_products: Object.fromEntries(Object.entries(cards).map(([k, c]) => [k, badges(c)])),
      dashboard: Object.fromEntries(Object.entries(dash).map(([k, c]) => [k, badges(c)])),
      dashboard_available_products_panel: available,
      explore_products: explore.map((c) => `${c.name}: ${c.actions.join("/")}`),
      prior: { crm: crm.ok, book: book.ok, project: project.ok },
    };
  });

  await flow("9.6 convert the Easy Project trial to paid (Explore -> Buy) and purchase Easy Help Desk (Product packages)", async () => {
    await goto(page, `/o/${A.slug}/explore`);
    await waitForText(page, byKey.PROJECT.name);
    const card = (await exploreCards(page)).find((c) => c.name === byKey.PROJECT.name);
    let path = `Explore product card "Buy: ${byKey.PROJECT.name}"`;
    if (card && card.actions.includes(`Buy: ${byKey.PROJECT.name}`)) await clickSelector(page, `[aria-label="Buy: ${byKey.PROJECT.name}"]`);
    else path = `no Buy on the trial card (${JSON.stringify(card && card.actions)}); Product packages -> ${await clickPackageAction(page, byKey.PROJECT)}`;
    const paidProject = await checkoutAndPay(page);
    const projectApp = await waitEntitlement(page, A.slug, byKey.PROJECT.code, ["active"], 90000);
    const helpdesk = await buyPackage(byKey.HELPDESK);
    return {
      ok: paidProject.paid && !!projectApp && projectApp.entitlement_status === "active" && helpdesk.ok,
      project_card_before: card && card.text,
      project_path: path,
      project_quote: paidProject.quote,
      project_status: projectApp && projectApp.entitlement_status,
      helpdesk,
    };
  });

  await flow("9.7 all four coexist Active in My products and on the dashboard; Explore offers nothing more", async () => {
    await goto(page, `/o/${A.slug}/apps`);
    await waitForText(page, byKey.HELPDESK.name);
    const cards = {};
    for (const p of PRODUCTS) cards[p.key] = (await appCard(page, p.name)) || {};
    await goto(page, `/o/${A.slug}/dashboard`);
    await waitForText(page, byKey.HELPDESK.name);
    const dash = {};
    for (const p of PRODUCTS) dash[p.key] = (await appCard(page, p.name)) || {};
    const apps = await appsOf(page, A.slug);
    await goto(page, `/o/${A.slug}/explore`);
    await waitForText(page, "Explore products");
    await page.waitForNetworkIdle({ idleTime: 500, timeout: 10000 }).catch(() => {});
    const exploreText = await bodyText(page);
    const all = PRODUCTS.every((p) => (cards[p.key].badges || []).includes("Active") && (dash[p.key].badges || []).includes("Active"));
    return {
      ok: all && exploreText.includes("Your organization already has every product Workeasy360 offers."),
      my_products: Object.fromEntries(Object.entries(cards).map(([k, c]) => [k, c.badges])),
      dashboard: Object.fromEntries(Object.entries(dash).map(([k, c]) => [k, c.badges])),
      api: apps.map((a) => `${a.product_code}:${a.state}/${a.entitlement_status}`),
    };
  });
}

// ---- Step 37: CSRF from a real signed-in browser page -------------------------------
async function journeyCsrf() {
  const A = ctx.orgA;
  await flow("37. CSRF: a cookie-authenticated write without X-CSRFToken is 403; with it, the refusal (if any) is a business one", async () => {
    if (!A || !A.slug) return { ok: false, blocked: "no Organization A" };
    const page = A.page;
    await goto(page, `/o/${A.slug}/dashboard`);
    const body = { product_code: byKey.HELPDESK.code, plan_code: `${byKey.HELPDESK.code}_team` };
    const without = await withExpectedFailures(() => api(page, "POST", `/api/v1/orgs/${A.slug}/trials`, body, { csrf: false }));
    const withToken = await withExpectedFailures(() => api(page, "POST", `/api/v1/orgs/${A.slug}/trials`, body, { csrf: true }));
    const csrfShaped = (r) => r.status === 403 && /csrf/i.test(JSON.stringify(r.body));
    return {
      ok: without.status === 403 && !!without.body && without.body.code === "csrf_failed" && !!without.body.correlation_id && !csrfShaped(withToken) && withToken.status !== 403,
      without_token: without,
      with_token: withToken,
      note: "Organization A already holds Easy Help Desk, so with the token the server refuses for a business reason.",
    };
  });
}

// ---- partner creation through the Add Reseller wizard (Steps 10 and 11) ---------------
async function addResellerWizard(staff, { name, products = [], prices = {}, commissionPct = null, adminEmail, auditSteps = false }) {
  const page = staff.page;
  if (auditSteps) await visit(page, "/admin/partners/resellers/new", "admin Add reseller: step 1 details");
  else await goto(page, "/admin/partners/resellers/new");
  await fill(page, "Company or display name", name);
  await fill(page, "Contact email (optional)", `contact-${RUN}@browser.test`);
  await clickText(page, "Next", { exact: true });
  await waitForText(page, "Standard Reseller");
  if (auditSteps) await audit(page, "admin Add reseller: step 2 capabilities");
  await clickText(page, "Next", { exact: true });
  await waitForText(page, "Products this reseller may sell");
  for (const productName of products) await clickText(page, productName, { within: "form" });
  const selected = await page.evaluate(() => (document.body.innerText.match(/(\d+) selected\./) || [])[1]);
  const offeredProducts = await page.evaluate(() => [...document.querySelectorAll("fieldset label")].map((l) => l.innerText.replace(/\s+/g, " ").trim()));
  if (auditSteps) await audit(page, "admin Add reseller: step 3 products");
  await clickText(page, "Next", { exact: true });
  let pricing = null;
  if (products.length) {
    await waitForText(page, "Prices per month, calculated by the server", 20000);
    for (const [productName, price] of Object.entries(prices)) {
      const id = await page.evaluate((n) => {
        const tr = [...document.querySelectorAll("tbody tr")].find((r) => (r.querySelector("th") || {}).innerText.startsWith(`${n} — `));
        const input = tr && tr.querySelector("input");
        return input ? input.id : null;
      }, productName);
      if (!id) throw new Error(`no partner price input for ${productName}`);
      const el = await page.$(`[id="${id}"]`);
      await el.click({ clickCount: 3 });
      await el.type(price);
      await page.keyboard.press("Tab");
      await sleep(1200);
      await page.waitForFunction(() => !document.body.innerText.includes("Calculating…"), { timeout: 20000 }).catch(() => {});
    }
    await sleep(800);
    pricing = await page.evaluate(() => [...document.querySelectorAll("tbody tr")].map((r) => r.innerText.replace(/\s+/g, " ").trim()));
    if (auditSteps) await audit(page, "admin Add reseller: step 4 pricing");
  }
  await clickText(page, "Next", { exact: true });
  await waitForText(page, "Programme default");
  if (commissionPct !== null) {
    await clickText(page, "A rate for this partner");
    await fill(page, "Commission rate (%)", String(commissionPct));
  }
  if (auditSteps) await audit(page, "admin Add reseller: step 5 commission");
  await clickText(page, "Next", { exact: true });
  await fill(page, "Administrator's email", adminEmail);
  await clickText(page, "Next", { exact: true });
  await waitForText(page, name);
  await page.waitForFunction(() => [...document.querySelectorAll("button")].some((b) => b.innerText.includes("Create reseller") && !b.disabled), { timeout: 30000 });
  const review = await page.evaluate(() => (document.querySelector("form") || document.body).innerText.replace(/\n+/g, " | ").slice(0, 900));
  if (auditSteps) await audit(page, "admin Add reseller: step 7 review");
  await clickText(page, "Create reseller");
  await waitForText(page, `Create ${name}?`);
  if (auditSteps) await audit(page, "admin Add reseller: confirmation");
  await clickText(page, "Confirm and create", { exact: true });
  const prompted = await answerPrompt(page, staff.secret, 20000);
  await page
    .waitForFunction((n) => document.body.innerText.includes(`${n} has been set up`) || document.body.innerText.includes(`${n} was created.`), { timeout: 90000 }, name)
    .catch(async () => {
      throw new Error(`the reseller was not reported as created: ${(await bodyText(page)).slice(0, 600)}`);
    });
  const created = await page.evaluate(() => document.querySelector("main").innerText.split(String.fromCharCode(10)).filter(Boolean).join(" | ").slice(0, 700));
  await sleep(1000);
  const list = await api(page, "GET", `/api/v1/admin/partners/resellers?q=${encodeURIComponent(name)}`);
  const row = ((list.body || {}).results || []).find((r) => r.display_name === name);
  const id = row && row.id;
  const summary = id ? await api(page, "GET", `/api/v1/admin/partners/${id}/summary`) : null;
  return { id, row, selected, offeredProducts, pricing, review, prompted, created, summary: summary && summary.body };
}

// A partner administrator opens the emailed setup link in their own browser, sets a password, signs in, enrols TOTP.
async function partnerAdminOnboard(browser, email) {
  const mail = await waitForMail(email, "reset-password");
  const context = await browser.createBrowserContext();
  const page = await newPage(context);
  await goto(page, `${BASE}/reset-password#token=${mail.token}`);
  await page.waitForSelector("::-p-aria(New password)");
  await fill(page, "New password", NEW_PASSWORD);
  await fill(page, "Repeat the new password", NEW_PASSWORD);
  await clickText(page, "Change password", { exact: true });
  await waitForText(page, "Password changed", 20000);
  await signIn(page, email, NEW_PASSWORD);
  const secret = await enrollTotp(page);
  return { context, page, secret, email, subject: mail.subject };
}

async function provisionCustomer(admin, partnerId, orgName, ownerEmail) {
  const page = admin.page;
  await goto(page, `/partner/${partnerId}/customers`);
  await fill(page, "Organization name", orgName);
  await fill(page, "Owner's email", ownerEmail);
  await clickText(page, "Create customer", { exact: true });
  const prompted = await answerPrompt(page, admin.secret, 6000);
  await waitForText(page, "Customer created.", 30000);
  await waitForText(page, orgName, 20000);
  const row = await page.evaluate((n) => ([...document.querySelectorAll("tr")].find((tr) => tr.innerText.includes(n)) || {}).innerText, orgName);
  return { prompted, row: row && row.replace(/\s+/g, " ") };
}

async function activateOwner(browser, ownerEmail, orgName) {
  const mail = await waitForMail(ownerEmail, "owner-invitations/accept");
  const context = await browser.createBrowserContext();
  const page = await newPage(context);
  await goto(page, `${BASE}/owner-invitations/accept#token=${mail.token}`);
  await waitForText(page, `Activate ${orgName}`);
  const fragmentGone = !(await page.evaluate(() => location.hash));
  if (await fieldId(page, "Your name")) await fill(page, "Your name", "Journey Owner");
  await fill(page, "Choose a password", NEW_PASSWORD);
  await fill(page, "Confirm the password", NEW_PASSWORD);
  await clickText(page, "Set password and continue", { exact: true });
  await page.waitForFunction(() => location.pathname.startsWith("/o/"), { timeout: 40000 });
  await sleep(800);
  const slug = page.url().match(/\/o\/([^/?#]+)/)[1];
  const org = (await orgOf(page)).find((o) => o.slug === slug);
  return { context, page, slug, orgId: org && org.organization_id, isOwner: org && org.is_owner, fragmentGone, emailBodyHasPassword: mail.text.includes(NEW_PASSWORD) };
}

// Sell one approved plan to a customer row from the partner portal.
async function sellPlan(admin, partnerId, orgName, orgId, product) {
  const page = admin.page;
  await goto(page, `/partner/${partnerId}/customers`);
  await waitForText(page, orgName);
  await page.evaluate((n) => {
    const tr = [...document.querySelectorAll("tr")].find((r) => r.innerText.includes(n));
    [...tr.querySelectorAll("button")].find((b) => b.innerText.trim() === "Sell a plan").click();
  }, orgName);
  await page.waitForFunction((n) => {
    const tr = [...document.querySelectorAll("tr")].find((r) => r.innerText.includes(n));
    const s = tr && tr.querySelector("select");
    return s && s.options.length > 1;
  }, { timeout: 20000 }, orgName);
  const { selectId, options } = await page.evaluate((n) => {
    const tr = [...document.querySelectorAll("tr")].find((r) => r.innerText.includes(n));
    const s = tr.querySelector("select");
    return { selectId: s.id, options: [...s.options].map((o) => ({ value: o.value, text: o.text })) };
  }, orgName);
  const option = options.find((o) => o.text.startsWith(`${product.name} — `));
  if (!option) return { ok: false, offered: options.map((o) => o.text) };
  await page.select(`[id="${selectId}"]`, option.value);
  await clickText(page, "Confirm sale", { exact: true });
  const prompted = await answerPrompt(page, admin.secret, 6000);
  // The form closes on success (its message goes with it): confirm from the server.
  const deadline = Date.now() + 30000;
  let row = null;
  while (Date.now() < deadline) {
    const list = await api(page, "GET", `/api/v1/partner/${partnerId}/customers`);
    row = ((list.body || {}).results || []).find((r) => r.organization_id === orgId);
    if (row && (row.products || []).includes(product.code)) break;
    await sleep(1500);
  }
  const errorShown = await page.evaluate(() => [...document.querySelectorAll('[role="alert"]')].map((a) => a.innerText).join(" | "));
  return { ok: !!row && row.products.includes(product.code), offered: options.map((o) => o.text), chosen: option.text, prompted, row_products: row && row.products, subscription_status: row && row.subscription_status, error_shown: errorShown };
}

async function waitCommission(page, partnerId, count, timeout = 150000) {
  const deadline = Date.now() + timeout;
  let body = null;
  while (Date.now() < deadline) {
    const r = await api(page, "GET", `/api/v1/partner/${partnerId}/commissions`);
    body = r.body;
    if (r.status === 200 && (body.results || []).length >= count) return body;
    await sleep(3000);
  }
  return body;
}

// ---- Step 10: reseller ------------------------------------------------------------
async function journeyReseller(browser, staff) {
  const name = `P12 Reseller ${RUN}`;
  const adminEmail = `p12-${RUN}-reseller-admin@browser.test`;
  const R = { name, adminEmail };
  ctx.reseller = R;
  const listPrice = { CRM: 24.99, BOOK: 19.99 };
  const partnerPrice = { CRM: "22.99", BOOK: "17.99" };

  await flow("10.1 Super Admin: Add Reseller wizard granting ONLY Easy CRM + EasyBook, partner prices, a 15% commission rate, an administrator (step-up)", async () => {
    const r = await addResellerWizard(staff, { name, products: [byKey.CRM.name, byKey.BOOK.name], prices: { [byKey.CRM.name]: partnerPrice.CRM, [byKey.BOOK.name]: partnerPrice.BOOK }, commissionPct: 15, adminEmail, auditSteps: true });
    R.id = r.id;
    const s = r.summary || {};
    const progress = Object.fromEntries((s.setup_progress || []).map((p) => [p.key, p.state]));
    const products = ((r.row || {}).products || []).map((x) => x.code || x.product_code || x);
    return {
      ok: !!r.id && r.selected === "2" && (s.partner || {}).status === "active" && (s.commission_rule || {}).rate_bp === 1500,
      partner: r.id,
      wizard_products_offered: r.offeredProducts,
      selected: r.selected,
      pricing_table: r.pricing,
      review: r.review,
      completion: r.created,
      step_up_prompted: r.prompted,
      status: (s.partner || {}).status,
      progress,
      commission_rule_bp: (s.commission_rule || {}).rate_bp,
      listed_products: products,
    };
  });
  if (!R.id) return;

  await flow("10.2 the reseller administrator sets a password from the emailed link and enrols TOTP", async () => {
    R.admin = await partnerAdminOnboard(browser, adminEmail);
    return { ok: true, email_subject: R.admin.subject };
  });
  if (!R.admin) return;

  const customerName = `P12 Reseller Customer ${RUN}`;
  const ownerEmail = `p12-${RUN}-reseller-owner@browser.test`;
  R.customerName = customerName;
  R.ownerEmail = ownerEmail;
  await flow("10.3 the reseller provisions a customer (partner portal, step-up); staff see 'Owner activation pending'", async () => {
    await visit(R.admin.page, `/partner/${R.id}`, "partner portal: overview (reseller)");
    await visit(R.admin.page, `/partner/${R.id}/customers`, "partner portal: customers (reseller)");
    const r = await provisionCustomer(R.admin, R.id, customerName, ownerEmail);
    await goto(staff.page, `/admin/partners/customers`);
    const staffRows = await api(staff.page, "GET", `/api/v1/admin/partners/${R.id}/customers?q=${encodeURIComponent(customerName)}`);
    const row = ((staffRows.body || {}).results || [])[0] || {};
    return { ok: /Owner activation pending/.test(r.row || "") && (row.owner || {}).activation === "pending", ...r, staff_owner_activation: (row.owner || {}).activation };
  });

  await flow("10.4 the owner invitation is emailed; the customer activates in their own browser", async () => {
    const o = await activateOwner(browser, ownerEmail, customerName);
    R.owner = o;
    return { ok: o.isOwner === true && o.fragmentGone && !o.emailBodyHasPassword, slug: o.slug, is_owner: o.isOwner, link_fragment_removed: o.fragmentGone };
  });
  if (!R.owner) return;

  const sales = {};
  for (const p of [byKey.CRM, byKey.BOOK]) {
    sales[p.key] = await flow(`10.5 the reseller sells ${p.name} (Team) to the customer`, async () => {
      const s = await sellPlan(R.admin, R.id, customerName, R.owner.orgId, p);
      const app = await waitEntitlement(R.owner.page, R.owner.slug, p.code, ["active"], 60000);
      return { ...s, ok: s.ok && !!app, owner_entitlement: app && `${app.state}/${app.entitlement_status}` };
    });
  }

  const denied = await flow("10.6 selling Easy Project (not granted) is DENIED: absent from the sell form, and refused by the server", async () => {
    const page = R.admin.page;
    await goto(page, `/partner/${R.id}/customers`);
    await waitForText(page, customerName);
    await page.evaluate((n) => {
      const tr = [...document.querySelectorAll("tr")].find((r) => r.innerText.includes(n));
      [...tr.querySelectorAll("button")].find((b) => b.innerText.trim() === "Sell a plan").click();
    }, customerName);
    await page.waitForFunction((n) => {
      const tr = [...document.querySelectorAll("tr")].find((r) => r.innerText.includes(n));
      const s = tr && tr.querySelector("select");
      return s && s.options.length > 1;
    }, { timeout: 20000 }, customerName);
    const offered = await page.evaluate((n) => [...[...document.querySelectorAll("tr")].find((r) => r.innerText.includes(n)).querySelector("select").options].map((o) => o.text), customerName);
    await audit(page, "partner portal: customers (sell form open)");
    const projectPv = await api(page, "GET", "/api/v1/public/catalog?currency=GBP").then((r) => ((r.body.products || []).find((x) => x.code === byKey.PROJECT.code) || {}).plans[0].plan_version_id);
    const refused = await withExpectedFailures(() => api(page, "POST", `/api/v1/partner/${R.id}/customers/${R.owner.orgId}/sell`, { plan_version_id: projectPv, quantity: 1 }));
    const apps = await appsOf(R.owner.page, R.owner.slug);
    const project = apps.find((a) => a.product_code === byKey.PROJECT.code);
    return {
      ok: !offered.some((o) => o.startsWith(byKey.PROJECT.name)) && refused.status >= 400 && refused.status < 500 && (!project || project.state === "no_entitlement"),
      sell_form_offers: offered,
      server_refusal: refused,
      customer_project_state: project && project.state,
    };
  });

  const commission = await flow("10.7 commission accrues for the two sales (partner Commission page and staff Commissions)", async () => {
    const body = await waitCommission(R.admin.page, R.id, 2);
    await visit(R.admin.page, `/partner/${R.id}/commissions`, "partner portal: commissions (reseller)");
    const partnerText = await bodyText(R.admin.page);
    await visit(staff.page, "/admin/commissions", "admin /admin/commissions");
    const staffLedger = await api(staff.page, "GET", `/api/v1/admin/commission/accruals?partner_id=${R.id}`);
    const rows = (staffLedger.body || {}).results || [];
    const expected = { CRM: Math.round(22.99 * 0.15 * 100), BOOK: Math.round(17.99 * 0.15 * 100) };
    const amounts = rows.map((r) => r.amount_minor);
    return {
      ok: ((body || {}).results || []).length >= 2 && rows.length >= 2,
      partner_view: { balances: (body || {}).balances, entries: ((body || {}).results || []).map((a) => `${a.kind} ${a.amount_minor} ${a.currency} clears ${a.clears_at}`) },
      staff_view: rows.map((r) => `${r.kind} ${r.amount_minor} ${r.currency} rate_bp=${r.rate_bp} org=${r.organization_name} ${r.source_ref}`),
      expected_at_15pct_of_partner_price_minor: expected,
      amounts_match_expected: [expected.CRM, expected.BOOK].every((e) => amounts.some((a) => Math.abs(a - e) <= 1)),
      partner_page_shows_entries: /Ledger entries/.test(partnerText) && !/No commission yet/.test(partnerText),
      list_prices: listPrice,
    };
  });
  for (const p of [byKey.CRM, byKey.BOOK]) {
    mark(p.key, "Reseller", sales[p.key].ok ? "PASS" : "FAIL", sales[p.key].ok ? `Step 10 reseller (granted CRM+BOOK only) sold "${sales[p.key].chosen}"; customer entitlement ${sales[p.key].owner_entitlement}` : sales[p.key].error || JSON.stringify(sales[p.key]).slice(0, 300));
    mark(p.key, "Commission", commission.ok ? "PASS" : "FAIL", `partner ledger: ${JSON.stringify((commission.partner_view || {}).entries)}; staff: ${JSON.stringify(commission.staff_view)}; expected 15% of partner price: ${JSON.stringify(commission.expected_at_15pct_of_partner_price_minor)} (match: ${commission.amounts_match_expected})`);
  }
  R.deniedProject = denied;

  await flow("10.8 Super Admin terminates the reseller (step-up); the partner loses access; the customer still signs in and holds CRM + EasyBook", async () => {
    const page = staff.page;
    await visit(page, `/admin/partners/${R.id}`, "admin partner detail (reseller, before termination)");
    await clickText(page, "Terminate", { exact: true });
    await fill(page, "Reason for", `Programme ended - Phase 12 browser check ${RUN}`);
    await clickText(page, "Confirm", { exact: true });
    const prompted = await answerPrompt(page, staff.secret, 5000);
    await waitForText(page, "Partner terminated", 25000);
    const summary = await api(page, "GET", `/api/v1/admin/partners/${R.id}/summary`);
    const portalCustomers = await withExpectedFailures(() => api(R.admin.page, "GET", `/api/v1/partner/${R.id}/customers`));
    await visit(R.admin.page, `/partner/${R.id}`, "partner portal: overview (terminated)");
    const banner = await bodyText(R.admin.page);
    const fresh = await login(browser, ownerEmail, NEW_PASSWORD);
    const apps = await appsOf(fresh.page, R.owner.slug);
    const held = Object.fromEntries([byKey.CRM, byKey.BOOK].map((p) => {
      const a = apps.find((x) => x.product_code === p.code) || {};
      return [p.key, `${a.state}/${a.entitlement_status}`];
    }));
    await goto(fresh.page, `/o/${R.owner.slug}/apps`);
    const text = await bodyText(fresh.page);
    await fresh.context.close();
    return {
      ok: (summary.body.partner || {}).status === "terminated" && portalCustomers.status === 403 && held.CRM.endsWith("/active") && held.BOOK.endsWith("/active") && text.includes(byKey.CRM.name) && text.includes(byKey.BOOK.name),
      step_up_prompted: prompted,
      status: (summary.body.partner || {}).status,
      partner_customers_api_after: portalCustomers.status,
      portal_banner: /has ended|terminated/i.test(banner),
      customer_signed_in_again: true,
      customer_products: held,
    };
  });
}

// ---- Step 11: white-label ------------------------------------------------------------
// Earlier runs of this check leave a live domain on these hostnames: disable
// them first (staff, the same console command) so this run can claim them.
async function releaseEarlierDomains(staff) {
  const page = staff.page;
  const released = [];
  const list = await api(page, "GET", "/api/v1/admin/partners/white-label?limit=100");
  for (const row of (list.body || {}).results || []) {
    if (!row.domain || ![WL_A, WL_B].includes(row.domain.hostname)) continue;
    const domains = await api(page, "GET", `/api/v1/admin/partners/${row.id}/domains`);
    for (const d of (domains.body || {}).results || []) {
      if (![WL_A, WL_B].includes(d.hostname) || !["active", "verified", "verification_pending", "requested"].includes(d.status)) continue;
      let r = await api(page, "POST", `/api/v1/admin/domains/${d.id}/disable`, {});
      if (r.status === 401) {
        await api(page, "POST", "/api/v1/auth/mfa/step-up", { code: await freshCode(staff.secret) });
        r = await api(page, "POST", `/api/v1/admin/domains/${d.id}/disable`, {});
      }
      released.push(`${row.display_name}: ${d.hostname} ${d.status} -> ${r.status} ${(r.body || {}).status || ""}`);
    }
  }
  if (released.length) report.notes.push(`white-label: released hostnames left by earlier runs of this check: ${released.join("; ")}`);
  return released;
}

async function tlsProbe(host) {
  const browser = await newBrowser();
  try {
    const page = await newPage(await browser.createBrowserContext());
    let browserResult;
    try {
      const res = await page.goto(`https://${host}/`, { waitUntil: "networkidle0", timeout: 20000 });
      await sleep(1000);
      const shown = await page.evaluate(() => ({ h1: (document.querySelector("h1") || {}).innerText, password_inputs: document.querySelectorAll('input[type="password"]').length, forms: document.querySelectorAll("form").length }));
      browserResult = `loaded (HTTP ${res && res.status()}) at ${page.url()}: h1 "${shown.h1}", ${shown.password_inputs} password fields, ${shown.forms} forms`;
    } catch (err) {
      browserResult = `refused: ${err.message.split("\n")[0]}`;
    }
    const raw = await rawGet(host, "/");
    return { browser: browserResult, node_tls: raw.error ? `refused: ${raw.error}` : `HTTP ${raw.status}` };
  } finally {
    await browser.close();
  }
}

// The white-label wizard, driven step by step (portal or staff variant).
async function runWhiteLabelWizard(page, secret, { brandName, accent, logoFile, hostname, labelPrefix, auditIt = false }) {
  const out = {};
  const nextEnabled = () => page.waitForFunction(() => [...document.querySelectorAll("button")].some((b) => b.innerText.trim() === "Next" && !b.disabled), { timeout: 20000 });
  await waitForText(page, "Step 1 of 5: Brand identity");
  await fill(page, "Brand name", brandName);
  await fill(page, "Accent colour", accent);
  const fileInput = await page.$('input[type="file"]');
  await fileInput.uploadFile(logoFile);
  await sleep(800);
  if (auditIt) await audit(page, `${labelPrefix}: step 1 brand identity`);
  await nextEnabled();
  await clickText(page, "Next", { exact: true });
  await waitForText(page, "Step 2 of 5: Preview");
  out.preview_before_save = await textVisible(page, "This is a preview only");
  out.preview_logo = await page.evaluate(() => {
    const img = [...document.querySelectorAll("main img")].find((i) => i.src.startsWith("data:") || i.src.includes("/api/v1/"));
    return img ? { loaded: img.complete && img.naturalWidth > 0 } : null;
  });
  if (auditIt) await audit(page, `${labelPrefix}: step 2 preview`);
  const logoResponses = [];
  const onResponse = async (res) => {
    if (!/\/(branding|brand)\/logo$/.test(new URL(res.url()).pathname) || res.request().method() !== "POST") return;
    logoResponses.push({ url: res.url(), status: res.status(), body: (await res.text().catch(() => "")).slice(0, 300) });
  };
  page.on("response", onResponse);
  await withExpectedFailures(async () => {
    await clickText(page, "Save brand", { exact: true });
    out.second_factor_at_brand = await answerPrompt(page, secret, 4000);
    await page.waitForFunction(() => document.body.innerText.includes("Brand saved.") || [...document.querySelectorAll('[role="alert"]')].some((a) => a.innerText.trim()), { timeout: 40000 });
  });
  out.logo_upload = logoResponses.slice();
  if (!(await textVisible(page, "Brand saved."))) {
    const alert = await page.evaluate(() => [...document.querySelectorAll('[role="alert"]')].map((a) => a.innerText.trim()).join(" | "));
    out.logo_upload_refused = { shown: alert, responses: logoResponses.slice(), screenshot: await shot(page, `${labelPrefix}-logo-refused`) };
    // Continue without the logo so the rest of the white-label journey can run:
    // back to step 1, clear the file input, and save the brand again.
    await clickText(page, "Back", { exact: true });
    await waitForText(page, "Step 1 of 5: Brand identity");
    const input = await page.$('input[type="file"]');
    await input.uploadFile();
    await page.evaluate(() => {
      const el = document.querySelector('input[type="file"]');
      el.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await sleep(500);
    await nextEnabled();
    await clickText(page, "Next", { exact: true });
    await waitForText(page, "Step 2 of 5: Preview");
    await clickText(page, "Save brand", { exact: true });
    out.second_factor_at_brand_retry = await answerPrompt(page, secret, 4000);
    await waitForText(page, "Brand saved.", 25000);
    out.saved_without_logo = true;
  }
  page.off("response", onResponse);
  await nextEnabled();
  await clickText(page, "Next", { exact: true });
  await waitForText(page, "Step 3 of 5: Domain request");
  await fill(page, "Hostname", hostname);
  await clickText(page, "Request domain", { exact: true });
  out.second_factor_at_domain = await answerPrompt(page, secret, 6000);
  await waitForText(page, "Domain requested.", 25000);
  if (auditIt) await audit(page, `${labelPrefix}: step 3 domain request`);
  await nextEnabled();
  await clickText(page, "Next", { exact: true });
  await waitForText(page, "Step 4 of 5: DNS verification");
  await waitForText(page, `_w360-verify.${hostname}`);
  const record = await page.evaluate(() => {
    const get = (n) => {
      const dt = [...document.querySelectorAll("dt")].find((d) => d.innerText.trim() === n);
      return dt && dt.nextElementSibling ? dt.nextElementSibling.innerText.trim() : null;
    };
    return { type: get("Type"), name: get("Name"), value: get("Value"), status: get("Status") };
  });
  out.record = record;
  out.next_disabled_before_proof = await page.evaluate(() => [...document.querySelectorAll("button")].find((b) => b.innerText.trim() === "Next").disabled);
  await clickText(page, "Check verification", { exact: true });
  await sleep(2500);
  out.before_record_published = await page.evaluate(() => [...document.querySelectorAll('[role="status"]')].map((s) => s.innerText).join(" | "));
  out.unproven_before_record = !(await textVisible(page, "Ownership is verified"));
  out.zone_serial = publishTxt(record.name, record.value);
  // The zone file reaches CoreDNS through a Docker Desktop bind mount, which can
  // take a while to show a change: keep checking (no faster than the platform's
  // own rate limit allows) for up to four minutes, and record how long it took.
  const published = Date.now();
  let verified = false;
  let checks = 0;
  while (!verified && Date.now() - published < 240000) {
    await sleep(checks === 0 ? 5000 : 12000);
    if (await textVisible(page, "Ownership is verified")) {
      verified = true;
      break;
    }
    checks++;
    await clickText(page, "Check verification", { exact: true }).catch(() => {});
    await sleep(3000);
    verified = await textVisible(page, "Ownership is verified");
  }
  out.verified = verified;
  out.seconds_from_publishing_to_verified = Math.round((Date.now() - published) / 1000);
  out.verification_checks_after_publishing = checks;
  if (!verified) {
    out.failed_at = "DNS verification";
    out.page_shows = (await page.evaluate(() => document.querySelector("main").innerText)).split("\n").filter(Boolean).join(" | ").slice(0, 600);
    out.screenshot = await shot(page, `${labelPrefix}-dns-not-verified`);
    return out;
  }
  if (auditIt) await audit(page, `${labelPrefix}: step 4 DNS verification`);
  await nextEnabled();
  await clickText(page, "Next", { exact: true });
  await waitForText(page, "Step 5 of 5: Activation");
  out.tls_before_activation = await tlsProbe(hostname);
  if (!/refused/.test(out.tls_before_activation.browser)) {
    out.tls_note =
      "The edge still holds a certificate issued when this hostname was active in an earlier run of this check (the tls-allowed answer only stops issuance and renewal; a cached certificate is served until it expires). What it serves before activation is the unbranded landing: no brand, no form, no password field.";
  }
  const publicBefore = await api(page, "GET", `/api/v1/public/branding?host=${hostname}`);
  out.public_branding_before_activation = publicBefore.body && publicBefore.body.brand ? publicBefore.body.brand.brand_name : null;
  await clickText(page, "Activate the domain", { exact: true });
  out.second_factor_at_activation = await answerPrompt(page, secret, 5000);
  await waitForText(page, "Live: this domain serves the brand.", 25000);
  if (auditIt) await audit(page, `${labelPrefix}: step 5 activation`);
  const publicAfter = await api(page, "GET", `/api/v1/public/branding?host=${hostname}`);
  out.public_branding_after_activation = publicAfter.body && publicAfter.body.brand ? publicAfter.body.brand.brand_name : null;
  return out;
}

// Each partner's logo is a solid square of its own colour, so the pixel in the
// middle of the displayed image says whose logo a page is showing.
const LOGO_A_RGB = [11, 110, 79];
const LOGO_B_RGB = [122, 31, 162];
const sameColour = (rgb, want) => Array.isArray(rgb) && rgb.every((v, i) => Math.abs(v - want[i]) <= 3);

async function journeyWhiteLabel(browser, staff) {
  const W = {};
  ctx.wl = W;
  const logoA = `${SHOTS}/${RUN}-logo-a.png`;
  const logoB = `${SHOTS}/${RUN}-logo-b.png`;
  makePng(logoA, LOGO_A_RGB);
  makePng(logoB, LOGO_B_RGB);
  W.brandA = `Aurora Brand ${RUN}`;
  W.brandB = `Borealis Brand ${RUN}`;

  await flow("11.0 release hostnames left live by an earlier run of this check (staff API; harness housekeeping)", async () => ({ ok: true, released: await releaseEarlierDomains(staff) }));

  await flow("11.1 Super Admin creates white-label partner A (Add Reseller wizard, all four products) and enables white-label (UI: Partners -> White-label -> Enable)", async () => {
    const adminEmail = `p12-${RUN}-wl-admin@browser.test`;
    const r = await addResellerWizard(staff, { name: `P12 WL Partner A ${RUN}`, products: PRODUCTS.map((p) => p.name), adminEmail });
    W.aId = r.id;
    W.aAdminEmail = adminEmail;
    await visit(staff.page, `/admin/partners/white-label/${r.id}`, "admin white-label setup (partner A, before enabling)");
    await clickText(staff.page, "Enable white-label", { exact: true });
    const prompted = await answerPrompt(staff.page, staff.secret, 5000);
    await waitForText(staff.page, "Step 1 of 5: Brand identity", 25000);
    const p = await api(staff.page, "GET", `/api/v1/admin/partners/${r.id}`);
    const caps = ((p.body || {}).capabilities || []).filter((c) => c.is_effective).map((c) => c.capability);
    return { ok: !!r.id && caps.includes("branding.manage") && caps.includes("domain.manage"), partner: r.id, capabilities: caps, step_up_prompted: prompted, created_via: "UI (Add Reseller wizard + Enable white-label button)" };
  });
  if (!W.aId) return;

  await flow("11.2 partner A's administrator sets a password (emailed link), enrols TOTP, and runs the white-label wizard in the partner portal: brand, PNG logo, colour, preview, save, domain app.wl-brand.com, DNS TXT, verify, activate", async () => {
    W.aAdmin = await partnerAdminOnboard(browser, W.aAdminEmail);
    await visit(W.aAdmin.page, `/partner/${W.aId}/white-label`, "partner portal: white-label wizard (start)", { auditIt: false });
    const out = await runWhiteLabelWizard(W.aAdmin.page, W.aAdmin.secret, { brandName: W.brandA, accent: "#0b6e4f", logoFile: logoA, hostname: WL_A, labelPrefix: "partner white-label wizard", auditIt: true });
    W.aWizard = out;
    W.logoSaved = !out.saved_without_logo;
    if (out.logo_upload_refused) {
      bug("Staging environment: every file upload (the white-label PNG logo) is refused - the staging SeaweedFS has no writable volume", {
        url: `${BASE}/partner/<id>/white-label -> Save brand`,
        request: "POST /api/v1/partner/<id>/branding/logo (image/png, 32x32)",
        responses: out.logo_upload.map((r) => `${r.status} ${r.body}`),
        ui_shows: out.logo_upload_refused.shown,
        screenshot: out.logo_upload_refused.screenshot,
        root_cause: "deploy/staging/compose.staging.yml starts SeaweedFS with -volume.max=4; the four volumes were allocated to the default collection, so the bucket's collection w360-files gets none. SeaweedFS log: 'failed to find writable volumes for collection:w360-files ... No writable volumes and no free volumes left'. A direct put_object from the api container (with and without SSE) returns 500 InternalError.",
        severity: "environment (staging infra), not application code; logo steps BLOCKED",
      });
    }
    return {
      logo_saved: W.logoSaved,
      ok: W.logoSaved && out.verified && out.unproven_before_record && out.next_disabled_before_proof && out.public_branding_before_activation === null && out.public_branding_after_activation === W.brandA && (/refused/.test(out.tls_before_activation.browser) || /h1 "Workeasy360", 0 password fields, 0 forms/.test(out.tls_before_activation.browser)),
      ...out,
    };
  });

  await flow("11.3 white-label partner B (other.wl-brand.com): created by Super Admin (Add Reseller wizard), enabled, and set up through the STAFF white-label wizard", async () => {
    const r = await addResellerWizard(staff, { name: `P12 WL Partner B ${RUN}`, products: [], adminEmail: `p12-${RUN}-wl-b-admin@browser.test` });
    W.bId = r.id;
    await goto(staff.page, `/admin/partners/white-label/${r.id}`);
    await clickText(staff.page, "Enable white-label", { exact: true });
    await answerPrompt(staff.page, staff.secret, 5000);
    await waitForText(staff.page, "Step 1 of 5: Brand identity", 25000);
    const out = await runWhiteLabelWizard(staff.page, staff.secret, { brandName: W.brandB, accent: "#7a1fa2", logoFile: logoB, hostname: WL_B, labelPrefix: "admin white-label wizard (staff)", auditIt: true });
    W.logoSavedB = !out.saved_without_logo;
    return { ok: out.verified && out.public_branding_after_activation === W.brandB && W.logoSavedB, partner: r.id, logo_saved: W.logoSavedB, ...out };
  });

  // A customer of partner A holding all four products (partner A sells them).
  const custName = `P12 WL Customer ${RUN}`;
  const ownerEmail = `p12-${RUN}-wl-owner@browser.test`;
  W.custName = custName;
  W.ownerEmail = ownerEmail;
  await flow("11.4 partner A provisions a customer, the owner activates, partner A sells it all four products", async () => {
    if (!W.aAdmin) return { ok: false, blocked: "partner A administrator not onboarded" };
    await provisionCustomer(W.aAdmin, W.aId, custName, ownerEmail);
    W.owner = await activateOwner(browser, ownerEmail, custName);
    const sold = {};
    for (const p of PRODUCTS) {
      const s = await sellPlan(W.aAdmin, W.aId, custName, W.owner.orgId, p);
      const app = await waitEntitlement(W.owner.page, W.owner.slug, p.code, ["active"], 60000);
      sold[p.key] = { ok: s.ok && !!app, chosen: s.chosen, entitlement: app && `${app.state}/${app.entitlement_status}`, error: s.error_shown };
      if (p.key === "PROJECT" || p.key === "HELPDESK") {
        mark(p.key, "Reseller", sold[p.key].ok ? "PASS" : "FAIL", `Step 11 partner A (a reseller with white-label) sold "${s.chosen}"; customer entitlement ${sold[p.key].entitlement}`);
      }
    }
    W.sold = sold;
    const body = await waitCommission(W.aAdmin.page, W.aId, 4, 150000);
    const staffLedger = await api(staff.page, "GET", `/api/v1/admin/commission/accruals?partner_id=${W.aId}`);
    const rows = (staffLedger.body || {}).results || [];
    for (const p of [byKey.PROJECT, byKey.HELPDESK]) {
      mark(p.key, "Commission", rows.length >= 4 ? "PASS" : "FAIL", `Step 11 partner A (programme default rate): partner ledger ${((body || {}).results || []).length} entries; staff ledger: ${JSON.stringify(rows.map((r) => `${r.amount_minor} ${r.currency} rate_bp=${r.rate_bp}`))}`);
    }
    return { ok: Object.values(sold).every((s) => s.ok), sold, commission_entries: ((body || {}).results || []).map((a) => `${a.kind} ${a.amount_minor} ${a.currency}`), staff_ledger: rows.map((r) => `${r.amount_minor} ${r.currency} rate_bp=${r.rate_bp} ${r.organization_name}`) };
  });

  // D-035 decision B, as a customer would meet it: a fresh browser.
  const d035 = await flow("11.5 D-035 (decision B): https://app.wl-brand.com/ is a branded landing without a password field; 'Continue to sign in' hands over to the canonical sign-in page, branded; signing in there works", async () => {
    const b = await newBrowser();
    try {
      const page = await newPage(await b.createBrowserContext());
      const res = await goto(page, `https://${WL_A}/`);
      const chain = res ? res.request().redirectChain().map((r) => `${r.response() ? r.response().status() : "?"} ${r.url()}`) : [];
      await waitForText(page, "Continue to sign in");
      await sleep(1500);
      const landing = await page.evaluate(() => {
        const img = document.querySelector("main img");
        const link = [...document.querySelectorAll("a")].find((a) => a.innerText.trim() === "Continue to sign in");
        return {
          url: location.href,
          h1: (document.querySelector("h1") || {}).innerText,
          logo: img ? { src: img.getAttribute("src"), loaded: img.complete && img.naturalWidth > 0, rgb: (() => { try { const c = document.createElement("canvas"); c.width = img.naturalWidth; c.height = img.naturalHeight; const x = c.getContext("2d"); x.drawImage(img, 0, 0); const d = x.getImageData(Math.floor(c.width / 2), Math.floor(c.height / 2), 1, 1).data; return [d[0], d[1], d[2]]; } catch (e) { return String(e); } })() } : null,
          password_inputs: document.querySelectorAll('input[type="password"]').length,
          inputs: document.querySelectorAll("input").length,
          forms: document.querySelectorAll("form").length,
          continue_href: link ? link.href : null,
          accent: document.documentElement.style.getPropertyValue("--w360-brand").trim(),
          text: document.body.innerText.replace(/\n+/g, " | ").slice(0, 400),
        };
      });
      await audit(page, `branded landing on ${WL_A}`);
      const apiOnCustomHost = {
        branding: await api(page, "GET", `/api/v1/public/branding?host=${WL_A}`, undefined, { idempotency: false }).then((r) => `${r.status} ${JSON.stringify(r.body).slice(0, 120)}`),
        session: await withExpectedFailures(() => api(page, "GET", "/api/v1/auth/session", undefined, { idempotency: false })).then((r) => `${r.status} ${JSON.stringify(r.body).slice(0, 80)}`),
        catalog: await withExpectedFailures(() => api(page, "GET", "/api/v1/public/catalog", undefined, { idempotency: false })).then((r) => `${r.status} ${JSON.stringify(r.body).slice(0, 80)}`),
        login_post: await withExpectedFailures(() => api(page, "POST", "/api/v1/auth/login", { email: ownerEmail, password: NEW_PASSWORD }, { idempotency: false })).then((r) => `${r.status} ${JSON.stringify(r.body).slice(0, 80)}`),
      };
      await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0", timeout: 30000 }).catch(() => {}), clickText(page, "Continue to sign in", { exact: true })]);
      await sleep(1500);
      const canonical = await page.evaluate(() => {
        const img = document.querySelector("main img");
        return { url: location.href, h1: (document.querySelector("h1") || {}).innerText, accent: document.documentElement.style.getPropertyValue("--w360-brand").trim(), logo: img ? { src: img.getAttribute("src"), loaded: img.complete && img.naturalWidth > 0, rgb: (() => { try { const c = document.createElement("canvas"); c.width = img.naturalWidth; c.height = img.naturalHeight; const x = c.getContext("2d"); x.drawImage(img, 0, 0); const d = x.getImageData(Math.floor(c.width / 2), Math.floor(c.height / 2), 1, 1).data; return [d[0], d[1], d[2]]; } catch (e) { return String(e); } })() } : null, has_password: !!document.querySelector('input[type="password"]') };
      });
      await audit(page, "canonical /login?brand=app.wl-brand.com (partner A's brand)");
      await page.type('input[type="email"]', ownerEmail);
      await page.type('input[type="password"]', NEW_PASSWORD);
      await Promise.all([page.click('button[type="submit"]'), page.waitForNetworkIdle({ idleTime: 800, timeout: 20000 }).catch(() => {})]);
      await page.waitForFunction(() => /^\/o\/[^/]+\/dashboard/.test(location.pathname), { timeout: 30000 }).catch(() => {});
      const afterSignIn = page.url();
      const session = await api(page, "GET", "/api/v1/auth/session", undefined, { idempotency: false });
      await waitForText(page, "My products").catch(() => {});
      const topbar = await page.evaluate(() => (document.querySelector("header") || {}).innerText);
      const headerLogo = await page.evaluate(() => {
        const img = document.querySelector("header img");
        return img ? { src: img.getAttribute("src"), loaded: img.complete && img.naturalWidth > 0 } : null;
      });
      await audit(page, "org dashboard of partner A's customer (branded console)");
      W.customerBrowser = b;
      W.customerPage = page;
      const ok =
        landing.url === `https://${WL_A}/login` &&
        landing.h1 === W.brandA &&
        landing.password_inputs === 0 &&
        landing.forms === 0 &&
        landing.continue_href === `${BASE}/login?brand=${WL_A}` &&
        (W.logoSaved ? !!landing.logo && landing.logo.loaded && sameColour(landing.logo.rgb, LOGO_A_RGB) : true) &&
        canonical.url === `${BASE}/login?brand=${WL_A}` &&
        canonical.h1 === W.brandA &&
        (W.logoSaved ? !!canonical.logo && canonical.logo.loaded && sameColour(canonical.logo.rgb, LOGO_A_RGB) : true) &&
        session.body.authenticated === true &&
        afterSignIn.startsWith(`${BASE}/o/`);
      return { ok, logo_checked: W.logoSaved ? "yes: the landing and the ?brand= sign-in page must both show the uploaded PNG" : "BLOCKED (logo upload refused by staging storage)", console_header_logo: headerLogo, redirect_chain_for_root: chain, landing, api_on_custom_host: apiOnCustomHost, canonical_login: canonical, after_sign_in: afterSignIn, console_topbar_shows: topbar, console_branded: (topbar || "").includes(W.brandA) };
    } catch (err) {
      await b.close();
      throw err;
    }
  });

  await flow("11.6 the branded customer's products: all four held and launchable from the canonical host", async () => {
    if (!W.customerPage || !W.owner) {
      for (const p of PRODUCTS) mark(p.key, "White-label", "BLOCKED", `blocked: the white-label customer journey did not reach a signed-in console (${d035.error || "see 11.1-11.5"})`);
      return { ok: false, blocked: "no signed-in white-label customer" };
    }
    const results = {};
    const seats = {};
    for (const p of PRODUCTS) seats[p.key] = await assignOwnSeat(W.customerPage, W.owner.slug, p, W.ownerEmail);
    for (const p of PRODUCTS) {
      const l = await launchFromCard(W.customerPage, W.owner.slug, p, W.owner.orgId, `${p.key} launch by partner A's (white-label) customer`);
      results[p.key] = l;
      mark(p.key, "White-label", l.ok && d035.ok ? "PASS" : "FAIL", `customer of white-label partner A: branded landing on ${WL_A} -> canonical sign-in branded "${W.brandA}" -> console header ${d035.console_branded ? "shows" : "does NOT show"} the brand -> Open ${p.name} -> ${l.target || l.refused_with || l.clicked}; logo: ${W.logoSaved ? `uploaded and shown (landing ${JSON.stringify((d035.landing || {}).logo)}, ?brand= sign-in ${JSON.stringify((d035.canonical_login || {}).logo)})` : "BLOCKED (the staging file store refuses every upload)"}`);
    }
    return { ok: Object.values(results).every((l) => l.ok), seats, targets: Object.fromEntries(Object.entries(results).map(([k, l]) => [k, l.target || l.refused_with || l.clicked])) };
  });

  await flow("11.7 other paths on the custom host redirect to the canonical host; a second partner's domain shows only its own brand; an unapproved name gets no certificate", async () => {
    const b = W.customerBrowser || (await newBrowser());
    try {
      const page = await newPage(await b.createBrowserContext());
      const res = await goto(page, `https://${WL_A}/dashboard`);
      const dashChain = res ? res.request().redirectChain().map((r) => `${r.response() ? r.response().status() : "?"} ${r.url()} -> ${r.response() ? r.response().headers().location : ""}`) : [];
      const dashFinal = page.url();
      const raw = {
        root: await rawGet(WL_A, "/"),
        dashboard: await rawGet(WL_A, "/dashboard"),
        settings: await rawGet(WL_A, "/o/anything/settings/billing?x=1"),
        login: await rawGet(WL_A, "/login"),
        api_session: await rawGet(WL_A, "/api/v1/auth/session"),
      };
      await goto(page, `https://${WL_B}/`);
      await waitForText(page, "Continue to sign in");
      await sleep(1200);
      const other = await page.evaluate(() => ({ url: location.href, h1: (document.querySelector("h1") || {}).innerText, text: document.body.innerText, logo: (() => { const img = document.querySelector("main img"); return img ? { src: img.getAttribute("src"), loaded: img.complete && img.naturalWidth > 0, rgb: (() => { try { const c = document.createElement("canvas"); c.width = img.naturalWidth; c.height = img.naturalHeight; const x = c.getContext("2d"); x.drawImage(img, 0, 0); const d = x.getImageData(Math.floor(c.width / 2), Math.floor(c.height / 2), 1, 1).data; return [d[0], d[1], d[2]]; } catch (e) { return String(e); } })() } : null; })(), continue_href: ([...document.querySelectorAll("a")].find((a) => a.innerText.trim() === "Continue to sign in") || {}).href }));
      await audit(page, `branded landing on ${WL_B}`);
      await goto(page, `/login?brand=${WL_B}`);
      await sleep(1500);
      const canonicalB = await page.evaluate(() => (document.querySelector("h1") || {}).innerText);
      await goto(page, `/login?brand=${WL_UNAPPROVED}`);
      await sleep(1500);
      const canonicalUnapproved = await page.evaluate(() => (document.querySelector("h1") || {}).innerText);
      const shop = await tlsProbe(WL_UNAPPROVED);
      const ok =
        dashFinal === `${BASE}/dashboard` || dashFinal.startsWith(`${BASE}/login`) ||
        false;
      const bleed = other.text.includes(W.brandA);
      return {
        ok: ok && other.h1 === W.brandB && !bleed && (W.logoSavedB ? !!other.logo && other.logo.loaded && sameColour(other.logo.rgb, LOGO_B_RGB) : true) && other.continue_href === `${BASE}/login?brand=${WL_B}` && canonicalB === W.brandB && canonicalUnapproved === "Workeasy360" && /refused/.test(shop.browser) && raw.dashboard.status === 302 && raw.dashboard.location === `${BASE}/dashboard`,
        custom_host_dashboard: { chain: dashChain, final: dashFinal },
        raw_edge_responses_on_custom_host: raw,
        second_partner_domain: { url: other.url, h1: other.h1, shows_partner_A_brand: bleed, continue_href: other.continue_href, logo: other.logo },
        canonical_login_brand_other: canonicalB,
        canonical_login_brand_unapproved: canonicalUnapproved,
        unapproved_hostname_tls: shop,
      };
    } finally {
      await b.close();
      W.customerBrowser = null;
    }
  });
}

// =============================================================== keyboard only ==

async function tabUntil(page, predicate, arg, max = 150) {
  for (let i = 0; i < max; i++) {
    await page.keyboard.press("Tab");
    if (await page.evaluate(predicate, arg)) return i + 1;
  }
  return 0;
}
const focusedLabelStarts = (text) => {
  const el = document.activeElement;
  return !!(el && el.id && [...document.querySelectorAll("label")].some((l) => l.htmlFor === el.id && l.innerText.replace(/\s+/g, " ").trim().startsWith(text)));
};
const focusedButtonIs = (text) => {
  const el = document.activeElement;
  return !!(el && el.tagName === "BUTTON" && el.innerText.trim() === text);
};
const focusDescription = () => {
  const el = document.activeElement;
  return el ? `${el.tagName}${el.getAttribute("aria-label") ? `[${el.getAttribute("aria-label")}]` : ""} "${(el.innerText || "").slice(0, 40)}"` : "none";
};

async function journeyKeyboard(browser, staff) {
  const A = ctx.orgA;
  await flow("K1. signing in with the keyboard alone", async () => {
    if (!A || !A.slug) return { ok: false, blocked: "no Organization A" };
    const context = await browser.createBrowserContext();
    const page = await newPage(context);
    await goto(page, "/login");
    const toEmail = await tabUntil(page, () => document.activeElement && document.activeElement.type === "email");
    await page.keyboard.type(A.email);
    await page.keyboard.press("Tab");
    await page.keyboard.type(PASSWORD);
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => /^\/o\/[^/]+\/dashboard/.test(location.pathname), { timeout: 30000 }).catch(() => {});
    const session = await page.evaluate(() => fetch("/api/v1/auth/session").then((r) => r.json()));
    const skip = await page.evaluate(() => [...document.querySelectorAll("a")].some((a) => /skip to main/i.test(a.innerText)));
    await context.close();
    const r = { ok: session.authenticated === true && toEmail > 0, tab_presses_to_email: toEmail, skip_link_on_dashboard: skip };
    report.keyboard.push({ check: "sign in (Tab to Email, type, Tab, type, Enter)", ...r });
    return r;
  });

  await flow("K2. dialogs: the navigation drawer (390 px) and the account menu open from the keyboard and close with Escape, focus returning to their button", async () => {
    if (!A || !A.slug) return { ok: false, blocked: "no Organization A" };
    const page = await newPage(A.context, 390);
    await goto(page, `/o/${A.slug}/dashboard`);
    await waitForText(page, "My products");
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    const toMenu = await tabUntil(page, () => document.activeElement && document.activeElement.getAttribute("aria-label") === "Menu");
    await page.keyboard.press("Enter");
    await sleep(500);
    const opened = await page.evaluate(() => ({ expanded: document.activeElement && document.querySelector('[aria-label="Menu"]').getAttribute("aria-expanded"), focus_inside_nav: !!(document.activeElement && document.activeElement.closest("nav")) }));
    await audit(page, "org dashboard at 390px with the navigation drawer open");
    // audit() tabbed around: put focus back inside the drawer, then Escape.
    await page.evaluate(() => {
      const n = document.querySelector("nav a, nav button");
      if (n) n.focus();
    });
    await page.keyboard.press("Escape");
    await sleep(400);
    const closed = await page.evaluate(() => ({ expanded: document.querySelector('[aria-label="Menu"]').getAttribute("aria-expanded"), focus: document.activeElement && document.activeElement.getAttribute("aria-label") }));
    await page.setViewport({ width: 1280, height: 900 });
    await sleep(400);
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    const toAccount = await tabUntil(page, () => document.activeElement && (document.activeElement.getAttribute("aria-label") || "").startsWith("Account menu"));
    await page.keyboard.press("Enter");
    await sleep(400);
    const menuOpen = await page.evaluate(() => !!document.querySelector('[role="menu"]'));
    await page.keyboard.press("Escape");
    await sleep(300);
    const menuClosed = await page.evaluate(() => ({ open: !!document.querySelector('[role="menu"]'), focus: (document.activeElement && document.activeElement.getAttribute("aria-label")) || "" }));
    await page.close();
    const drawerOk = opened.expanded === "true" && opened.focus_inside_nav && closed.expanded === "false" && closed.focus === "Menu";
    const menuOk = menuOpen && !menuClosed.open && menuClosed.focus.startsWith("Account menu");
    report.keyboard.push({ check: "navigation drawer (a modal-like panel below 900px): Enter opens, focus moves in; Escape closes, focus returns to Menu", ok: drawerOk, detail: { toMenu, opened, closed } });
    report.keyboard.push({ check: "account menu: Enter opens, Escape closes, focus returns to its button", ok: menuOk, detail: { toAccount, menuOpen, menuClosed } });
    return { ok: drawerOk && menuOk, drawer: { toMenu, opened, closed }, account_menu: { toAccount, menuOpen, menuClosed } };
  });

  await flow("K3. a wizard with Tab/Enter: Add Reseller step 1 -> step 2 (nothing is created)", async () => {
    const page = staff.page;
    await goto(page, "/admin/partners/resellers/new");
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    const toField = await tabUntil(page, focusedLabelStarts, "Company or display name");
    await page.keyboard.type("Keyboard Only Co");
    const toNext = await tabUntil(page, focusedButtonIs, "Next");
    await page.keyboard.press("Enter");
    await waitForText(page, "Standard Reseller");
    const heading = await page.evaluate(() => (document.querySelector("form h2") || {}).innerText);
    const focusAfter = await page.evaluate(focusDescription);
    const r = { ok: toField > 0 && toNext > 0 && /Step 2/.test(heading || ""), tab_presses_to_first_field: toField, tab_presses_to_next: toNext, heading_after_enter: heading, focus_after_step_change: focusAfter };
    report.keyboard.push({ check: "wizard: Tab to fields and Next, Enter advances (Add Reseller)", ...r });
    return r;
  });

  await flow("K4. ARIA tabs move with the arrow keys (Super Admin partner detail)", async () => {
    const id = (ctx.reseller && ctx.reseller.id) || (ctx.wl && ctx.wl.aId);
    if (!id) return { ok: false, blocked: "no partner created" };
    const page = staff.page;
    await goto(page, `/admin/partners/${id}`);
    await page.waitForSelector('[role="tab"]');
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    const onTab = await tabUntil(page, () => document.activeElement && document.activeElement.getAttribute("role") === "tab");
    const selected = () => page.evaluate(() => (document.querySelector('[role="tab"][aria-selected="true"]') || {}).innerText);
    const first = await selected();
    await page.keyboard.press("ArrowRight");
    await sleep(600);
    const second = await selected();
    await page.keyboard.press("End");
    await sleep(600);
    const last = await selected();
    await page.keyboard.press("Home");
    await sleep(600);
    const home = await selected();
    const panel = await page.evaluate(() => !!document.querySelector('[role="tabpanel"]'));
    const r = { ok: onTab > 0 && first !== second && last !== second && home === first && panel, first, second, last, home, tabpanel_present: panel };
    report.keyboard.push({ check: "ARIA tabs: ArrowRight / End / Home", ...r });
    return r;
  });
}

// =========================================================== accessibility sweep ==

async function auditSweep(browser, staff) {
  await flow("12. accessibility sweep: organization, Super Admin and partner portal pages not already audited in a journey", async () => {
    const A = ctx.orgA;
    const done = [];
    const run = async (page, url, label) => {
      if (audited.has(label)) return;
      try {
        await visit(page, url, label);
        done.push(label);
      } catch (err) {
        report.notes.push(`audit sweep: ${label} not audited: ${err.message.slice(0, 160)}`);
      }
    };
    if (A && A.slug) {
      for (const [u, l] of [
        ["dashboard", "org dashboard (Organization A, all four)"],
        ["apps", "org My products (Organization A, all four)"],
        ["explore", "org Explore products (Organization A, all owned)"],
        ["orders", "org Orders"],
        ["settings/billing", "org Subscription & billing (Organization A)"],
        ["settings/members", "org settings: members"],
        ["settings/organization", "org settings: organization"],
      ]) await run(A.page, `/o/${A.slug}/${u}`, l);
    }
    const sp = staff.page;
    for (const [u, l] of [
      ["/admin", "admin overview /admin"],
      ["/admin/customers", "admin /admin/customers"],
      ["/admin/products", "admin /admin/products"],
      ["/admin/partners", "admin /admin/partners (overview)"],
      ["/admin/partners/resellers", "admin /admin/partners/resellers"],
      ["/admin/partners/white-label", "admin /admin/partners/white-label"],
      ["/admin/partners/customers", "admin /admin/partners/customers"],
      ["/admin/commissions", "admin /admin/commissions"],
    ]) await run(sp, u, l);
    if (A && A.orgId) await run(sp, `/admin/customers/${A.orgId}`, "admin /admin/customers/<Organization A>");
    const partnerId = ctx.wl && ctx.wl.aId;
    if (partnerId) {
      for (const t of ["overview", "customers", "products", "commissions", "branding", "domains", "users"]) await run(sp, `/admin/partners/${partnerId}?tab=${t}`, `admin partner detail (white-label partner A) / ${t}`);
    }
    if (ctx.wl && ctx.wl.aAdmin) {
      const pp = ctx.wl.aAdmin.page;
      for (const [u, l] of [
        [`/partner/${partnerId}`, "partner portal: overview (white-label partner A)"],
        [`/partner/${partnerId}/customers`, "partner portal: customers (white-label partner A)"],
        [`/partner/${partnerId}/products`, "partner portal: products"],
        [`/partner/${partnerId}/commissions`, "partner portal: commissions (white-label partner A)"],
        [`/partner/${partnerId}/white-label`, "partner portal: white-label (after activation)"],
        [`/partner/${partnerId}/branding`, "partner portal: branding"],
        [`/partner/${partnerId}/domains`, "partner portal: domains"],
      ]) await run(pp, u, l);
    }
    return { ok: true, audited_here: done.length };
  });
}

// ========================================================================= main ==

function summarize() {
  const failedFlows = report.flows.filter((f) => !f.ok);
  const axePages = report.pages.filter((p) => p.axe.length > 0);
  const focusPages = report.pages.filter((p) => p.noFocus.length > 0);
  const h1Pages = report.pages.filter((p) => p.h1.length !== 1);
  report.summary = {
    run: RUN,
    flows: `${report.flows.length - failedFlows.length}/${report.flows.length} passed`,
    failed_flows: failedFlows.map((f) => f.flow),
    pages_audited: report.pages.length,
    axe_pages_with_violations: axePages.length,
    axe_violations: axePages.map((p) => `${p.label}: ${p.axe.map((v) => `${v.id} (${v.impact}) x${v.nodes} e.g. ${v.target}`).join("; ")}`),
    pages_with_invisible_focus: focusPages.map((p) => `${p.label}: ${p.noFocus.join(", ")}`),
    pages_without_exactly_one_h1: h1Pages.map((p) => `${p.label}: ${JSON.stringify(p.h1)}`),
    csp_violations: report.csp.length,
    console_errors: report.errors.length,
    expected_refusals_in_journeys: report.expected.length,
    launches_recorded: report.launches.length,
    blocked_external_requests: report.blocked_requests.length,
    keyboard: report.keyboard.map((k) => `${k.ok ? "PASS" : "FAIL"} ${k.check}`),
    bugs: report.bugs.map((b) => b.title),
    screen_reader_pass: "NOT EXECUTED",
    matrix: Object.fromEntries(Object.entries(report.matrix).map(([row, cols]) => [row, Object.fromEntries(Object.entries(cols).map(([k, v]) => [k, v.status]))])),
  };
  report.finished = new Date().toISOString();
}

function save() {
  summarize();
  fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
}

(async () => {
  console.log(`Phase 12 browser check, run ${RUN}, against ${BASE} (edge 127.0.0.1:${EDGE_PORT})`);
  const browser = await newBrowser();
  const staff = { browser };
  try {
    await flow("setup + D-037: the Super Admin signs in with a password only; the control plane stays closed (UI gate, API 403 mfa_required) until a TOTP code is verified in this session", async () => {
      const { context, page } = await login(browser, STAFF_EMAIL, STAFF_PASSWORD);
      staff.context = context;
      staff.page = page;
      let enrolledNow = false;
      if (fs.existsSync(STAFF_TOTP_FILE)) staff.secret = fs.readFileSync(STAFF_TOTP_FILE, "utf8").trim();
      else {
        staff.secret = await enrollTotp(page);
        fs.writeFileSync(STAFF_TOTP_FILE, staff.secret);
        enrolledNow = true;
      }
      const landed = page.url();
      await waitForText(page, "Two-step verification is required", 20000).catch(() => {});
      const gateAtAdmin = await textVisible(page, "Two-step verification is required");
      if (gateAtAdmin) await audit(page, "admin MFA gate (Two-step verification is required)");
      const state = () =>
        page.evaluate(() => ({
          gate: document.body.innerText.includes("Two-step verification is required"),
          customer_list_shown: document.body.innerText.includes("Customer organizations"),
          code_field: !!document.querySelector('input[autocomplete="one-time-code"]'),
        }));
      await goto(page, "/admin/customers");
      await page.waitForFunction(() => /Two-step verification is required|Customer organizations/.test(document.body.innerText), { timeout: 20000 }).catch(() => {});
      const before = await state();
      const apiBefore = await withExpectedFailures(() => api(page, "GET", "/api/v1/admin/customers"));
      const answered = await answerPrompt(page, staff.secret, 10000);
      await page.waitForFunction(() => document.body.innerText.includes("Customer organizations"), { timeout: 25000 }).catch(() => {});
      const after = await state();
      const apiAfter = await api(page, "GET", "/api/v1/admin/customers");
      return {
        ok:
          gateAtAdmin &&
          before.gate &&
          before.code_field &&
          !before.customer_list_shown &&
          apiBefore.status === 403 &&
          (apiBefore.body || {}).code === "mfa_required" &&
          answered &&
          !after.gate &&
          after.customer_list_shown &&
          apiAfter.status === 200,
        landed,
        totp_enrolled_in_this_run: enrolledNow,
        gate_on_admin: gateAtAdmin,
        before_verification: before,
        api_before: { status: apiBefore.status, code: (apiBefore.body || {}).code },
        verified_through_the_gate_form: answered,
        after_verification: after,
        api_after: { status: apiAfter.status, rows: ((apiAfter.body || {}).results || []).length },
      };
    });
    // Whatever the check found, later journeys need a verified staff session.
    if (staff.page && (await api(staff.page, "GET", "/api/v1/admin/customers").catch(() => ({}))).status !== 200) {
      await goto(staff.page, "/admin");
      await answerPrompt(staff.page, staff.secret, 10000);
      await sleep(1500);
    }

    await flow("D-037b: a new sign-in needs its own verification (a second session, and again after signing out and in); the verified session stays verified", async () => {
      const second = await login(browser, STAFF_EMAIL, STAFF_PASSWORD);
      const gatedNow = async () => {
        await second.page.waitForFunction(() => /Two-step verification is required|Dashboard/.test(document.body.innerText), { timeout: 20000 }).catch(() => {});
        return textVisible(second.page, "Two-step verification is required");
      };
      const gate1 = await gatedNow();
      const api1 = await withExpectedFailures(() => api(second.page, "GET", "/api/v1/admin/customers"));
      const firstStillVerified = await api(staff.page, "GET", "/api/v1/admin/customers");
      const out = await api(second.page, "POST", "/api/v1/auth/logout", {});
      await signIn(second.page, STAFF_EMAIL, STAFF_PASSWORD);
      const gate2 = await gatedNow();
      const api2 = await withExpectedFailures(() => api(second.page, "GET", "/api/v1/admin/customers"));
      await second.context.close();
      return {
        ok: gate1 && api1.status === 403 && (api1.body || {}).code === "mfa_required" && firstStillVerified.status === 200 && gate2 && api2.status === 403 && (api2.body || {}).code === "mfa_required",
        second_session: { gate_shown: gate1, api: `${api1.status} ${(api1.body || {}).code}` },
        first_session_still_verified: firstStillVerified.status,
        sign_out: out.status,
        after_signing_in_again: { gate_shown: gate2, api: `${api2.status} ${(api2.body || {}).code}` },
      };
    });
    if (!staff.secret) throw new Error("no Super Admin session/second factor - nothing else can run");

    if (runs(0)) await journeyPublicAndStaffFacts(browser, staff);
    save();
    if (runs(8)) {
      let first = true;
      const only = (process.env.PRODUCTS_ONLY || "").split(",").filter(Boolean);
      for (const p of PRODUCTS.filter((x) => only.length === 0 || only.includes(x.key))) {
        const st = await productJourney(browser, staff, p, first);
        first = false;
        save();
        await st.context.close().catch(() => {});
      }
      if (process.env.SKIP_STORECARD !== "1") await journeyStoreCardTrial(browser);
      save();
    }
    if (runs(9)) {
      await journeyOrganizationA(browser);
      await journeyCsrf();
      save();
    }
    if (runs(10)) {
      await journeyReseller(browser, staff);
      save();
    }
    if (runs(11)) {
      await journeyWhiteLabel(browser, staff);
      save();
    }
    if (runs(12)) {
      await journeyKeyboard(browser, staff);
      await auditSweep(browser, staff);
    }
    const denied = ctx.reseller && ctx.reseller.deniedProject;
    if (denied) report.notes.push(`Step 10: selling Easy Project (not granted) - ${denied.ok ? "DENIED as required" : "NOT denied as required"}: form offers ${JSON.stringify(denied.sell_form_offers)}; server ${JSON.stringify(denied.server_refusal)}`);
  } catch (err) {
    console.error("CHECK CRASHED", err);
    report.notes.push(`CRASHED: ${err.stack}`);
  } finally {
    save();
    await browser.close().catch(() => {});
  }
  const s = report.summary;
  console.log(JSON.stringify(s, null, 2));
  report.flows.filter((f) => !f.ok).forEach((f) => console.log("FAILED FLOW:", JSON.stringify(f).slice(0, 1500)));
  report.csp.forEach((c) => console.log("CSP:", JSON.stringify(c)));
  report.errors.slice(0, 40).forEach((e) => console.log("ERROR:", JSON.stringify(e)));
  console.log(`report: ${OUT}`);
  process.exit(0);
})();
