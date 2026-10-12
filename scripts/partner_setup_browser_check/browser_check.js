// Real-browser verification of the complete reseller + white-label setup,
// against the production build and a real backend. Same harness and rules as
// ../partner_center_browser_check:
//   - CSP violations and console errors on every page;
//   - axe WCAG 2.1 A/AA, a visible keyboard focus indicator, exactly one h1 -
//     on EVERY wizard step, the partner tabs and the customer-facing pages;
//   - reflow (no horizontal scroll) at 1440, 1024, 768, 390 and 320 px;
// and the brief's journeys, driven through the UI:
//   R. Super Admin -> Partners -> Resellers -> Add reseller: every step, Review,
//      Activate -> the partner's page shows everything that was typed
//   D. Save draft after step 1: "Setup incomplete", Activate waits
//   W. Super Admin -> Partners -> White-label -> choose an EXISTING partner:
//      brand, logo, colour, products, portal, communication, domain (DNS TXT,
//      verification, a separate activation), review, enable - no second partner
//   C. A combined partner: reseller created in R, white-label enabled later, then
//      an affiliate capability: one account wearing three labels
//   U. The customer of the white-label partner sees the brand, the welcome
//      message and the footer; a hidden product is not offered; a held one stays
//   S. A customer reaching the admin screens is refused
// Not part of CI. A manual screen-reader pass is NOT part of it: it is reported
// as NOT EXECUTED.
const puppeteer = require("puppeteer-core");
const crypto = require("crypto");
const fs = require("fs");
const zlib = require("zlib");

const BASE = process.env.BASE || "http://127.0.0.1:3999";
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const AXE = fs.readFileSync(process.env.AXE_PATH, "utf8");
const PASSWORD = "Browser-Test-Password-1!";
const { STAFF_TOTP, RESELLER_ID, AFFILIATE_ID, CUSTOMER_ORG, DNS_FILE, OUT } = process.env;
const WIDTHS = [1440, 1024, 768, 390, 320];
const WL_HOST = "app.seed-reseller-brand.com";

const report = { pages: [], csp: [], errors: [], expected: [], reflow: [], flows: [], notes: [] };
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
    const refusal = /status of (401|403|400|409)/.test(m.text());
    if (m.text().startsWith("Failed to load resource") && inJourney > 0 && refusal) report.expected.push(line);
    else report.errors.push(line);
  });
  page.on("pageerror", (e) => report.errors.push(`${page.url()} :: pageerror ${e.message}`));
  // The wizard warns before leaving unsaved work (a beforeunload prompt): leaving is what the journey means.
  page.on("dialog", (dialog) => {
    if (dialog.type() === "beforeunload") report.notes.push(`leave-page warning shown on ${page.url()}`);
    dialog.accept().catch(() => {});
  });
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
  await page.evaluate(() => window.scrollTo(0, 0));
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

const bodyText = (page) => page.evaluate(() => (document.querySelector("main") || document.body).innerText);

async function waitForText(page, text, timeout = 15000) {
  await page.waitForFunction((t) => document.body.innerText.includes(t), { timeout }, text).catch(async () => {
    const shown = (await bodyText(page)).slice(0, 500).replace(/\n+/g, " | ");
    throw new Error(`text not found: "${text}" on ${page.url()} :: ${shown}`);
  });
}

async function waitForPath(page, test, timeout = 20000) {
  await page.waitForFunction(test, { timeout }).catch(() => {
    throw new Error(`unexpected address ${page.url()}`);
  });
  await page.waitForNetworkIdle({ idleTime: 500, timeout: 15000 }).catch(() => {});
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

// ----- forms: found by their visible label, the way a person finds them -------------

async function fieldHandle(page, labelText, { within = "body", nth = 0, timeout = 8000 } = {}) {
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
      labelText,
      within,
      nth,
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

// A checkbox or radio inside a label whose text contains `text` (optionally within a fieldset legend).
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
    text,
    on,
    legend,
  );
  if (!done) throw new Error(`no enabled choice "${text}"${legend ? ` in "${legend}"` : ""} on ${page.url()}`);
}

async function stepHeading(page) {
  return page.evaluate(() => {
    const h = [...document.querySelectorAll("h2")].find((x) => /^Step \d+ of \d+/.test(x.innerText));
    return h ? h.innerText.trim() : "";
  });
}

