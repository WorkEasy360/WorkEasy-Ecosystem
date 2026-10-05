// Real-browser verification of Phase 10 (the reseller and white-label consoles
// and the customer owner's activation, D-020) against the production build and a
// real backend. Same harness and rules as ../phase9_browser_check:
//   - CSP violations and console errors on every page;
//   - axe WCAG 2.1 A/AA, a visible keyboard focus indicator, exactly one h1;
//   - reflow (no horizontal scroll) at 1440, 1024, 768, 390 and 320 px;
// and five journeys, driven through the UI, reading emailed links from the
// backend's console email output (BACKEND_LOG) and "publishing" DNS by writing
// DNS_FILE (the scratch backend's resolver reads it):
//   1. Reseller creation (staff, the Add Reseller wizard)
//   2. Customer provisioning -> owner activation -> product access (the mandatory
//      one: D-020 is not closed until it passes)
//   3. White-label, including AAL2 on the domain step only
//   4. A combined partner (one account, both labels)
//   5. Termination (the partner loses access at once; the customer keeps login
//      and products)
// Not part of the repository's CI. A manual screen-reader pass is NOT part of it
// either: it is reported as NOT EXECUTED.
const puppeteer = require("puppeteer-core");
const crypto = require("crypto");
const fs = require("fs");
const zlib = require("zlib");

const BASE = process.env.BASE || "http://127.0.0.1:3999";
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const AXE = fs.readFileSync(process.env.AXE_PATH, "utf8");
const PASSWORD = "Browser-Test-Password-1!";
const NEW_PASSWORD = "Another-Browser-Password-2!";
const { BACKEND_LOG, DNS_FILE, STAFF_TOTP, BRAND_TOTP, SEED_RESELLER_ID, SEED_BRAND_ID, OUT } = process.env;
const WIDTHS = [1440, 1024, 768, 390, 320];
const WL_HOST = "app.wl-brand.com";
const COMBINED_HOST = "shop.wl-brand.com";

const report = { pages: [], csp: [], errors: [], expected: [], reflow: [], flows: [], notes: [] };
let expectingFailure = 0;
// Inside a journey a dangerous action is refused with 401 (step-up required) and
// answered, and a terminated partner is refused with 403: the browser logs each
// as "Failed to load resource". They are counted and listed as expected; on the
// audited pages (outside any journey) every console error still counts.
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
    if (m.text().startsWith("Failed to load resource") && (expectingFailure > 0 || (inJourney > 0 && refusal))) report.expected.push(line);
    else report.errors.push(line);
  });
  page.on("pageerror", (e) => report.errors.push(`${page.url()} :: pageerror ${e.message}`));
  return page;
}

async function withExpectedFailures(fn) {
  expectingFailure++;
  try {
    return await fn();
  } finally {
    expectingFailure--;
  }
}

async function signIn(page, email, password = PASSWORD) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle0" });
  await page.type('input[type="email"]', email);
  await page.type('input[type="password"]', password);
  await Promise.all([
    page.click('button[type="submit"]'),
    page.waitForNetworkIdle({ idleTime: 800, timeout: 20000 }).catch(() => {}),
  ]);
  await page
    .waitForFunction(() => !location.pathname.startsWith("/login") && location.pathname !== "/dashboard", { timeout: 20000 })
    .catch(() => {});
  await page.waitForNetworkIdle({ idleTime: 500, timeout: 15000 }).catch(() => {});
  const session = await page.evaluate(() => fetch("/api/v1/auth/session").then((r) => r.json()));
  if (!session.authenticated) throw new Error(`sign-in failed for ${email}`);
  return page;
}

async function login(browser, email, password = PASSWORD) {
  const context = await browser.createBrowserContext();
  const page = await newPage(context);
  await signIn(page, email, password);
  return { context, page };
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

// ----------------------------------------------------------- page interactions --

const norm = (s) => (s || "").replace(/\s+/g, " ").trim();

async function bodyText(page) {
  return page.evaluate(() => document.body.innerText);
}

async function waitForText(page, text, timeout = 15000) {
  await page.waitForFunction((t) => document.body.innerText.includes(t), { timeout }, text).catch(async () => {
    const shown = (await bodyText(page)).slice(0, 400).replace(/\n+/g, " | ");
    throw new Error(`text not found: "${text}" on ${page.url()} :: ${shown}`);
  });
}

async function textVisible(page, text) {
  return page.evaluate((t) => document.body.innerText.includes(t), text);
}

// Click the first visible button/link/radio whose text or aria-label contains ``text``.
async function clickText(page, text, { exact = false, within = "body", timeout = 8000 } = {}) {
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
  const shown = (await bodyText(page)).slice(0, 300).replace(/\n+/g, " | ");
  throw new Error(`nothing to click for "${text}" on ${page.url()} :: ${shown}`);
}

// The id of the input a label points at (or contains).
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
  for (let i = 0; i < 30 && !id; i++) {
    id = await fieldId(page, labelText);
    if (!id) await sleep(200);
  }
  if (!id) throw new Error(`no field labelled "${labelText}" on ${page.url()}`);
  const el = await page.$(`[id="${id}"]`);
  await el.click({ clickCount: 3 });
  await page.keyboard.press("Backspace");
  await el.type(value);
}

