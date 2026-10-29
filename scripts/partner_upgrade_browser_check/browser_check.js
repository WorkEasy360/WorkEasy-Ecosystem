// Real-browser verification of the partner upgrade (ADR-049), against the production
// build and a real backend. Same harness and rules as ../partner_experience_browser_check:
//   - CSP violations and console errors on every audited page;
//   - axe WCAG 2.1 A/AA, a visible keyboard focus indicator, exactly one h1;
//   - reflow (no horizontal scroll) at 1440, 1024, 768, 390 and 320 px;
// and the brief's journeys, driven through the UI with the partner's own sign-in:
//   A. an Affiliate upgrades to RESELLER on the same login; staff approve; the same
//      email and password open the Reseller dashboard (same partner account)
//   B. an Affiliate upgrades straight to WHITE-LABEL (staff ask for more first); the
//      same login opens the White-label dashboard
//   C. before approval an Affiliate is refused the Reseller / White-label addresses,
//      edited local storage, forged request bodies and every reseller / staff API call
//   D. the Affiliate's referral, commission and payout history survives (Reseller)
//   E. ... and survives the White-label upgrade
//   F. a Reseller upgrades to WHITE-LABEL on the same login
//   S. what the Super Admin console shows afterwards
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

const report = { pages: [], csp: [], errors: [], expected: [], reflow: [], flows: [], notes: [], evidence: [] };
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


async function reflowHere(page, label) {
  for (const width of WIDTHS) {
    await page.setViewport({ width, height: 900 });
    await sleep(250);
    const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    report.reflow.push({ url: label, width, over });
  }
  await page.setViewport({ width: 1280, height: 900 });
}


// ------------------------------------------------------------------ what the upgrade check knows --

const SEED = {
  one: "affiliate.one@browser.test",
  two: "affiliate.two@browser.test",
  three: "affiliate.three@browser.test",
  four: "affiliate.four@browser.test",
  reseller: "reseller.one@browser.test",
};

const AFFILIATE_MENU = ["Dashboard", "My Products", "Referral Links", "Referral Codes", "Campaigns", "Marketing Materials", "Leads & Referrals", "Conversions", "Commissions", "Payouts", "Reports & Analytics", "Profile & Settings", "Support"];
const AFFILIATE_ONLY = ["My Products", "Referral Links", "Referral Codes", "Campaigns", "Marketing Materials", "Leads & Referrals", "Conversions", "Profile & Settings"];
const RESELLER_MENU = ["Dashboard", "Customers", "Products & Plans", "Pricing & Margins", "Sales & Orders", "Subscriptions", "Commissions", "Payouts", "Sub-Resellers", "Referrals", "Team & Permissions", "Reports & Analytics", "Support", "Settings"];
const WHITE_LABEL_MENU = ["Dashboard", "Brand Settings", "Custom Domain", "Website Branding", "Products & Plans", "Pricing & Plans", "Customers", "Subscriptions", "Orders & Billing", "Commissions", "Payouts", "Referrals", "Team & Permissions", "Reports & Analytics", "Support", "Settings"];

const sameSet = (a, b) => JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());
const lower = (s) => String(s).toLowerCase();
const untilTextI = (page, text, timeout, what) =>
  until(page, (t) => document.body.innerText.toLowerCase().includes(t), lower(text), timeout, what || `"${text}"`);

const portalOf = async (page) => {
  const r = await apiGet(page, "/api/v1/partner/portal");
  expectThat(r.status === 200, `portal answered ${r.status}`);
  return r.body.partners[0];
};

const putJson = (page, path, body) =>
  page.evaluate(
    async (path, body) => {
      const csrf = (document.cookie.match(/(?:^|; )(?:__Host-)?w360_csrf=([^;]*)/) || [])[1];
      const r = await fetch(path, {
        method: "PUT",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf ? decodeURIComponent(csrf) : "" },
        body: JSON.stringify(body || {}),
      });
      const text = await r.text();
      let json = null;
      try { json = JSON.parse(text); } catch { json = text.slice(0, 200); }
      return { status: r.status, body: json };
    },
    path,
    body,
  );

const notGranted = (r) => !(r.status >= 200 && r.status < 300);