async function waitStep(page, text, timeout = 15000) {
  await page.waitForFunction((t) => [...document.querySelectorAll("h2")].some((h) => /^Step \d+ of \d+/.test(h.innerText) && h.innerText.includes(t)), { timeout }, text).catch(async () => {
    throw new Error(`step "${text}" not reached; at "${await stepHeading(page)}" :: ${(await bodyText(page)).slice(0, 300).replace(/\n+/g, " | ")}`);
  });
  await sleep(250);
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

// Whatever happens next (a prompt, or the work finishing), answer prompts until `done` is true.
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


// Move to the next step: "Save & continue" on a step that saves, "Continue" on one that does not.
// A step that saves may ask for the second factor first; answer it, then wait for the next heading.
async function advance(page) {
  const before = await stepHeading(page);
  const label = await page.evaluate(() => {
    const buttons = [...document.querySelectorAll("main button")].filter((b) => !b.disabled);
    return buttons.some((b) => b.innerText.trim() === "Save & continue") ? "Save & continue" : "Continue";
  });
  await clickText(page, label, { exact: true });
  const deadline = Date.now() + 90000;
  while (Date.now() < deadline) {
    if ((await stepHeading(page)) !== before) return;
    if (await answerPrompt(page, STAFF_TOTP, 1500)) continue;
    await sleep(300);
  }
  const shown = (await bodyText(page)).slice(0, 300).split(String.fromCharCode(10)).join(" | ");
  throw new Error(`stayed on "${before}" after ${label}: ${shown}`);
}

async function advanceToReview(page) {
  for (let i = 0; i < 12; i++) {
    if ((await stepHeading(page)).includes("Review")) return;
    await advance(page);
  }
}

// ------------------------------------------------------------------ sign-in --

async function signInAs(page, mode, email) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle0" });
  await page.waitForFunction(() => document.body.innerText.includes("How would you like to sign in?"), { timeout: 15000 });
  await clickText(page, mode === "admin" ? "Administrator" : "User", { exact: true });
  await page.waitForSelector('input[type="email"]', { visible: true, timeout: 15000 });
  await page.type('input[type="email"]', email);
  await page.type('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 20000 });
  await page.waitForNetworkIdle({ idleTime: 600, timeout: 15000 }).catch(() => {});
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
    const at = staff.page ? await stepHeading(staff.page).catch(() => "") : "";
    report.flows.push({ flow: name, ok: false, at, error: err.message.slice(0, 300) });
    console.log("  AT", at);
    return { ok: false };
  } finally {
    inJourney--;
  }
}

function publishDns(name, values) {
  const current = fs.existsSync(DNS_FILE) ? JSON.parse(fs.readFileSync(DNS_FILE, "utf8") || "{}") : {};
  current[name] = values;
  fs.writeFileSync(DNS_FILE, JSON.stringify(current));
}

// A real PNG (a solid 24x24 square), made here so nothing is read from disk.
function makePng(path) {
  const crc = (buf) => {
    let c;
    let crcTable = [];
    for (let n = 0; n < 256; n++) {
      c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      crcTable[n] = c >>> 0;
    }
    let crcv = 0xffffffff;
    for (const b of buf) crcv = crcTable[(crcv ^ b) & 0xff] ^ (crcv >>> 8);
    return (crcv ^ 0xffffffff) >>> 0;
  };
  const chunk = (type, data) => {
    const len = Buffer.alloc(4);
    len.writeUInt32BE(data.length);
    const td = Buffer.concat([Buffer.from(type), data]);
    const c = Buffer.alloc(4);
    c.writeUInt32BE(crc(td));
    return Buffer.concat([len, td, c]);
  };
  const w = 24;
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0);
  ihdr.writeUInt32BE(w, 4);
  ihdr[8] = 8;
  ihdr[9] = 2;
  const row = Buffer.concat([Buffer.from([0]), Buffer.from(Array.from({ length: w }, () => [26, 86, 219]).flat())]);
  const raw = Buffer.concat(Array.from({ length: w }, () => row));
  fs.writeFileSync(path, Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", ihdr), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]));
}

const tabs = (page) => page.evaluate(() => [...document.querySelectorAll("[role=tab]")].map((t) => t.innerText.trim()));
const badges = (page) =>
  page.evaluate(() => {
    const list = document.querySelector('ul[aria-label="Derived from capabilities"]');
    return list ? [...list.querySelectorAll("li")].map((li) => li.innerText.trim()) : [];
  });

async function openTab(page, name) {
  await clickText(page, name, { exact: true, within: '[role="tablist"]' });
  await sleep(500);
  await page.waitForNetworkIdle({ idleTime: 400, timeout: 10000 }).catch(() => {});
}