async function choose(page, labelText, value) {
  const id = await fieldId(page, labelText);
  if (!id) throw new Error(`no select labelled "${labelText}"`);
  await page.select(`[id="${id}"]`, value);
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

// RFC 6238 (SHA-1, 30 s, 6 digits) - what the authenticator app would show.
function totp(secret, at = Date.now()) {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(at / 1000 / 30)));
  const mac = crypto.createHmac("sha1", base32(secret)).update(counter).digest();
  const offset = mac[mac.length - 1] & 0xf;
  const value = (mac.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
  return String(value).padStart(6, "0");
}

// A code the server has not seen: it refuses to accept one code twice, so when
// the last one was for this 30-second window, wait for the next.
// The seed enrolled the two seeded authenticators moments before this starts, using
// a code from the current window - so those windows count as already used.
const lastWindow = {};
for (const seeded of [STAFF_TOTP, BRAND_TOTP]) if (seeded) lastWindow[seeded] = Math.floor(Date.now() / 30000);
async function freshCode(secret) {
  const window = () => Math.floor(Date.now() / 30000);
  while (lastWindow[secret] === window()) await sleep(1000);
  lastWindow[secret] = window();
  return totp(secret);
}

// A request the page itself makes, with its session and CSRF token.
async function api(page, method, url, body) {
  return page.evaluate(
    async ({ method, url, body }) => {
      const csrf = document.cookie.split("; ").find((c) => c.startsWith("w360_csrf="));
      const r = await fetch(url, {
        method,
        headers: {
          "content-type": "application/json",
          "x-csrftoken": csrf ? csrf.split("=")[1] : "",
          "idempotency-key": Math.random().toString(16).slice(2) + Date.now().toString(16),
        },
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

async function stepUpViaApi(page, secret) {
  const r = await api(page, "POST", "/api/v1/auth/mfa/step-up", { code: await freshCode(secret) });
  if (r.status !== 200) throw new Error(`step-up refused: ${r.status} ${JSON.stringify(r.body)}`);
}

// If the screen asked for a second factor, answer it; report whether it did.
async function answerPrompt(page, secret, wait = 3500) {
  const selector = 'input[autocomplete="one-time-code"]';
  const shown = await page.waitForSelector(selector, { timeout: wait, visible: true }).then(() => true).catch(() => false);
  if (!shown) return false;
  await page.type(selector, await freshCode(secret));
  await clickText(page, "Verify");
  await page.waitForFunction((s) => !document.querySelector(s), { timeout: 15000 }, selector).catch(() => {});
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

// --------------------------------------------------------------- emailed links --

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
  for (let i = 0; i < 60; i++) {
    const token = emailedLink(kind, after);
    if (token) return token;
    await sleep(250);
  }
  throw new Error(`no ${kind} link was emailed`);
}

function publishDns(name, values) {
  const current = fs.existsSync(DNS_FILE) ? JSON.parse(fs.readFileSync(DNS_FILE, "utf8") || "{}") : {};
  current[name] = values;
  fs.writeFileSync(DNS_FILE, JSON.stringify(current));
}

// ------------------------------------------------------------------- flow record --

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

// A real 32x32 PNG (an accent-coloured square), written without a library.
function makePng(path, [r, g, b]) {
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
  fs.writeFileSync(
    path,
    Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", ihdr), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]),
  );
}

// =================================================================== the journeys ==

const ctx = {}; // what earlier journeys made, for the later ones

// ---- 1. Reseller creation (staff, the Add Reseller wizard) ---------------------------
async function journeyResellerCreation(staff) {
  await flow("1. reseller creation: the Add Reseller wizard, review, confirm, step-up, progress", async () => {
    const page = staff.page;
    await visit(page, "/admin/partners/resellers/new", "wizard: step 1");
    await fill(page, "Company or display name", "Browser Reseller");
    const code = await page.evaluate(() => {
      const label = [...document.querySelectorAll("label")].find((l) => l.innerText.trim().startsWith("Code"));
      return label ? document.getElementById(label.htmlFor).value : "";
    });
    await fill(page, "Contact email", "contact@browser-reseller.test");
    await clickText(page, "Next");

    // 2. capabilities: the Standard Reseller preset is the default
    await waitForText(page, "Standard Reseller");
    const presetLabelled = await textVisible(page, "Customer management");
    await audit(page, "wizard: step 2 capabilities");
    await clickText(page, "Next");

    // 3. products
    await waitForText(page, "Example Desk");
    await clickText(page, "Example Desk", { within: "form" });
    await clickText(page, "Next");

    // 4. pricing: the server calculates; the browser only sends what was typed
    await waitForText(page, "Prices per month, calculated by the server");
    await fill(page, "Partner price for Team in GBP", "12.99");
    await page.keyboard.press("Tab");
    await waitForText(page, "£2.00"); // 14.99 list - 12.99 partner = 2.00 discount, computed by the server
    const figures = await bodyText(page);
    await audit(page, "wizard: step 4 pricing");
    await clickText(page, "Next");

    // 5. commission: a rate for this partner
    await waitForText(page, "Programme default");
    await clickText(page, "A rate for this partner");
    await fill(page, "Commission rate (%)", "15");
    await clickText(page, "Next");

    // 6. administrator
    await fill(page, "Administrator's email", "reseller-admin@browser.test");
    await clickText(page, "Next");

    // 7. review, server-backed, then confirm
    await waitForText(page, "Browser Reseller");
    await page.waitForFunction(() => [...document.querySelectorAll("button")].some((b) => b.innerText.includes("Create reseller") && !b.disabled), { timeout: 20000 });
    await audit(page, "wizard: step 7 review");
    const before = logSize();
    ctx.logMark = before;
    await clickText(page, "Create reseller");
    await waitForText(page, "Create Browser Reseller?");
    await audit(page, "wizard: confirmation");
    await clickText(page, "Confirm and create");
    const prompted = await answerPrompt(page, STAFF_TOTP, 20000);
    // The progress list finishes; the wizard then offers the new partner.
    await page.waitForFunction(() => /[Dd]one|created|Created/.test(document.body.innerText) && !document.body.innerText.includes("Creating…"), { timeout: 60000 }).catch(() => {});
    await sleep(1500);
    let id = (page.url().match(/admin\/partners\/([0-9a-f-]{36})/) || [])[1];
    if (!id) {
      const link = await page.evaluate(() => [...document.querySelectorAll("a")].map((a) => a.getAttribute("href")).find((h) => /\/admin\/partners\/[0-9a-f-]{36}/.test(h || "")));
      id = link && link.match(/([0-9a-f-]{36})/)[1];
    }
    if (!id) {
      const list = await api(page, "GET", "/api/v1/admin/partners/resellers?q=Browser%20Reseller");
      id = list.body && list.body.results && list.body.results[0] && list.body.results[0].id;
    }
    ctx.resellerId = id;
    ctx.resellerCode = code;
    const created = await api(page, "GET", `/api/v1/admin/partners/${id}/summary`);
    const s = created.body || {};
    const progress = Object.fromEntries((s.setup_progress || []).map((p) => [p.key, p.state]));
    return {
      ok: !!id && presetLabelled && figures.includes("£2.00") && s.partner && s.partner.status === "active" && progress.products === "complete" && progress.pricing === "complete" && progress.commission === "complete",
      partner: id,
      code,
      prompted,
      status: s.partner && s.partner.status,
      progress,
      commission_rule: s.commission_rule && s.commission_rule.rate_bp,
    };
  });

  await flow("1b. the new partner appears in Resellers and in the overview counts, with its label derived", async () => {
    const page = staff.page;
    await visit(page, "/admin/partners/resellers?q=Browser", "resellers: after create");
    await waitForText(page, "Browser Reseller");
    const text = await bodyText(page);
    const overview = await api(page, "GET", "/api/v1/admin/partners/overview");
    const cards = Object.fromEntries((overview.body.cards || []).map((c) => [c.key, c.count]));
    return { ok: text.includes("Reseller") && cards.active_resellers >= 2, cards };
  });

  await flow("1c. the administrator is emailed a one-time link and sets a password (the existing reset flow)", async () => {
    const token = await waitForLink("reset-password", ctx.logMark);
    const admin = await login2(staff.browser, token);
    ctx.reseller = admin;
    return { ok: true };
  });
}

// The partner administrator opens the emailed setup link in their own browser,
// chooses a password, signs in and enrols an authenticator (a partner's
// dangerous actions need one).
async function login2(browser, token) {
  const context = await browser.createBrowserContext();
  const page = await newPage(context);
  await page.goto(`${BASE}/reset-password#token=${token}`, { waitUntil: "networkidle0" });
  const fragmentGone = !(await page.evaluate(() => location.hash));
  await page.type('input[autocomplete="new-password"]', NEW_PASSWORD);
  const confirm = await page.$$('input[autocomplete="new-password"]');
  if (confirm[1]) await confirm[1].type(NEW_PASSWORD);
  await clickText(page, "Change password");
  await page.waitForNetworkIdle({ idleTime: 800, timeout: 20000 }).catch(() => {});
  await signIn(page, "reseller-admin@browser.test", NEW_PASSWORD);
  const secret = await enrollTotp(page);
  report.notes.push(`partner administrator: link fragment removed from the address bar = ${fragmentGone}`);
  return { context, page, secret, fragmentGone };
}

// ---- 2. Customer provisioning -> owner activation -> product access ------------------------
async function journeyCustomerActivation(staff) {
  const admin = ctx.reseller;
  const partnerId = ctx.resellerId;
  const ownerEmail = "journey-owner@browser.test";
  const customerName = "Journey Customer Ltd";

  await flow("2a. the partner provisions a customer (step-up on the click that needs it)", async () => {
    const page = admin.page;
    await visit(page, `/partner/${partnerId}/customers`, "partner portal: customers");
    ctx.customerLogMark = logSize();
    await fill(page, "Organization name", customerName);
    await fill(page, "Owner's email", ownerEmail);
    await clickText(page, "Create customer");
    const prompted = await answerPrompt(page, admin.secret, 6000);
    await waitForText(page, "Customer created");
    await waitForText(page, customerName);
    const text = await bodyText(page);
    return { ok: text.includes("Owner activation pending") || text.includes("Pending"), prompted, sees_pending: text.includes("pending") };
  });

  await flow("2b. staff see the customer as 'Owner activation pending' - honestly, never onboarded", async () => {
    const page = staff.page;
    await visit(page, `/admin/partners/customers?q=Journey`, "staff: customers (pending owner)");
    await waitForText(page, customerName);
    const table = await page.evaluate(() => document.querySelector("table") && document.querySelector("table").innerText);
    const leaks = /token|#token=|owner-invitations\/accept/i.test(await bodyText(page));
    return { ok: table.includes("Owner activation pending") && !leaks, leaks };
  });

  let ownerToken;
  await flow("2c. the owner's email carries one link, in the URL fragment, and no password", async () => {
    ownerToken = await waitForLink("owner-invitations/accept", ctx.customerLogMark);
    const log = fs.readFileSync(BACKEND_LOG, "utf8").slice(ctx.customerLogMark);
    const noPassword = !log.includes(PASSWORD) && !log.includes(NEW_PASSWORD);
    const tokenNotLoggedElsewhere = log.split(ownerToken).length - 1 <= 2; // the email body only (and its echo)
    return { ok: !!ownerToken && noPassword, tokenNotLoggedElsewhere };
  });

  const ownerContext = await staff.browser.createBrowserContext();
  ctx.ownerContext = ownerContext;
  let ownerSlug;
  await flow("2d. the owner opens the link in their own browser, sets a password, and becomes the owner", async () => {
    const page = await newPage(ownerContext);
    ctx.ownerPage = page;
    await page.goto(`${BASE}/owner-invitations/accept#token=${ownerToken}`, { waitUntil: "networkidle0" });
    await waitForText(page, `Activate ${customerName}`);
    const fragmentGone = !(await page.evaluate(() => location.hash));
    const named = (await bodyText(page)).includes("Reseller") || (await bodyText(page)).includes("Browser Reseller");
    await audit(page, "owner activation page");
    await fill(page, "Your name", "Journey Owner");
    await fill(page, "Choose a password", NEW_PASSWORD);
    await fill(page, "Confirm the password", NEW_PASSWORD);
    await clickText(page, "Set password and continue");
    await page.waitForFunction(() => location.pathname.startsWith("/o/"), { timeout: 30000 });
    await sleep(800);
    ownerSlug = page.url().match(/\/o\/([^/?#]+)/)[1];
    ctx.ownerSlug = ownerSlug;
    const session = await page.evaluate(() => fetch("/api/v1/auth/session").then((r) => r.json()));
    const orgs = await api(page, "GET", "/api/v1/organizations");
    const mine = (orgs.body.organizations || []).find((o) => o.slug === ownerSlug);
    return { ok: fragmentGone && session.authenticated && session.email === ownerEmail && mine && mine.is_owner === true, fragmentGone, is_owner: mine && mine.is_owner, named };
  });

  await flow("2e. the same link, opened again, is refused with one message (single use)", async () => {
    const context = await staff.browser.createBrowserContext();
    const page = await newPage(context);
    await withExpectedFailures(async () => {
      await page.goto(`${BASE}/owner-invitations/accept#token=${ownerToken}`, { waitUntil: "networkidle0" });
      await waitForText(page, "This link can't be used");
    });
    await audit(page, "owner activation page: used link");
    await context.close();
    return { ok: true };
  });

  await flow("2f. the customer can sign in again with the password they chose", async () => {
    const { context, page } = await login(staff.browser, ownerEmail, NEW_PASSWORD);
    ctx.ownerLogin = { context, page };
    return { ok: page.url().includes("/o/") || page.url().includes("/organizations") || page.url().includes("/dashboard") };
  });

  await flow("2g. the partner sells the customer a plan; the owner then holds the product", async () => {
    const page = admin.page;
    await visit(page, `/partner/${partnerId}/customers`, "partner portal: customers (activated)");
    await waitForText(page, customerName);
    const activated = (await bodyText(page)).includes("Activated") || (await bodyText(page)).includes("activated");
    // the sell form for the customer's row
    const row = await page.evaluateHandle((name) => [...document.querySelectorAll("tr")].find((tr) => tr.innerText.includes(name)), customerName);
    await row.asElement().evaluate((tr) => [...tr.querySelectorAll("button")].find((b) => b.innerText.includes("Sell a plan")).click());
    await sleep(800);
    const planId = await page.evaluate(() => {
      const select = [...document.querySelectorAll("select")].find((s) => [...s.options].some((o) => /Team/.test(o.text)));
      return select ? select.id : null;
    });
    if (!planId) throw new Error("no plan select in the sell form");
    const option = await page.$eval(`[id="${planId}"]`, (s) => [...s.options].find((o) => /Team/.test(o.text)).value);
    await page.select(`[id="${planId}"]`, option);
    await clickText(page, "Confirm sale");
    const prompted = await answerPrompt(page, admin.secret, 6000);
    await sleep(2500);
    const owner = ctx.ownerLogin.page;
    await owner.goto(`${BASE}/o/${ownerSlug}/apps`, { waitUntil: "networkidle0" }).catch(() => {});
    const apps = await api(owner, "GET", `/api/v1/orgs/${ownerSlug}/apps`);
    const desk = (apps.body.apps || []).find((a) => a.product_code === "exampledesk");
    const ownerText = await bodyText(owner);
    await audit(owner, "owner: my products");
    return {
      ok: !!desk && ["active", "no_seat", "seat_required"].includes(desk.state) && ownerText.includes("Example Desk"),
      activated_shown: activated,
      prompted,
      state: desk && desk.state,
      can_launch: desk && desk.can_launch,
    };
  });

  await flow("2h. staff now see 'Owner activated' for that customer", async () => {
    const page = staff.page;
    await visit(page, `/admin/partners/customers?q=Journey`, "staff: customers (activated owner)");
    await waitForText(page, customerName);
    const table = await page.evaluate(() => document.querySelector("table").innerText);
    return { ok: table.includes("Owner activated") };
  });
}

// ---- 3. White-label, including AAL2 on the domain step ------------------------------------
async function journeyWhiteLabel(staff) {
  const brandId = SEED_BRAND_ID;
  makePng("C:/Windows/Temp/w360-p10-logo.png", [11, 110, 79]);
  const { context, page } = await login(staff.browser, "seed-brand@browser.test");
  ctx.brand = { context, page };
  const fresh = await api(page, "GET", "/api/v1/auth/session");
  await flow("3. white-label: brand (no second factor), preview, domain (second factor), DNS, activation", async () => {
    await visit(page, `/partner/${brandId}/white-label`, "white-label wizard: step 1");
    const asked = () => page.$('input[autocomplete="one-time-code"]').then((h) => !!h);
    const nextEnabled = () =>
      page.waitForFunction(() => [...document.querySelectorAll("button")].some((b) => b.innerText.trim() === "Next" && !b.disabled), { timeout: 15000 });

    // step 1: brand identity, including a PNG logo. Nothing is stored yet.
    await fill(page, "Brand name", "Blair Brand Co");
    await fill(page, "Accent colour", "#0b6e4f");
    const fileInput = await page.$('input[type="file"]');
    if (fileInput) await fileInput.uploadFile("C:/Windows/Temp/w360-p10-logo.png");
    await sleep(600);
    const stored = await api(page, "GET", `/api/v1/partner/${brandId}/branding`);
    const nothingStoredByEditing = stored.body === null || stored.body.brand === null || stored.status === 404 || !stored.body.brand_name;
    await nextEnabled();
    await clickText(page, "Next");

    // step 2: the live preview, then approval saves the brand - no second factor for presentation
    await waitForText(page, "Preview - the layout and every security prompt stay Workeasy360's");
    const previewShown = true;
    await audit(page, "white-label wizard: preview");
    await clickText(page, "Save brand");
    await waitForText(page, "Brand saved");
    const promptedAtBrand = await asked();
    await nextEnabled();
    await clickText(page, "Next");

    // step 3: the domain request needs a second factor - this step, not the brand step
    await fill(page, "Hostname", WL_HOST);
    await clickText(page, "Request domain");
    const promptedAtDomain = await answerPrompt(page, BRAND_TOTP, 6000);
    await waitForText(page, "Domain requested");
    await audit(page, "white-label wizard: domain request");
    await nextEnabled();
    await clickText(page, "Next");

    // step 4: the DNS record to publish; nothing is proven until it is really there
    await waitForText(page, `_w360-verify.${WL_HOST}`);
    const value = await page.evaluate(() => {
      const dd = [...document.querySelectorAll("dd")].map((e) => e.innerText.trim());
      return dd.find((t) => t.startsWith("w360-verify=")) || null;
    });
    if (!value) throw new Error("the DNS record value was not shown");
    const nextBeforeProof = await page.evaluate(() => [...document.querySelectorAll("button")].find((b) => b.innerText.trim() === "Next").disabled);
    await clickText(page, "Check verification");
    await sleep(1500);
    const stillUnproven = !(await textVisible(page, "Ownership is verified"));
    publishDns(`_w360-verify.${WL_HOST}`, [value]);
    await clickText(page, "Check verification");
    await waitForText(page, "Ownership is verified", 20000);
    await audit(page, "white-label wizard: DNS verification");
    await nextEnabled();
    await clickText(page, "Next");

    // step 5: activation is its own, explicit action
    const publicBefore = await fetch(`${BASE}/api/v1/public/branding?host=${WL_HOST}`).then((r) => r.json());
    await clickText(page, "Activate the domain");
    const promptedAtActivate = await answerPrompt(page, BRAND_TOTP, 4000);
    await waitForText(page, "Live: this domain serves the brand", 20000);
    await audit(page, "white-label wizard: activation");
    const publicAfter = await fetch(`${BASE}/api/v1/public/branding?host=${WL_HOST}`).then((r) => r.json());
    return {
      ok:
        !promptedAtBrand && promptedAtDomain && previewShown && nextBeforeProof && stillUnproven && publicBefore.brand === null && !!publicAfter.brand && publicAfter.brand.brand_name === "Blair Brand Co",
      second_factor_asked: { brand_step: promptedAtBrand, domain_step: promptedAtDomain, activation_step: promptedAtActivate },
      editing_stored_nothing: nothingStoredByEditing,
      not_serving_before_activation: publicBefore.brand === null,
      unproven_before_the_record_existed: stillUnproven,
      serving_after_activation: !!(publicAfter.brand && publicAfter.brand.brand_name),
    };
  });

  await flow("3b. the partner's own domain serves its brand on the login page (production build, real hostname mapping)", async () => {
    const branded = await puppeteer.launch({
      executablePath: CHROME,
      headless: "new",
      // A partner's domain is served over TLS in production. Here the mapped
      // hostname is plain http, so Chrome must not upgrade the navigation (a typed
      // .com) and must treat the origin as secure - otherwise the production
      // CSP's upgrade-insecure-requests sends every asset request to https, where
      // this rig has no TLS. Neither flag touches the application.
      args: [
        "--no-sandbox",
        "--disable-features=HttpsUpgrades,HttpsFirstBalancedModeAutoEnable",
        `--unsafely-treat-insecure-origin-as-secure=http://${WL_HOST}`,
        `--host-resolver-rules=MAP ${WL_HOST} 127.0.0.1:3999`,
      ],
    });
    try {
      const p = await newPage(branded);
      await p.goto(`http://${WL_HOST}/login`, { waitUntil: "networkidle0", timeout: 30000 });
      await sleep(1200);
      const h1 = await p.evaluate(() => document.querySelector("h1").innerText);
      const accent = await p.evaluate(() => document.documentElement.style.getPropertyValue("--w360-brand"));
      const logo = await p.evaluate(() => {
        const img = document.querySelector("main img");
        return img ? { src: img.getAttribute("src"), loaded: img.complete && img.naturalWidth > 0 } : null;
      });
      const csp = await p.evaluate(() => window.__csp || []);
      await audit(p, "login page on the partner's domain");
      const plain = await newPage(branded);
      await plain.goto(`http://127.0.0.1:3999/login`, { waitUntil: "networkidle0", timeout: 30000 });
      await sleep(600);
      const plainH1 = await plain.evaluate(() => document.querySelector("h1").innerText);

      // An observation, not a pass/fail: does signing in *on the partner's own
      // domain* work? Django's CSRF origin check does not trust a partner domain
      // (debt D-035), so today it is refused - recorded here on every run so the
      // report says so instead of the brand page implying otherwise.
      let domainSignIn = "not attempted";
      try {
        // A fresh page: the one above has been through an axe run and 40 Tab presses.
        const q = await newPage(branded);
        await q.goto(`http://${WL_HOST}/login`, { waitUntil: "networkidle0", timeout: 30000 });
        await q.type('input[type="email"]', "seed-reseller@browser.test");
        await q.type('input[type="password"]', PASSWORD);
        await withExpectedFailures(async () => {
          await q.click('button[type="submit"]');
          await sleep(3500);
        });
        const text = await Promise.race([bodyText(q), sleep(5000).then(() => "")]);
        domainSignIn = !q.url().includes("/login")
          ? "works"
          : text.includes("Something went wrong")
            ? 'refused ("Something went wrong")'
            : "no session";
      } catch (err) {
        domainSignIn = `not determined (${err.message.slice(0, 60)})`;
      }
      report.notes.push(`sign-in on the partner's own domain (${WL_HOST}): ${domainSignIn}`);
      return {
        ok: h1 === "Blair Brand Co" && plainH1 === "Workeasy360" && (!logo || logo.src.startsWith("/api/v1/")) && csp.length === 0,
        sign_in_on_the_partner_domain: domainSignIn,
        h1,
        default_h1_on_the_platform_host: plainH1,
        accent: (accent || "").trim(),
        logo,
        csp_violations: csp.length,
      };
    } finally {
      await branded.close();
    }
  });
}

// ---- 4. A combined partner (one account, both labels) ---------------------------------------
async function journeyCombined(staff) {
  const partnerId = ctx.resellerId;
  await flow("4. combined partner: granting white-label capabilities to the reseller adds a label, not an account", async () => {
    const page = staff.page;
    await visit(page, `/admin/partners/${partnerId}?tab=users`, "staff: partner users & permissions");
    const before = await api(page, "GET", "/api/v1/admin/partners/resellers?q=Browser%20Reseller");
    const beforeLabels = ((before.body.results[0] || {}).experience || []).map((e) => e.key);
    for (const capability of ["branding.manage", "domain.manage"]) {
      await choose(page, "Grant a capability", capability);
      await fill(page, "Reason (recorded)", "Combined programme agreed for the browser check");
      await clickText(page, "Grant capability");
      await answerPrompt(page, STAFF_TOTP, 4000);
      await waitForText(page, "Capability granted", 15000);
      await sleep(600);
    }
    const after = await api(page, "GET", "/api/v1/admin/partners/resellers?q=Browser%20Reseller");
    const afterLabels = ((after.body.results[0] || {}).experience || []).map((e) => e.key);
    const listing = await api(page, "GET", "/api/v1/admin/partners/white-label?q=Browser");
    const inWhiteLabel = (listing.body.results || []).some((r) => r.id === partnerId);
    const count = await api(page, "GET", "/api/v1/admin/partners?limit=100");
    const accounts = ((count.body || {}).results || []).filter((p) => p.display_name === "Browser Reseller").length;
    // the partner's portal now offers both, from one sign-in
    const rp = ctx.reseller.page;
    await visit(rp, `/partner/${partnerId}`, "partner portal: combined");
    const nav = await rp.evaluate(() => [...document.querySelectorAll("nav a")].map((a) => a.innerText.trim()));
    return {
      ok: beforeLabels.join() === "reseller" && afterLabels.join() === "reseller,white_label" && inWhiteLabel && accounts === 1 && nav.includes("Customers") && nav.some((n) => /white-label/i.test(n)),
      labels_before: beforeLabels,
      labels_after: afterLabels,
      appears_in_white_label_list: inWhiteLabel,
      accounts,
      portal_nav: nav,
    };
  });

  await flow("4b. the combined partner brands and serves a domain of its own, alongside its customers", async () => {
    const rp = ctx.reseller.page;
    await stepUpViaApi(rp, ctx.reseller.secret);
    const saved = await api(rp, "PUT", `/api/v1/partner/${partnerId}/branding`, { brand_name: "Browser Shop", accent_color: "#7a1fa2" });
    const requested = await api(rp, "POST", `/api/v1/partner/${partnerId}/domains`, { hostname: COMBINED_HOST });
    const domainId = requested.body && requested.body.id;
    publishDns(`_w360-verify.${COMBINED_HOST}`, [requested.body.verification_record ? requested.body.verification_record.value : ""]);
    const verified = await api(rp, "POST", `/api/v1/partner/${partnerId}/domains/${domainId}/verify`, {});
    const activated = await api(rp, "POST", `/api/v1/partner/${partnerId}/domains/${domainId}/activate`, {});
    const served = await fetch(`${BASE}/api/v1/public/branding?host=${COMBINED_HOST}`).then((r) => r.json());
    ctx.combinedDomainId = domainId;
    return {
      ok: saved.status === 200 && requested.status < 300 && verified.status === 200 && activated.status === 200 && served.brand && served.brand.brand_name === "Browser Shop",
      statuses: [saved.status, requested.status, verified.status, activated.status],
      serving: served.brand && served.brand.brand_name,
    };
  });
}

// ---- 5. Termination --------------------------------------------------------------------------
async function journeyTermination(staff) {
  const partnerId = ctx.resellerId;
  await flow("5. termination: the partner loses access at once; its domain goes off the air", async () => {
    const page = staff.page;
    await visit(page, `/admin/partners/${partnerId}`, "staff: partner detail before termination", { auditIt: false });
    await clickText(page, "Terminate", { exact: true });
    await fill(page, "Reason for", "Programme membership ended for the browser check");
    await clickText(page, "Confirm", { exact: true });
    await answerPrompt(page, STAFF_TOTP, 4000);
    await waitForText(page, "Partner terminated", 20000);
    const summary = await api(page, "GET", `/api/v1/admin/partners/${partnerId}/summary`);
    const servedAfter = await fetch(`${BASE}/api/v1/public/branding?host=${COMBINED_HOST}`).then((r) => r.json());
    const rp = ctx.reseller.page;
    await visit(rp, `/partner/${partnerId}`, "partner portal: terminated");
    const banner = await bodyText(rp);
    const nav = await rp.evaluate(() => [...document.querySelectorAll("nav a")].map((a) => a.innerText.trim()));
    const customers = await api(rp, "GET", `/api/v1/partner/${partnerId}/customers`);
    const provisioning = await withExpectedFailures(() => api(rp, "POST", `/api/v1/partner/${partnerId}/customers`, { organization_name: "After Ltd", owner_email: "after@browser.test" }));
    const commissions = await api(rp, "GET", `/api/v1/partner/${partnerId}/commissions`);
    return {
      ok:
        summary.body.partner.status === "terminated" &&
        servedAfter.brand === null &&
        banner.includes("has ended") &&
        nav.every((n) => ["Overview", "Commissions", "Payouts"].includes(n)) &&
        customers.status === 403 &&
        provisioning.status === 403 &&
        commissions.status === 200,
      domain_off_air: servedAfter.brand === null,
      banner: banner.includes("has ended"),
      portal_nav: nav,
      customers_status: customers.status,
      provisioning_status: provisioning.status,
      commissions_status_history_readable: commissions.status,
    };
  });

  await flow("5b. the customer keeps their login, their organization and their product", async () => {
    const { context, page } = await login(staff.browser, "journey-owner@browser.test", NEW_PASSWORD);
    const orgs = await api(page, "GET", "/api/v1/organizations");
    const mine = (orgs.body.organizations || []).find((o) => o.slug === ctx.ownerSlug);
    const apps = await api(page, "GET", `/api/v1/orgs/${ctx.ownerSlug}/apps`);
    const desk = (apps.body.apps || []).find((a) => a.product_code === "exampledesk");
    await visit(page, `/o/${ctx.ownerSlug}/apps`, "owner after their partner ended: my products");
    const text = await bodyText(page);
    const list = await api(staff.page, "GET", `/api/v1/admin/partners/${ctx.resellerId}/customers?q=Journey`);
    const link = (list.body.results || [])[0] || {};
    await context.close();
    return {
      ok: !!mine && mine.is_owner === true && !!desk && ["active", "no_seat", "seat_required"].includes(desk.state) && text.includes("Example Desk") && link.link_status === "ended" && link.owner.activation === "activated",
      signed_in: true,
      is_owner: mine && mine.is_owner,
      product_state: desk && desk.state,
      relationship: link.link_status,
      owner_activation_still_recorded: link.owner && link.owner.activation,
    };
  });
}

// ================================================================ keyboard only ==

// Press Tab until the focused element satisfies ``predicate`` (run in the page).
async function tabUntil(page, predicate, arg, max = 120) {
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
  return el ? `${el.tagName}${el.id ? "#" + el.id.slice(0, 12) : ""} "${(el.innerText || el.getAttribute("aria-label") || "").slice(0, 40)}"` : "none";
};

async function journeyKeyboard(staff, browser) {
  await flow("K1. the Add Reseller wizard is operable with the keyboard alone (fields, Next, the next step)", async () => {
    const page = staff.page;
    await page.goto(`${BASE}/admin/partners/resellers/new`, { waitUntil: "networkidle0" });
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    const toField = await tabUntil(page, focusedLabelStarts, "Company or display name");
    await page.keyboard.type("Keyboard Only Co");
    const toNext = await tabUntil(page, focusedButtonIs, "Next");
    await page.keyboard.press("Enter");
    await waitForText(page, "Standard Reseller");
    const focusAfter = await page.evaluate(focusDescription);
    const skipLink = await page.evaluate(() => [...document.querySelectorAll("a")].some((a) => /skip to main/i.test(a.innerText)));
    return { ok: toField > 0 && toNext > 0 && skipLink, tab_presses_to_first_field: toField, tab_presses_to_next: toNext, skip_link_present: skipLink, focus_after_step_change: focusAfter };
  });

  await flow("K2. the partner sections are ARIA tabs that move with the arrow keys", async () => {
    const page = staff.page;
    await page.goto(`${BASE}/admin/partners/${SEED_RESELLER_ID}`, { waitUntil: "networkidle0" });
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
    return { ok: onTab > 0 && first !== second && last !== second && home === first && panel, first, second, last, home, tabpanel_present: panel };
  });

  await flow("K3. signing in is operable with the keyboard alone", async () => {
    const context = await browser.createBrowserContext();
    const page = await newPage(context);
    await page.goto(`${BASE}/login`, { waitUntil: "networkidle0" });
    await tabUntil(page, () => document.activeElement && document.activeElement.type === "email");
    await page.keyboard.type("seed-reseller@browser.test");
    await page.keyboard.press("Tab");
    await page.keyboard.type(PASSWORD);
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 20000 });
    await page.waitForNetworkIdle({ idleTime: 600, timeout: 15000 }).catch(() => {});
    const session = await page.evaluate(() => fetch("/api/v1/auth/session").then((r) => r.json()));
    await context.close();
    return { ok: session.authenticated === true };
  });
}

// =================================================================== the pages ==

async function auditPages(staff) {
  const reseller = SEED_RESELLER_ID;
  const staffPages = [
    ["/admin/partners", "staff: partners overview"],
    ["/admin/partners/resellers", "staff: resellers"],
    ["/admin/partners/resellers/new", "staff: add reseller (step 1)"],
    ["/admin/partners/white-label", "staff: white-label"],
    [`/admin/partners/white-label/${SEED_BRAND_ID}`, "staff: white-label wizard"],
    ["/admin/partners/customers", "staff: partner customers"],
    ["/admin/partners/products", "staff: products & pricing"],
    ["/admin/commissions", "staff: commissions"],
    ["/admin/payouts", "staff: payouts"],
    ...["overview", "customers", "products", "subscriptions", "commissions", "payouts", "branding", "domains", "users", "activity"].map((t) => [
      `/admin/partners/${reseller}?tab=${t}`,
      `staff: reseller detail / ${t}`,
    ]),
  ];
  for (const [url, label] of staffPages) await visit(staff.page, url, label);
  const partnerPages = [
    [`/partner/${ctx.resellerId || reseller}`, "partner portal: overview"],
    [`/partner/${ctx.resellerId || reseller}/customers`, "partner portal: customers"],
  ];
  if (ctx.brand) partnerPages.push([`/partner/${SEED_BRAND_ID}/white-label`, "partner portal: white-label"]);
  await visit(staff.page, "/login", "login", { auditIt: true });
  return { staffPages: staffPages.map((p) => p[0]), partnerPages };
}

async function reflowAll(browser, staff) {
  const staffUrls = [
    "/admin/partners",
    "/admin/partners/resellers",
    "/admin/partners/resellers/new",
    "/admin/partners/white-label",
    `/admin/partners/white-label/${SEED_BRAND_ID}`,
    "/admin/partners/customers",
    `/admin/partners/${SEED_RESELLER_ID}?tab=overview`,
    `/admin/partners/${SEED_RESELLER_ID}?tab=customers`,
    `/admin/partners/${SEED_RESELLER_ID}?tab=products`,
    `/admin/partners/${SEED_RESELLER_ID}?tab=commissions`,
    `/admin/partners/${SEED_RESELLER_ID}?tab=users`,
  ];
  await reflow(browser, staff.page, staffUrls);
  if (ctx.brand) await reflow(browser, ctx.brand.page, [`/partner/${SEED_BRAND_ID}`, `/partner/${SEED_BRAND_ID}/white-label`]);
  if (ctx.reseller) await reflow(browser, ctx.reseller.page, [`/partner/${ctx.resellerId}`, `/partner/${ctx.resellerId}/customers`]);
  const anon = await browser.createBrowserContext();
  const p = await newPage(anon);
  await p.goto(`${BASE}/login`, { waitUntil: "networkidle0" });
  await reflow(browser, p, ["/login", "/owner-invitations/accept"]);
}

// ===================================================================== main ==

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--no-sandbox"] });
  const staff = await login(browser, "staff@browser.test");
  staff.browser = browser;
  report.notes.push("staff second factor is answered where a screen asks for it (the seed enrolled it seconds earlier, and a code is refused if reused within its window)");

  await journeyResellerCreation(staff);
  if (ctx.reseller) await journeyCustomerActivation(staff);
  await journeyWhiteLabel(staff);
  if (ctx.reseller) await journeyCombined(staff);
  await auditPages(staff);
  await reflowAll(browser, staff);
  await journeyKeyboard(staff, browser);
  if (ctx.reseller) await journeyTermination(staff);

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
  const out = JSON.stringify(report, null, 2);
  if (OUT) fs.writeFileSync(OUT, out);
  console.log(JSON.stringify(report.summary, null, 2));
  failedFlows.forEach((f) => console.log("FAILED FLOW:", JSON.stringify(f)));
  axeFailures.forEach((p) => console.log("AXE:", p.label, JSON.stringify(p.axe)));
  focusFailures.forEach((p) => console.log("FOCUS:", p.label, JSON.stringify(p.noFocus)));
  h1Failures.forEach((p) => console.log("H1:", p.label, JSON.stringify(p.h1)));
  reflowFailures.forEach((r) => console.log("REFLOW:", JSON.stringify(r)));
  report.csp.forEach((c) => console.log("CSP:", c));
  report.errors.slice(0, 20).forEach((e) => console.log("ERROR:", e));
  await browser.close();
  process.exit(failedFlows.length || axeFailures.length || reflowFailures.length || report.csp.length || report.errors.length ? 1 : 0);
})().catch((err) => {
  console.error("CHECK CRASHED", err);
  process.exit(2);
});