/** What the server says a partner holds right now, kept in the report. */
const record = async (page, tag) => {
  const me = await portalOf(page);
  report.evidence.push({
    flow: tag,
    partner_id: me.partner_id,
    landing: me.landing,
    default_experience: me.default_experience,
    workspaces: me.workspaces.map((w) => w.key),
    capabilities: [...me.capabilities].sort(),
  });
  return me;
};

const deniedWorkspace = async (page, url, label) => {
  await page.goto(`${BASE}${url}`, { waitUntil: "networkidle0" });
  await untilTextI(page, "You don't have access to the", 20000);
  expectThat(!(await sidebarLabels(page)).includes("Customers"), `${label}: ${url} drew a commercial menu`);
};

// ------------------------------------------------------------------ the wizard, through the UI --

async function startFromDashboard(page, target, tag) {
  const label = target === "white_label" ? "White-label" : "Reseller";
  await clickText(page, `Upgrade to ${label}`, { exact: true });
  await untilTextI(page, "What happens", 20000);
  const sensitive = await page.evaluate(() => document.querySelectorAll('input[type="password"], input[autocomplete="new-password"], input[autocomplete="current-password"]').length);
  expectThat(sensitive === 0, `${tag}: the upgrade page asks for a password`);
  await audit(page, `${tag} upgrade intro`);
  await reflowHere(page, `${tag} upgrade intro`);
  await clickText(page, `Start your ${label} request`, { exact: true });
  await untilTextI(page, "Step 1 of", 20000);
}

async function stepNext(page, n) {
  await clickText(page, "Save and continue", { exact: true });
  await untilTextI(page, `Step ${n} of`, 30000, `step ${n}`);
}

const STEP_HANDLERS = {
  Business: async (page, spec) => {
    await type(page, "Phone", spec.phone);
    await type(page, "Legal name", spec.legal);
    await type(page, "Tax / GST / VAT number", "GB123456789");
    await type(page, "Billing contact name", "Bea Billing");
    await type(page, "Billing contact email", spec.billing);
  },
  Brand: async (page, spec) => {
    await type(page, "Brand name", spec.brand);
    await type(page, "Primary brand colour", "#1a73e8");
    await type(page, "Secondary colour", "#34a853");
    await type(page, "Support email", spec.billing);
    await type(page, "Support phone", "+44 20 7946 0001");
  },
  Products: async (page, spec) => {
    await tick(page, spec.product, true, { legend: "Products you want to sell" });
  },
  Pricing: async (page) => {
    await tick(page, "I would like to discuss my pricing", true);
    await type(page, "Notes for the review", "Volume discount please");
  },
  Commercial: async (page) => {
    await type(page, "Customers you expect", "40");
    await type(page, "People on your team", "3");
    await type(page, "How will you manage your customers?", "Through the CRM");
  },
  Domain: async (page, spec) => {
    await type(page, "Domain you want to use", spec.domain);
  },
};

/** Walk the wizard from Step 1 to Review (without sending), auditing each step. */
async function walkWizard(page, labels, spec, tag) {
  for (let i = 0; i < labels.length; i++) {
    const label = labels[i];
    await untilTextI(page, `Step ${i + 1} of ${labels.length}: ${label}`, 30000, `the ${label} step`);
    if (label === "Review") break;
    if (i === 0) await page.waitForSelector('input[type="tel"]', { visible: true, timeout: 20000 });
    await STEP_HANDLERS[label](page, spec);
    await audit(page, `${tag} step ${label}`);
    if (label === "Business" || label === "Products") await reflowHere(page, `${tag} step ${label}`);
    await stepNext(page, i + 2);
  }
}

async function sendForReview(page, tag) {
  await untilTextI(page, "Before you send it", 30000);
  const text = await bodyText(page);
  expectThat(!/Still needed/.test(text), `${tag}: the review still lists something needed :: ${text.slice(0, 300)}`);
  await audit(page, `${tag} step Review`);
  await reflowHere(page, `${tag} step Review`);
  await clickText(page, "Send for review", { exact: true });
  await untilTextI(page, "Pending approval", 30000);
  await audit(page, `${tag} pending`);
}

const RESELLER_STEPS = ["Business", "Products", "Pricing", "Commercial", "Review"];
const WHITE_LABEL_STEPS = ["Business", "Brand", "Products", "Pricing", "Domain", "Review"];