async function partnerCount(page, code) {
  // How many rows All partners shows for this partner code/name: one account, ever.
  await page.goto(`${BASE}/admin/partners/all?q=${encodeURIComponent(code)}`, { waitUntil: "networkidle0" });
  await sleep(500);
  return page.evaluate((c) => [...document.querySelectorAll("tbody tr")].filter((r) => r.innerText.includes(c)).length, code);
}

// =================================================================== the journeys ==

const staff = {};
const made = {};

async function signInStaff(browser) {
  // The password-only session is answered 403 mfa_required until the code is
  // given: an expected refusal, not an error.
  inJourney++;
  staff.context = await browser.createBrowserContext();
  staff.page = await newPage(staff.context);
  await signInAs(staff.page, "admin", "staff@browser.test");
  await answerPrompt(staff.page, STAFF_TOTP, 15000);
  await waitForText(staff.page, "Partners");
  inJourney--;
}

async function journeyReseller() {
  await flow("R. Add reseller: nine steps, Review, Activate, then the partner's page", async () => {
    const page = staff.page;
    await visit(page, "/admin/partners/resellers", "resellers list");
    await clickText(page, "Add reseller");
    await waitStep(page, "Business details");
    const out = { steps: {} };

    // 1. Business details
    await audit(page, "reseller wizard 1: business");
    await type(page, "Company or partner name", "Acme Resell Co");
    out.code = await (await fieldHandle(page, "Code")).evaluate((el) => el.value);
    await type(page, "Business email", "hello@acme-resell.browser.test");
    await type(page, "Website (optional)", "https://acme-resell.browser.test");
    await type(page, "Business phone (optional)", "+44 20 7946 0001");
    await type(page, "Registration or company number", "12345678");
    await type(page, "Tax, GST or VAT number", "GB123456789");
    await type(page, "Address line 1", "1 High Street");
    await type(page, "City", "London");
    await type(page, "Postal or ZIP code", "EC1A 1BB");
    await choose(page, "Country", "United Kingdom");
    await clickText(page, "Save & continue");

    // 2. Contacts
    await waitStep(page, "Contacts");
    await audit(page, "reseller wizard 2: contacts");
    await type(page, "Primary contact name", "Priya Shah");
    await type(page, "Primary contact job title", "Director");
    await type(page, "Primary contact email", "priya@acme-resell.browser.test");
    await type(page, "Primary contact phone", "+44 20 7946 0002");
    await tick(page, "Same as primary contact", false, { legend: "Billing contact" });
    await type(page, "Billing contact name", "Bala Billing");
    await type(page, "Billing contact email", "bala@acme-resell.browser.test");
    await clickText(page, "Save & continue");

    // 3. Capabilities
    await waitStep(page, "Capabilities");
    await audit(page, "reseller wizard 3: capabilities");
    out.steps.capabilities = (await bodyText(page)).includes("Standard Reseller") && (await bodyText(page)).includes("Customer management");
    await clickText(page, "Save & continue");

    // 4. Products and trials
    await waitStep(page, "Products and trials");
    await tick(page, "Example Desk", true);
    await tick(page, "Example Books", true);
    await sleep(300);
    await audit(page, "reseller wizard 4: products");
    out.steps.trialInfo = (await bodyText(page)).includes("14");
    await clickText(page, "Save & continue");

    // 5. Pricing
    await waitStep(page, "Pricing");
    await tick(page, "Custom partner pricing", true);
    await waitForText(page, "List price");
    await type(page, "Partner price for Team", "12.00");
    await page.keyboard.press("Tab");
    // the discount (list 14.99 - partner 12.00 = 2.99) is the SERVER's figure, shown once it answers
    await waitForText(page, "£2.99");
    await audit(page, "reseller wizard 5: pricing");
    out.steps.pricingServerFigures = (await bodyText(page)).includes("£14.99") && (await bodyText(page)).includes("£2.99");
    await clickText(page, "Save & continue");

    // 6. Commission and payouts
    await waitStep(page, "Commission and payouts");
    await tick(page, "A rate for this partner", true);
    await type(page, "Commission rate (%)", "20");
    await page.keyboard.press("Tab");
    await waitForText(page, "£2,000.00");
    out.steps.example = (await bodyText(page)).includes("£10,000.00") && (await bodyText(page)).includes("£8,000.00");
    await choose(page, "Payout frequency", "Monthly");
    await type(page, "Payout contact email", "pay@acme-resell.browser.test");
    await audit(page, "reseller wizard 6: commission and payouts");
    await clickText(page, "Save & continue");

    // 7. Administrator and support
    await waitStep(page, "Administrator and support");
    await type(page, "First name", "Boss");
    await type(page, "Last name", "Person");
    await type(page, "Administrator's email", "boss@acme-resell.browser.test");
    await tick(page, "After the partner is activated", true);
    await type(page, "Support email", "help@acme-resell.browser.test");
    await type(page, "Internal notes", "Met at the browser check.");
    out.steps.noPassword = !(await page.evaluate(() => Boolean(document.querySelector('input[type="password"]'))));
    await audit(page, "reseller wizard 7: administrator and support");
    await clickText(page, "Save & continue");

    // 8. White-label (decided later in journey C)
    await waitStep(page, "White-label");
    await tick(page, "Not now", true);
    await audit(page, "reseller wizard 8: white-label");
    await clickText(page, "Save & continue");

    // 9. Review
    await waitStep(page, "Review");
    await waitForText(page, "Acme Resell Co");
    await sleep(800);
    await audit(page, "reseller wizard 9: review");
    const review = await bodyText(page);
    out.steps.review = ["hello@acme-resell.browser.test", "Priya Shah", "Bala Billing", "boss@acme-resell.browser.test", "Example Desk", "Example Books"].every((t) => review.includes(t));
    await clickText(page, "Activate reseller");
    await waitForText(page, "Confirm and activate");
    await audit(page, "reseller wizard: confirm panel");
    await clickText(page, "Confirm and activate");
    out.prompts = await untilDone(page, STAFF_TOTP, () => document.body.innerText.includes("Reseller created"), 120000);
    out.created = (await bodyText(page)).includes("Acme Resell Co has been set up and activated");
    await clickText(page, "Open Acme Resell Co");
    await waitForPath(page, () => /^\/admin\/partners\/[0-9a-f-]{36}$/.test(location.pathname));
    made.reseller = page.url().split("/").pop();
    made.resellerCode = out.code;

    // The partner's page shows what was typed.
    await waitForText(page, "Setup progress");
    out.badges = await badges(page);
    out.tabs = await tabs(page);
    await audit(page, "reseller detail: overview");
    const overview = await bodyText(page);
    out.overviewReady = !overview.includes("Setup incomplete");
    await openTab(page, "Business Details");
    const business = await page.evaluate(() => [...document.querySelectorAll("input,select,textarea")].map((e) => (e.tagName === "SELECT" ? e.options[e.selectedIndex].text : e.value)).join("|"));
    await audit(page, "reseller detail: business details");
    out.business = ["Acme Resell Co", "hello@acme-resell.browser.test", "https://acme-resell.browser.test", "12345678", "GB123456789", "1 High Street", "London", "United Kingdom", "Monthly", "help@acme-resell.browser.test", "Met at the browser check."].every((t) => business.includes(t));
    await openTab(page, "Contacts");
    const contacts = await page.evaluate(() => [...document.querySelectorAll("input")].map((e) => e.value).join("|"));
    await audit(page, "reseller detail: contacts");
    out.contacts = ["Priya Shah", "Director", "priya@acme-resell.browser.test", "Bala Billing", "bala@acme-resell.browser.test"].every((t) => contacts.includes(t));
    await openTab(page, "Products & Pricing");
    await sleep(600);
    await audit(page, "reseller detail: products and pricing");
    const products = await bodyText(page);
    out.products = products.includes("Example Desk") && products.includes("Example Books") && products.includes("£12.00") && products.includes("Customers may start the free trial");
    await openTab(page, "Users & Permissions");
    await audit(page, "reseller detail: users and permissions");
    const users = await bodyText(page);
    out.users = users.includes("boss@acme-resell.browser.test") && users.includes("Boss Person") && users.includes("Invitation sent");
    await openTab(page, "Activity");
    await audit(page, "reseller detail: activity");
    const activity = await bodyText(page);
    out.activity = ["Partner created", "Partner activated", "Capability granted", "Product granted", "Commission rate assigned", "Business details or contacts changed", "Pricing changed"].every((t) => activity.includes(t));
    out.ok =
      Object.values(out.steps).every(Boolean) &&
      out.created &&
      out.overviewReady &&
      out.badges.includes("Reseller") &&
      out.tabs.includes("Business Details") &&
      out.tabs.includes("Contacts") &&
      out.business &&
      out.contacts &&
      out.products &&
      out.users &&
      out.activity;
    return out;
  });
}

async function journeyDraft() {
  await flow("D. Save draft after step 1: setup incomplete, Activate waits", async () => {
    const page = staff.page;
    await visit(page, "/admin/partners/resellers/new", "reseller wizard (draft run)", { auditIt: false });
    await waitStep(page, "Business details");
    await type(page, "Company or partner name", "Draft Only Co");
    const code = await (await fieldHandle(page, "Code")).evaluate((el) => el.value);
    await clickText(page, "Save draft");
    const out = { code, prompts: await untilDone(page, STAFF_TOTP, () => /^\/admin\/partners\/[0-9a-f-]{36}$/.test(location.pathname), 90000) };
    await waitForText(page, "Setup progress");
    made.draft = page.url().split("/").pop();
    const text = await bodyText(page);
    out.incomplete = text.includes("Setup incomplete");
    out.listsMissing = ["Primary contact", "Business email", "Capabilities", "Partner administrator"].every((t) => text.includes(t));
    const activate = await page.evaluate(() => {
      const b = [...document.querySelectorAll("button")].find((x) => x.innerText.trim() === "Activate");
      return b ? { disabled: b.disabled } : null;
    });
    out.activateDisabled = Boolean(activate && activate.disabled);
    await audit(page, "draft partner: overview");
    out.ok = out.incomplete && out.listsMissing && out.activateDisabled;
    return out;
  });
}