// ------------------------------------------------------------------ main --

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--no-sandbox"] });
  const fresh = () => browser.createBrowserContext();

  // The persistent Super Admin session that approves every request.
  const staff = { context: await fresh(), signedIn: false };
  staff.page = await newPage(staff.context);
  const staffReady = async () => {
    if (staff.signedIn) return;
    await staffSignIn(staff.page);
    staff.signedIn = true;
  };

  /** A partner signs in with the email and password it has always had. */
  const partnerPage = async (email) => {
    const context = await fresh();
    const page = await newPage(context);
    await partnerSignIn(page, email, PASSWORD);
    return { context, page };
  };

  /** Staff open the partner's Upgrade panel and decide the open request. */
  async function staffDecide(partnerId, action, { note = "", reference = "", tag }) {
    await staffReady();
    const page = staff.page;
    await page.goto(`${BASE}/admin/partners/${partnerId}?tab=users`, { waitUntil: "networkidle0", timeout: 30000 });
    await untilTextI(page, "Current partner level", 30000);
    await untilTextI(page, "Decision", 30000);
    await audit(page, `${tag} admin Upgrade panel`);
    await reflowHere(page, `${tag} admin Upgrade panel`);
    if (action === "approve") {
      await tick(page, "Approve", true);
      if (reference) await type(page, "Commercial reference", reference);
      if (note) await type(page, "Note (optional)", note);
      await clickText(page, "Approve upgrade", { exact: true });
      await until(
        page,
        () => {
          const dts = [...document.querySelectorAll("dt")];
          const dt = dts.find((d) => d.innerText.trim() === "Upgrade status");
          return !!dt && dt.nextElementSibling && dt.nextElementSibling.innerText.toLowerCase().includes("approved");
        },
        null,
        90000,
        "the approval to be recorded",
      );
    } else if (action === "request_info") {
      await tick(page, "Ask for more information", true);
      await type(page, "Note to the partner", note);
      await clickText(page, "Send back to the partner", { exact: true });
      await until(
        page,
        () => {
          const dt = [...document.querySelectorAll("dt")].find((d) => d.innerText.trim() === "Upgrade status");
          return !!dt && dt.nextElementSibling && /profile required/i.test(dt.nextElementSibling.innerText);
        },
        null,
        90000,
        "the request to be sent back",
      );
    }
  }

  /** The partner asks, fills the wizard and sends the request - all through the UI. */
  async function askThroughUi(page, target, spec, tag) {
    await startFromDashboard(page, target, tag);
    const steps = target === "white_label" ? WHITE_LABEL_STEPS : RESELLER_STEPS;
    await walkWizard(page, steps, spec, tag);
    await sendForReview(page, tag);
  }

  const historyOf = async (page, id) => {
    const overview = await apiGet(page, `/api/v1/partner/${id}/affiliate/overview?range=90d`);
    const balance = await apiGet(page, `/api/v1/partner/${id}/balance`);
    expectThat(overview.status === 200 && balance.status === 200, `history reads answered ${overview.status}/${balance.status}`);
    return {
      links: overview.body.links.map((l) => [l.id, l.code, l.clicks, l.conversions]),
      balances: balance.body.balances || balance.body,
    };
  };

  const ids = {}; // email -> partner id, and the history seen before an upgrade
  const before = {};

  // ---------------------------------------------------------------- A. affiliate -> reseller
  await flow("A An Affiliate upgrades to Reseller on the same login; the same email and password open the Reseller dashboard", async () => {
    const { context, page } = await partnerPage(SEED.one);
    expectThat(new URL(page.url()).pathname === "/partner/affiliate/dashboard", `landed on ${page.url()}`);
    const me = await record(page, "A before");
    ids[SEED.one] = me.partner_id;
    const labels = await sidebarLabels(page);
    expectThat(sameSet(labels, AFFILIATE_MENU), `affiliate menu: ${labels.join(" | ")}`);
    await untilTextI(page, "Grow Your Partnership", 20000);
    const text = await bodyText(page);
    expectThat(/Upgrade to Reseller/.test(text) && /Upgrade to White-label/.test(text), "both upgrade buttons are offered to an Affiliate");
    await audit(page, "A affiliate dashboard with Grow Your Partnership");
    await reflowHere(page, "A affiliate dashboard");
    before[SEED.one] = await historyOf(page, me.partner_id);
    expectThat(before[SEED.one].links.length >= 1, "the affiliate has a referral link");

    await askThroughUi(page, "reseller", { phone: "+44 20 7946 0958", legal: "Alpha Affiliates Ltd", billing: "billing@alpha.example.com", product: "Easy CRM" }, "A");

    // Asking changed nothing: still an affiliate, still refused everything a reseller does.
    const still = await portalOf(page);
    expectThat(still.landing === "affiliate" && sameSet(still.workspaces.map((w) => w.key), ["affiliate"]), `after asking: ${JSON.stringify(still.workspaces)}`);
    expectThat(notGranted(await apiGet(page, `/api/v1/partner/${me.partner_id}/customers`)), "a pending request opened the customers API");
    await page.goto(`${BASE}/partner/reseller/dashboard`, { waitUntil: "networkidle0" });
    await untilTextI(page, "You don't have access to the Reseller workspace", 20000);
    await context.close();

    // Staff approve it, on the server, through the console.
    await staffDecide(me.partner_id, "approve", { note: "Agreed on the call", reference: "CONTRACT-2026-17", tag: "A" });

    // Sign in again with the SAME email and password.
    const again = await partnerPage(SEED.one);
    const path = new URL(again.page.url()).pathname;
    expectThat(path === "/partner/reseller/dashboard", `the same login landed on ${path}`);
    const reseller = await record(again.page, "A after");
    expectThat(reseller.partner_id === me.partner_id, "a different partner account opened");
    expectThat(reseller.landing === "reseller", `landing ${reseller.landing}`);
    const menu = await sidebarLabels(again.page);
    expectThat(sameSet(menu, RESELLER_MENU), `reseller menu: ${menu.join(" | ")}`);
    expectThat(!AFFILIATE_ONLY.some((l) => menu.includes(l)), "the affiliate navigation is still the main sidebar");
    const grow = await bodyText(again.page);
    expectThat(/Upgrade to White-label/.test(grow) && !/Upgrade to Reseller/.test(grow), "a Reseller must be offered White-label and not Reseller");
    await audit(again.page, "A reseller dashboard after the upgrade");
    await reflowHere(again.page, "A reseller dashboard");
    expectThat((await apiGet(again.page, `/api/v1/partner/${me.partner_id}/customers`)).status === 200, "the reseller customers API stayed closed after approval");
    again.page.__id = me.partner_id;
    ids.resellerSession = again;
    return `landed on ${path}; ${menu.length} entries`;
  });

  // ---------------------------------------------------------------- D. history survives the reseller upgrade
  await flow("D The Affiliate's referral, commission and payout history survives the Reseller upgrade", async () => {
    const session = ids.resellerSession;
    if (!session) throw new Error("journey A did not finish");
    const page = session.page;
    const id = page.__id;
    const after = await historyOf(page, id);
    expectThat(JSON.stringify(after) === JSON.stringify(before[SEED.one]), `history changed: ${JSON.stringify(before[SEED.one])} -> ${JSON.stringify(after)}`);
    for (const path of ["commissions", "payouts", "affiliates"]) {
      const r = await apiGet(page, `/api/v1/partner/${id}/${path}`);
      expectThat(r.status === 200, `${path} answered ${r.status}`);
    }
    // The Affiliate workspace is one click away, with its menu and its link intact.
    const portal = await portalOf(page);
    expectThat(portal.workspaces.map((w) => w.key).includes("affiliate"), "the Affiliate workspace was lost");
    await page.goto(`${BASE}/partner/affiliate/referral-links`, { waitUntil: "networkidle0" });
    await untilTextI(page, "Referral Links", 20000);
    const labels = await sidebarLabels(page);
    expectThat(sameSet(labels, AFFILIATE_MENU), `affiliate workspace menu: ${labels.join(" | ")}`);
    const code = before[SEED.one].links[0][1];
    expectThat((await bodyText(page)).includes(code), "the referral code is no longer listed");
    await audit(page, "D the affiliate workspace after the upgrade");
    await session.context.close();
    return `${before[SEED.one].links.length} link(s), balances and payouts unchanged`;
  });

  // ---------------------------------------------------------------- B. affiliate -> white-label, directly, with a request for more
  await flow("B An Affiliate upgrades straight to White-label (staff ask for more first); the same login opens the White-label dashboard", async () => {
    const { context, page } = await partnerPage(SEED.two);
    const me = await record(page, "B before");
    ids[SEED.two] = me.partner_id;
    await deniedWorkspace(page, "/partner/white-label/dashboard", "B before asking");
    await page.goto(`${BASE}/partner/affiliate/dashboard`, { waitUntil: "networkidle0" });
    await askThroughUi(page, "white_label", { phone: "+44 20 7946 0102", legal: "Bravo Affiliates Ltd", billing: "billing@bravo.example.com", product: "Easy CRM", brand: "Bravo Cloud", domain: "app.bravo-cloud.example.org" }, "B");
    // Asked, not granted: the address and the customer API are still refused.
    await deniedWorkspace(page, "/partner/white-label/dashboard", "B pending");
    expectThat(notGranted(await apiGet(page, `/api/v1/partner/${me.partner_id}/customers`)), "B: a pending request opened the customers API");
    await context.close();

    // Staff send it back; the partner sees what was asked, edits and sends again.
    await staffDecide(me.partner_id, "request_info", { note: "Please confirm the VAT number", tag: "B" });
    const back = await partnerPage(SEED.two);
    await untilTextI(back.page, "Please confirm the VAT number", 20000);
    await audit(back.page, "B dashboard with a request for more information");
    await clickText(back.page, "Continue your request", { exact: true });
    await untilTextI(back.page, "Workeasy360 asks for more information", 20000);
    for (let n = 2; n <= 6; n++) await stepNext(back.page, n).catch(() => {});
    await untilTextI(back.page, "Before you send it", 30000);
    await clickText(back.page, "Send for review", { exact: true });
    await untilTextI(back.page, "Pending approval", 30000);
    await back.context.close();

    await staffDecide(me.partner_id, "approve", { reference: "CONTRACT-2026-18", tag: "B" });

    const again = await partnerPage(SEED.two);
    const path = new URL(again.page.url()).pathname;
    expectThat(path === "/partner/white-label/dashboard", `the same login landed on ${path}`);
    const wl = await record(again.page, "B after");
    expectThat(wl.partner_id === me.partner_id && wl.landing === "white_label", `portal ${JSON.stringify(wl.landing)}`);
    const menu = await sidebarLabels(again.page);
    expectThat(sameSet(menu, WHITE_LABEL_MENU), `white-label menu: ${menu.join(" | ")}`);
    expectThat(!AFFILIATE_ONLY.some((l) => menu.includes(l)), "the affiliate navigation is still the main sidebar");
    const text = await bodyText(again.page);
    expectThat(!/Grow Your Partnership/.test(text) && !/Upgrade to/.test(text), "White-label was offered an upgrade");
    expectThat(/Bravo Cloud/.test(text) || /brand/i.test(text), "the white-label dashboard shows no brand status");
    await audit(again.page, "B white-label dashboard after the upgrade");
    await reflowHere(again.page, "B white-label dashboard");
    await again.context.close();
    return `landed on ${path}; ${menu.length} entries`;
  });

  // ---------------------------------------------------------------- C. an Affiliate cannot upgrade itself
  await flow("C Before approval an Affiliate is denied the Reseller and White-label addresses, stored state and API calls", async () => {
    const { context, page } = await partnerPage(SEED.three);
    const me = await portalOf(page);
    const id = me.partner_id;
    for (const url of ["/partner/reseller/dashboard", "/reseller/dashboard", "/partner/white-label/dashboard", "/white-label/dashboard"]) {
      await page.goto(`${BASE}${url}`, { waitUntil: "networkidle0" });
      await untilTextI(page, "You don't have access to the", 20000);
      expectThat(!(await sidebarLabels(page)).includes("Customers"), `${url} drew a Reseller menu`);
    }
    await page.goto(`${BASE}/partner/affiliate/users`, { waitUntil: "networkidle0" });
    await untilTextI(page, "not part of the Affiliate workspace", 20000);

    // Editing the browser's stored state decides nothing.
    await page.evaluate(() => {
      localStorage.setItem("partner_type", "reseller");
      localStorage.setItem("primary_partner_workspace", "reseller");
      sessionStorage.setItem("partner_type", "reseller");
    });
    await page.goto(`${BASE}/partner/reseller/dashboard`, { waitUntil: "networkidle0" });
    await untilTextI(page, "You don't have access to the Reseller workspace", 20000);
    await page.goto(`${BASE}/partner/affiliate/dashboard`, { waitUntil: "networkidle0" });
    expectThat(sameSet(await sidebarLabels(page), AFFILIATE_MENU), "stored state changed the sidebar");
    await audit(page, "C reseller address refused");

    // The reseller's customer APIs and every staff command: never 2xx.
    const reads = ["customers", "catalog", "plans", "agency", "price-books"];
    for (const path of reads) {
      const r = await apiGet(page, `/api/v1/partner/${id}/${path}`);
      expectThat(notGranted(r), `GET ${path} answered ${r.status}`);
    }
    const posts = [
      [`/api/v1/partner/${id}/customers`, { organization_name: "Smuggled Ltd", owner_email: "x@example.com" }],
      [`/api/v1/admin/partners/${id}/experience`, { action: "grant", experience: "reseller" }],
      [`/api/v1/admin/partners/${id}/capabilities`, { capability: "customer.provision", reason: "Please let me in" }],
      [`/api/v1/admin/partners/${id}/product-grants`, { product_code: "exampledesk" }],
      [`/api/v1/admin/partners/${id}/upgrades/00000000-0000-4000-8000-000000000000/decision`, { action: "approve" }],
    ];
    for (const [path, body] of posts) {
      const r = await post(page, path, body);
      expectThat(notGranted(r), `POST ${path} answered ${r.status}`);
    }

    // Asking is allowed and grants nothing; a forged request body is refused.
    const asked = await post(page, `/api/v1/partner/${id}/upgrade/requests`, { experience: "reseller" });
    expectThat(asked.status === 201, `asking answered ${asked.status}`);
    const forged = await putJson(page, `/api/v1/partner/${id}/upgrade/requests/${asked.body.id}/sections/capabilities`, { data: { capabilities: ["customer.provision"] }, expected_version: asked.body.version });
    expectThat(forged.status === 400, `a forged section answered ${forged.status}`);
    const ineligible = await post(page, `/api/v1/partner/${id}/upgrade/requests`, { experience: "affiliate" });
    expectThat(ineligible.status >= 400, `asking for "affiliate" answered ${ineligible.status}`);
    expectThat(notGranted(await apiGet(page, `/api/v1/partner/${id}/customers`)), "an open request opened the customers API");
    const entry = await portalOf(page);
    expectThat(entry.landing === "affiliate" && entry.workspaces.length === 1, `an open request changed the workspaces: ${JSON.stringify(entry.workspaces)}`);
    await page.goto(`${BASE}/partner/reseller/users`, { waitUntil: "networkidle0" });
    await untilTextI(page, "You don't have access to the Reseller workspace", 20000);
    await context.close();
    return "addresses, stored state, forged body and API calls all refused";
  });

  // ---------------------------------------------------------------- E. history survives the white-label upgrade
  await flow("E An Affiliate with history upgrades to White-label; its referral, commission and payout history survives", async () => {
    const { context, page } = await partnerPage(SEED.four);
    const me = await record(page, "E before");
    ids[SEED.four] = me.partner_id;
    const seen = await historyOf(page, me.partner_id);
    expectThat(seen.links.length >= 1, "affiliate.four has no referral link");
    await askThroughUi(page, "white_label", { phone: "+44 20 7946 0104", legal: "Delta Affiliates Ltd", billing: "billing@delta.example.com", product: "Easy CRM", brand: "Delta Cloud", domain: "" }, "E");
    await context.close();
    await staffDecide(me.partner_id, "approve", { tag: "E" });
    const again = await partnerPage(SEED.four);
    expectThat(new URL(again.page.url()).pathname === "/partner/white-label/dashboard", `landed on ${again.page.url()}`);
    await record(again.page, "E after");
    const after = await historyOf(again.page, me.partner_id);
    expectThat(JSON.stringify(after) === JSON.stringify(seen), `history changed: ${JSON.stringify(seen)} -> ${JSON.stringify(after)}`);
    for (const path of ["commissions", "payouts", "affiliates"]) {
      expectThat((await apiGet(again.page, `/api/v1/partner/${me.partner_id}/${path}`)).status === 200, `${path} is closed after the upgrade`);
    }
    // The Referral Links page lists each code as text (the dashboard shows the link in an input).
    await again.page.goto(`${BASE}/partner/affiliate/referral-links`, { waitUntil: "networkidle0" });
    await untilTextI(again.page, "Referral Links", 20000);
    expectThat((await bodyText(again.page)).includes(seen.links[0][1]), "the referral link vanished");
    await audit(again.page, "E affiliate workspace after the white-label upgrade");
    await again.context.close();
    return "history identical before and after";
  });

  // ---------------------------------------------------------------- F. reseller -> white-label
  await flow("F A Reseller upgrades to White-label on the same login", async () => {
    const { context, page } = await partnerPage(SEED.reseller);
    expectThat(new URL(page.url()).pathname === "/partner/reseller/dashboard", `landed on ${page.url()}`);
    const me = await record(page, "F before");
    await untilTextI(page, "Grow Your Partnership", 20000);
    const text = await bodyText(page);
    expectThat(/Upgrade to White-label/.test(text) && !/Upgrade to Reseller/.test(text), "a Reseller must be offered White-label and not Reseller");
    await deniedWorkspace(page, "/partner/white-label/dashboard", "F before asking");
    expectThat(notGranted(await apiGet(page, `/api/v1/partner/${me.partner_id}/branding`)), "F: the brand API answered a Reseller");
    await page.goto(`${BASE}/partner/reseller/dashboard`, { waitUntil: "networkidle0" });
    await askThroughUi(page, "white_label", { phone: "+44 20 7946 0105", legal: "Echo Resellers Ltd", billing: "billing@echo.example.com", product: "Easy CRM", brand: "Echo Cloud", domain: "shop.echo-cloud.example.org" }, "F");
    await context.close();
    await staffDecide(me.partner_id, "approve", { tag: "F" });
    const again = await partnerPage(SEED.reseller);
    const path = new URL(again.page.url()).pathname;
    expectThat(path === "/partner/white-label/dashboard", `the same login landed on ${path}`);
    const wl = await record(again.page, "F after");
    expectThat(wl.partner_id === me.partner_id && wl.landing === "white_label", `landing ${wl.landing}`);
    const menu = await sidebarLabels(again.page);
    expectThat(menu.includes("Brand Settings") && menu.includes("Custom Domain") && menu.includes("Customers"), `white-label menu: ${menu.join(" | ")}`);
    expectThat(!/Upgrade to/.test(await bodyText(again.page)), "White-label was offered an upgrade");
    await audit(again.page, "F white-label dashboard after Reseller -> White-label");
    await again.context.close();
    return `landed on ${path}`;
  });

  // ---------------------------------------------------------------- S. what the console shows
  await flow("S The console lists the requests' outcome: level, status and history", async () => {
    await staffReady();
    const page = staff.page;
    await page.goto(`${BASE}/admin/partners/${ids[SEED.one]}?tab=users`, { waitUntil: "networkidle0" });
    await untilTextI(page, "Current partner level", 30000);
    const text = (await bodyText(page)).toLowerCase();
    expectThat(text.includes("reseller") && text.includes("approved"), "the approved Reseller upgrade is not shown");
    expectThat(text.includes("contract-2026-17"), "the commercial reference is not shown to staff");
    await audit(page, "S admin panel after approval");
    const queue = await apiGet(page, "/api/v1/admin/partner-upgrades?status=approved");
    expectThat(queue.status === 200 && queue.body.items.length >= 3, `queue answered ${queue.status} with ${queue.body.items && queue.body.items.length}`);
    const list = await apiGet(page, "/api/v1/admin/partners/resellers?scope=all&limit=100");
    expectThat(list.status === 200, `partner list answered ${list.status}`);
    return `${queue.body.items.length} approved requests`;
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
  for (const f of report.flows) console.log(`${f.ok ? "PASS" : "FAIL"}  ${f.name}${f.ok ? `  [${f.detail}]` : `\n      ${f.detail}`}`);
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