async function journeyWhiteLabel() {
  await flow("W. White-label for an existing partner: brand, logo, colour, products, portal, communication, domain, review, enable", async () => {
    const page = staff.page;
    const out = { steps: {} };
    const before = await partnerCount(page, "Seed Reseller");
    const png = `${process.env.TEMP || "/tmp"}/wl-logo.png`;
    makePng(png);

    await visit(page, "/admin/partners/white-label", "white-label list");
    await clickText(page, "Set up white-label");
    await waitForPath(page, () => location.pathname === "/admin/partners/white-label/new");
    await waitStep(page, "Select partner");
    await audit(page, "white-label wizard 1: select partner");
    await type(page, "Search by partner name or code", "Seed Reseller");
    await clickText(page, "Search");
    await waitForText(page, "Seed Reseller");
    await tick(page, "Seed Reseller", true);
    out.steps.partnerRow = (await bodyText(page)).includes("Reseller");
    await advance(page);

    await waitStep(page, "Brand identity");
    await type(page, "Brand name", "Reseller Cloud");
    await type(page, "Short name", "RC");
    // the logo: a real PNG, through the real upload
    const file = await page.$('input[type="file"]');
    await file.uploadFile(png);
    await sleep(500);
    await type(page, "Primary colour", "#0b7a5c");
    await type(page, "Support contact name", "Reseller Help Desk");
    await type(page, "Support email", "help@seed-reseller.browser.test");
    await type(page, "Support phone", "+44 20 7946 0003");
    await type(page, "Support page", "https://seed-reseller.browser.test/help");
    await type(page, "Company website", "https://seed-reseller.browser.test");
    await audit(page, "white-label wizard 2: brand identity");
    await advance(page);

    await waitStep(page, "Portal appearance");
    for (const mode of ["Login", "Dashboard", "Product launcher", "Header"]) {
      await clickText(page, mode, { within: '[role="tablist"]' });
      await sleep(200);
    }
    out.steps.previewModes = true;
    await audit(page, "white-label wizard 3: appearance");
    await advance(page);

    await waitStep(page, "Products");
    await audit(page, "white-label wizard 4: products");
    const products = await bodyText(page);
    out.steps.products = products.includes("Example Desk") && products.includes("Example Books") && products.includes("never grants");
    // hide Example Books from the portal (display only)
    await tick(page, "Example Books", false);
    await untilDone(page, STAFF_TOTP, () => !document.body.innerText.includes("Saving"), 60000);
    await advance(page);

    await waitStep(page, "Customer portal");
    await type(page, "Welcome message", "Welcome to Reseller Cloud. We are glad you are here.");
    await type(page, "Footer text", "Reseller Cloud is operated by Seed Reseller Ltd.");
    await audit(page, "white-label wizard 5: customer portal");
    await advance(page);

    await waitStep(page, "Communication");
    await type(page, "Sender display name", "Reseller Cloud");
    await type(page, "Reply-to address", "support@seed-reseller.browser.test");
    await tick(page, "Use my brand on invitation emails", true);
    out.steps.honestEmails = (await bodyText(page)).includes("Not available yet");
    await audit(page, "white-label wizard 6: communication");
    await advance(page);

    await waitStep(page, "Custom domain");
    const requested = await page.evaluate(() => ![...document.querySelectorAll("label")].some((l) => l.innerText.startsWith("Host name to request")));
    if (!requested) {
      await type(page, "Host name to request", WL_HOST);
      await clickText(page, "Request domain");
      await untilDone(page, STAFF_TOTP, () => document.body.innerText.includes("Copy"), 90000);
    }
    const dns = await bodyText(page);
    out.steps.dnsShown = dns.includes("TXT") && dns.includes("Copy") && dns.includes("Host") && dns.includes("Value");
    const [recordType, recordName, recordValue] = await page.evaluate(() => [...document.querySelectorAll("dl code")].map((e) => e.innerText.trim()));
    out.record = { type: recordType, name: recordName, value: recordValue ? "(token)" : null };
    out.steps.statusBox = (await bodyText(page)).includes("Uses Workeasy360 secure sign-in");
    await audit(page, "white-label wizard 7: custom domain");
    // Not verified yet: activation is not even offered, and nothing activates by itself.
    const needsCheckEarly = await page.evaluate(() => [...document.querySelectorAll("button")].some((b) => b.innerText.includes("Check verification")));
    out.steps.noAutoActivate = !needsCheckEarly || !(await page.evaluate(() => [...document.querySelectorAll("button")].some((b) => b.innerText.includes("Activate domain") && !b.disabled)));
    const needsCheck = await page.evaluate(() => [...document.querySelectorAll("button")].some((b) => b.innerText.includes("Check verification")));
    if (needsCheck) {
      publishDns(recordName, [recordValue]);
      await clickText(page, "Check verification");
    }
    await untilDone(page, STAFF_TOTP, () => document.body.innerText.includes("Ownership is verified") || document.body.innerText.includes("Live: this domain shows the brand"), 60000);
    // Verification proves the name; the deliberate Activate is still waiting.
    out.steps.verifiedNotActive = await page.evaluate(() => [...document.querySelectorAll("button")].some((b) => b.innerText.includes("Activate domain") && !b.disabled));
    if (out.steps.verifiedNotActive) await clickText(page, "Activate domain");
    await untilDone(page, STAFF_TOTP, () => document.body.innerText.includes("Live: this domain shows the brand"), 60000);
    out.steps.domainActive = (await bodyText(page)).includes("Custom-domain branding: Active");
    await advance(page);

    await waitStep(page, "Login experience");
    await audit(page, "white-label wizard 8: login experience");
    out.steps.loginHonest = (await bodyText(page)).includes("Workeasy360");
    await advance(page);

    await waitStep(page, "Reseller connection");
    await audit(page, "white-label wizard 9: reseller connection");
    out.steps.derived = (await bodyText(page)).includes("Reseller") && (await bodyText(page)).includes("White-label") && (await bodyText(page)).includes("Affiliate");
    await advance(page);

    await waitStep(page, "Review");
    await audit(page, "white-label wizard 10: review");
    const review = await bodyText(page);
    out.steps.review = ["Reseller Cloud", "help@seed-reseller.browser.test", "Welcome to Reseller Cloud", WL_HOST].every((t) => review.includes(t));
    await clickText(page, "Enable white-label");
    await sleep(400);
    await type(page, "Reason", "Enabled in the browser check");
    await audit(page, "white-label wizard: enable confirm");
    await clickText(page, "Confirm");
    await untilDone(page, STAFF_TOTP, () => /white-label is enabled|White-label enabled|now white-label/i.test(document.body.innerText), 90000);

    // The same partner now wears both labels; no second partner exists.
    await page.goto(`${BASE}/admin/partners/${RESELLER_ID}`, { waitUntil: "networkidle0" });
    await waitForText(page, "Setup progress");
    out.badges = await badges(page);
    out.tabs = await tabs(page);
    out.partnerRowsBefore = before;
    out.partnerRowsAfter = await partnerCount(page, "Seed Reseller");
    out.ok =
      Object.values(out.steps).every(Boolean) &&
      out.badges.includes("Reseller") &&
      out.badges.includes("White-label") &&
      out.tabs.includes("Branding") &&
      out.partnerRowsBefore === 1 &&
      out.partnerRowsAfter === 1;
    return out;
  });
}

async function journeyCombined() {
  await flow("C. Combined: the reseller from R gains white-label, then affiliate: one account, three labels", async () => {
    const page = staff.page;
    const out = {};
    if (!made.reseller) {
      // a re-run of this journey alone: find the reseller journey R made
      await page.goto(`${BASE}/admin/partners/all?q=Acme`, { waitUntil: "networkidle0" });
      await clickText(page, "Acme Resell Co", { within: "table" });
      await waitForPath(page, () => /^\/admin\/partners\/[0-9a-f-]{36}$/.test(location.pathname));
      made.reseller = page.url().split("/").pop();
    }
    await page.goto(`${BASE}/admin/partners/white-label/${made.reseller}`, { waitUntil: "networkidle0" });
    await waitStep(page, "Brand identity");
    await type(page, "Brand name", "Acme Resell Brand");
    await advanceToReview(page);
    await waitStep(page, "Review");
    const canEnable = await page.evaluate(() => [...document.querySelectorAll("main button")].some((b) => b.innerText.trim() === "Enable white-label"));
    if (canEnable) {
      await clickText(page, "Enable white-label");
      await sleep(300);
      await type(page, "Reason", "Enabled in the combined journey");
      await clickText(page, "Confirm");
      await untilDone(page, STAFF_TOTP, () => /white-label is enabled|White-label enabled|now white-label/i.test(document.body.innerText), 90000);
    }

    await page.goto(`${BASE}/admin/partners/${made.reseller}?tab=users`, { waitUntil: "networkidle0" });
    await waitForText(page, "Grant a capability");
    await choose(page, "Grant a capability", "referral.create");
    await type(page, "Reason (recorded)", "Also an affiliate for the browser check");
    await clickText(page, "Grant capability");
    await untilDone(page, STAFF_TOTP, () => document.body.innerText.includes("Capability granted"), 60000);
    await page.goto(`${BASE}/admin/partners/${made.reseller}`, { waitUntil: "networkidle0" });
    await waitForText(page, "Setup progress");
    out.badges = await badges(page);
    out.rows = {
      all: await partnerCount(page, "Acme Resell Co"),
    };
    for (const scope of ["resellers", "affiliates"]) {
      await page.goto(`${BASE}/admin/partners/${scope === "resellers" ? "resellers" : "affiliates"}?q=Acme`, { waitUntil: "networkidle0" });
      await sleep(500);
      out.rows[scope] = await page.evaluate(() => [...document.querySelectorAll("tbody tr")].filter((r) => r.innerText.includes("Acme Resell Co")).length);
    }
    out.ok = ["Reseller", "White-label", "Affiliate"].every((b) => out.badges.includes(b)) && out.rows.all === 1 && out.rows.resellers === 1 && out.rows.affiliates === 1;
    return out;
  });
}

async function journeyAffiliateToWhiteLabel() {
  await flow("A. An existing affiliate becomes white-label: same account", async () => {
    const page = staff.page;
    const before = await partnerCount(page, "Seed Affiliate");
    await page.goto(`${BASE}/admin/partners/white-label/${AFFILIATE_ID}`, { waitUntil: "networkidle0" });
    await waitStep(page, "Brand identity");
    await type(page, "Brand name", "Affiliate Brand");
    await advanceToReview(page);
    await waitStep(page, "Review");
    await clickText(page, "Enable white-label");
    await sleep(300);
    await type(page, "Reason", "Enabled for the existing affiliate");
    await clickText(page, "Confirm");
    await untilDone(page, STAFF_TOTP, () => /white-label is enabled|White-label enabled|now white-label/i.test(document.body.innerText), 90000);
    await page.goto(`${BASE}/admin/partners/${AFFILIATE_ID}`, { waitUntil: "networkidle0" });
    await waitForText(page, "Setup progress");
    const labels = await badges(page);
    const after = await partnerCount(page, "Seed Affiliate");
    return { ok: labels.includes("Affiliate") && labels.includes("White-label") && before === 1 && after === 1, labels, before, after };
  });
}

async function journeyCustomer(browser) {
  await flow("U. The white-label partner's customer sees the brand; a hidden product is not offered", async () => {
    const context = await browser.createBrowserContext();
    const page = await newPage(context);
    await signInAs(page, "user", "customer@browser.test");
    await waitForPath(page, () => /^\/o\/[^/]+\/dashboard/.test(location.pathname));
    await sleep(800);
    const out = {};
    const dash = await bodyText(page);
    out.brand = dash.includes("RC") || dash.includes("Reseller Cloud");
    out.welcome = dash.includes("Welcome to Reseller Cloud. We are glad you are here.");
    out.footer = dash.includes("Reseller Cloud is operated by Seed Reseller Ltd.");
    out.support = dash.includes("help@seed-reseller.browser.test");
    await audit(page, "customer: branded dashboard");
    await page.goto(`${BASE}/o/${CUSTOMER_ORG}/explore`, { waitUntil: "networkidle0" });
    await sleep(500);
    const explore = await bodyText(page);
    out.deskOffered = explore.includes("Example Desk");
    out.booksHidden = !explore.includes("Example Books");
    await audit(page, "customer: explore");
    const storage = await page.evaluate(() => ({ local: Object.keys(localStorage), session: Object.keys(sessionStorage) }));
    out.noBrowserStorage = storage.local.length === 0 && storage.session.length === 0;
    out.ok = out.brand && out.welcome && out.footer && out.support && out.deskOffered && out.booksHidden && out.noBrowserStorage;
    await context.close();
    return out;
  });
}

async function journeyDenied(browser) {
  await flow("S. A customer reaching the admin wizards is refused", async () => {
    const context = await browser.createBrowserContext();
    const page = await newPage(context);
    const answers = [];
    page.on("response", (r) => {
      if (r.url().includes("/api/v1/admin/")) answers.push(`${r.status()} ${new URL(r.url()).pathname}`);
    });
    await signInAs(page, "admin", "customer@browser.test");
    await waitForText(page, "You don't have administrator access.");
    const out = {};
    for (const url of ["/admin/partners/resellers/new", "/admin/partners/white-label/new"]) {
      await page.goto(`${BASE}${url}`, { waitUntil: "networkidle0" });
      await sleep(500);
      const text = await bodyText(page);
      out[url] = { denied: text.includes("You don't have administrator access."), noForm: !(await page.evaluate(() => document.querySelector("form input"))) };
    }
    out.answers = [...new Set(answers)];
    out.ok = Object.values(out).filter((v) => v && v.denied !== undefined).every((v) => v.denied && v.noForm) && answers.every((a) => a.startsWith("403"));
    await context.close();
    return out;
  });
}

// =================================================================== responsive ==

async function reflowWizards(browser) {
  // Walk the wizards at every width, measuring after every step.
  for (const width of WIDTHS) {
    const page = await newPage(browser, width);
    await page.setCookie(...(await staff.page.cookies()));
    const measure = async (url, label) => {
      const w = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
      report.reflow.push({ width, url: `${url} :: ${label}`, ...w, ok: w.scroll <= w.inner + 1 });
    };
    // the reseller wizard: fill the minimum, step through all nine
    await page.goto(`${BASE}/admin/partners/resellers/new`, { waitUntil: "networkidle0" });
    await waitStep(page, "Business details");
    await measure("/admin/partners/resellers/new", "step 1");
    await type(page, "Company or partner name", `Reflow ${width}`);
    await type(page, "Business email", "r@reflow.browser.test");
    await choose(page, "Country", "United Kingdom");
    for (let guard = 0; guard < 12; guard++) {
      const heading = await stepHeading(page);
      if (heading.includes("Contacts")) {
        await type(page, "Primary contact name", "R Flow");
        await type(page, "Primary contact email", "rf@reflow.browser.test");
      }
      if (heading.includes("Administrator")) await type(page, "Administrator's email", "rf-admin@reflow.browser.test");
      await sleep(300);
      await measure("/admin/partners/resellers/new", heading || "step");
      if (heading.includes("Review")) break;
      await clickText(page, "Save & continue");
      await sleep(500);
    }
    // the white-label wizard
    await page.goto(`${BASE}/admin/partners/white-label/${RESELLER_ID}`, { waitUntil: "networkidle0" });
    await sleep(500);
    await measure(`/admin/partners/white-label/${RESELLER_ID}`, "step 2");
    for (let i = 3; i <= 10; i++) {
      await advance(page).catch(() => {});
      await sleep(300);
      await measure(`/admin/partners/white-label/${RESELLER_ID}`, (await stepHeading(page)) || `step ${i}`);
    }
    for (const url of [
      "/admin/partners/white-label/new",
      `/admin/partners/${RESELLER_ID}`,
      `/admin/partners/${RESELLER_ID}?tab=business`,
      `/admin/partners/${RESELLER_ID}?tab=contacts`,
      `/admin/partners/${RESELLER_ID}?tab=products`,
      `/admin/partners/${RESELLER_ID}?tab=users`,
      `/admin/partners/${RESELLER_ID}?tab=activity`,
    ]) {
      await page.goto(`${BASE}${url}`, { waitUntil: "networkidle0" });
      await sleep(400);
      await measure(url, "page");
    }
    await page.close();
  }
}

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--no-sandbox"] });
  const only = (process.env.ONLY || "").split(",").filter(Boolean);
  const want = (key) => only.length === 0 || only.includes(key);
  await signInStaff(browser);
  if (want("R")) await journeyReseller();
  if (want("D")) await journeyDraft();
  if (want("W")) await journeyWhiteLabel();
  if (want("C")) await journeyCombined();
  if (want("A")) await journeyAffiliateToWhiteLabel();
  if (want("U")) await journeyCustomer(browser);
  if (want("S")) await journeyDenied(browser);
  if (want("X")) await reflowWizards(browser);

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
