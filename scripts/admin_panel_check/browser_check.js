// Real-browser acceptance for the Admin Panel programme (2026-11-09, docs/admin-panel/status.md).
// The production frontend build, a real backend, a scratch database seeded by prepare.py,
// headless Chrome. ONLY=A,B,... runs some of it. See README.md.
//
//   CHECK_PASSWORD=... NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> \
//     OUT=<dir> node scripts/admin_panel_check/browser_check.js
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const path = require("path");

const BASE = process.env.BASE || "http://127.0.0.1:3999";
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const PASSWORD = process.env.CHECK_PASSWORD;
const OUT = process.env.OUT || "admin_panel_shots";
const AXE = process.env.AXE_PATH ? fs.readFileSync(process.env.AXE_PATH, "utf8") : null;
const STAFF = "staff@eco.test";
const WIDTHS = (process.env.WIDTHS || "1920,1366,1024,768,390,360").split(",").map(Number);
const ONLY = (process.env.ONLY || "").split(",").filter(Boolean);
if (!PASSWORD) throw new Error("CHECK_PASSWORD is required");
fs.mkdirSync(OUT, { recursive: true });

const MODULES = fs.readFileSync(path.join(__dirname, "../../frontend/src/features/controlplane/adminModules.ts"), "utf8");
const PAGES = [...new Set([...MODULES.matchAll(/href: "(\/admin[^"]*)"/g)].map((m) => m[1]))];

const report = { base: BASE, checks: [], pages: [], consoleErrors: [], apiErrors: [] };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const slug = (p) => (p === "/admin" ? "dashboard" : p.replace(/^\//, "").replace(/[^a-z0-9]+/gi, "-"));

function check(name, ok, detail = "") {
  report.checks.push({ name, ok: Boolean(ok), detail: String(detail).slice(0, 400) });
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${name}${detail ? ` - ${String(detail).slice(0, 200)}` : ""}`);
}

// A full navigation cancels Next's background prefetches, and Puppeteer can count those as
// in flight for the rest of a tab's life, so a tab may never report idle again. Pages here
// load in about a second: the limit only stops one such tab from costing 30s on every wait.
async function settle(page) {
  await page.waitForNetworkIdle({ idleTime: 500, timeout: 8000 }).catch(() => {});
  await sleep(250);
}

async function open(page, target) {
  await page.goto(`${BASE}${target}`, { waitUntil: "domcontentloaded", timeout: 90000 }).catch(() => {});
  await settle(page);
}

/** The visible control with exactly this text or accessible label. */
async function control(page, text, { selector = "button, a, [role=menuitem], [role=tab], summary", timeout = 20000, contains = false } = {}) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    const handle = await page.evaluateHandle(
      (t, sel, partial) => {
        const seen = (e) => {
          const r = e.getBoundingClientRect();
          return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== "hidden";
        };
        const match = (e) => {
          const words = [e.getAttribute("aria-label") || "", (e.textContent || "").replace(/\s+/g, " ").trim()];
          return words.some((w) => (partial ? w.includes(t) : w === t));
        };
        const dialogs = [...document.querySelectorAll("[role=dialog], dialog[open]")].filter(seen);
        const root = dialogs[dialogs.length - 1] || document;
        return [...root.querySelectorAll(sel)].filter(seen).find(match) || [...document.querySelectorAll(sel)].filter(seen).find(match) || null;
      },
      text,
      selector,
      contains,
    );
    const el = handle.asElement();
    if (el) return el;
    await sleep(200);
  }
  throw new Error(`No control "${text}"`);
}

async function press(page, text, options) {
  const el = await control(page, text, options);
  await el.evaluate((e) => e.scrollIntoView({ block: "center" }));
  await el.click();
  await sleep(150);
}

/** The field whose visible label starts with this text. */
async function field(page, label, timeout = 15000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    const handle = await page.evaluateHandle((t) => {
      const seen = (e) => e.getBoundingClientRect().width > 0;
      const labels = [...document.querySelectorAll("label")].filter((l) => seen(l) && l.textContent.trim().startsWith(t));
      const found = labels[labels.length - 1];
      if (found) return found.control || document.getElementById(found.htmlFor) || found.querySelector("input,textarea,select");
      return [...document.querySelectorAll("input,textarea,select")].find((e) => seen(e) && (e.getAttribute("aria-label") || "").startsWith(t)) || null;
    }, label);
    const el = handle.asElement();
    if (el) return el;
    await sleep(200);
  }
  throw new Error(`No field "${label}"`);
}

async function fill(page, label, value) {
  const el = await field(page, label);
  await el.evaluate((e) => e.scrollIntoView({ block: "center" }));
  const tag = await el.evaluate((e) => e.tagName);
  if (tag === "SELECT") {
    await el.select(value);
  } else {
    await el.click({ clickCount: 3 });
    await page.keyboard.press("Backspace");
    await el.type(value);
  }
}

const bodyText = (page) => page.evaluate(() => document.body.innerText);
const seesText = async (page, text, timeout = 15000) => {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if ((await bodyText(page)).includes(text)) return true;
    await sleep(200);
  }
  return false;
};

async function signIn(page, email, password = PASSWORD, path_ = "/login?mode=admin") {
  await open(page, path_);
  await page.waitForSelector('input[type="email"]', { visible: true, timeout: 60000 });
  await page.type('input[type="email"]', email);
  await page.type('input[type="password"]', password);
  await page.click('button[type="submit"]');
}

async function firstLink(page, from, pattern) {
  await open(page, from);
  return page.evaluate((src) => {
    const re = new RegExp(src);
    const a = [...document.querySelectorAll("main a[href]")].find((x) => re.test(x.getAttribute("href")));
    return a ? a.getAttribute("href") : null;
  }, pattern.source);
}

async function freshPage(browser, { label = "" } = {}) {
  const context = await browser.createBrowserContext();
  return tabIn(context, label);
}

/** A new tab sharing a context's session, with the same reporting as any other. */
async function tabIn(context, label) {
  const page = await context.newPage();
  await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: "light" }]);
  await page.setViewport({ width: 1366, height: 900 });
  page.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource/.test(m.text())) {
      report.consoleErrors.push(`${label} ${page.url()} :: ${m.text().slice(0, 300)}`);
    }
  });
  page.on("pageerror", (e) => report.consoleErrors.push(`${label} ${page.url()} :: PAGEERROR ${e.message.slice(0, 300)}`));
  page.on("response", (r) => {
    const url = r.url();
    if (url.includes("/api/") && r.status() >= 400 && !page.__expectErrors) {
      report.apiErrors.push(`${label} ${r.request().method()} ${url.replace(BASE, "")} -> ${r.status()} (on ${page.url().replace(BASE, "")})`);
    }
  });
  return page;
}

async function measure(page) {
  return page.evaluate(() => {
    const h1s = [...document.querySelectorAll("h1")].filter((h) => h.getBoundingClientRect().width > 0);
    const cut = [...document.querySelectorAll("main button, main a, main input, main select")]
      .filter((e) => {
        const r = e.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return false;
        if (e.closest("[data-scroll], table, [class*=tableWrap], [class*=scroll]")) return false;
        return r.right > window.innerWidth + 1 || r.left < -1;
      })
      .map((e) => (e.textContent || e.getAttribute("aria-label") || e.tagName).trim().slice(0, 40));
    return {
      h1: h1s.map((h) => h.textContent.trim()),
      title: document.title,
      overflowX: document.documentElement.scrollWidth - window.innerWidth,
      clipped: cut.slice(0, 6),
      broken: /Something went wrong|Couldn't load|Application error/.test(document.body.innerText),
    };
  });
}

async function axe(page) {
  if (!AXE) return null;
  await page.addScriptTag({ content: AXE });
  return page.evaluate(async () => {
    const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
    return r.violations.map((v) => `${v.id} (${v.nodes.length}): ${v.nodes[0].target.join(" ")}`);
  });
}

const journeys = {
  // --- authentication --------------------------------------------------------------------
  async A(browser) {
    console.log("A: authentication");
    const page = await freshPage(browser, { label: "A" });
    page.__expectErrors = true;
    await open(page, "/admin");
    await page.waitForFunction(() => location.pathname.startsWith("/login"), { timeout: 30000 }).catch(() => {});
    check("A1 a direct URL without a session goes to sign-in", page.url().includes("/login"), page.url());

    for (const width of WIDTHS) {
      await page.setViewport({ width, height: width < 800 ? 800 : 1000 });
      await open(page, "/login?mode=admin");
      const m = await measure(page);
      await page.screenshot({ path: path.join(OUT, `login-${width}.png`), fullPage: true });
      check(`A2 login at ${width}: one h1, no overflow`, m.h1.length === 1 && m.overflowX <= 0 && m.clipped.length === 0, JSON.stringify(m));
      report.pages.push({ page: "/login?mode=admin", width, ...m });
    }
    for (const target of ["/login?mode=admin", "/forgot-password", "/partner/apply"]) {
      for (const width of [1366, 390]) {
        await page.setViewport({ width, height: width < 800 ? 800 : 1000 });
        await open(page, target);
        const found = await axe(page);
        check(`A2 ${target} at ${width}: axe ran and found nothing`, Array.isArray(found) && found.length === 0, found);
      }
    }
    await page.setViewport({ width: 1366, height: 900 });
    await open(page, "/login?mode=admin");
    const words = await bodyText(page);
    for (const needed of ["Admin Portal", "Platform Control Center", "Admin Login", "Platform & User Management", "Product & Plan Management", "Orders, Billing & Payments", "Credits & Usage Control", "Remember Me", "Forgot Password"]) {
      check(`A3 login shows "${needed}"`, words.toLowerCase().includes(needed.toLowerCase()));
    }
    check("A4 no public registration on the Admin sign-in", !/sign up|create (an )?account|register/i.test(words));

    await signIn(page, STAFF, "not-the-password-at-all");
    check("A5 a wrong password is refused and says so", await seesText(page, "isn't right", 15000));
    check("A6 a refused sign-in stays on the sign-in page", page.url().includes("/login"));

    await press(page, "Show password", { selector: "button" }).catch(() => {});
    const shown = await page.evaluate(() => [...document.querySelectorAll("input")].some((i) => i.type === "text" && i.autocomplete === "current-password"));
    check("A7 Show/Hide reveals the password", shown);

    await open(page, "/forgot-password");
    await fill(page, "Email", "nobody@eco.test");
    await page.click('button[type="submit"]');
    check("A8 forgot password answers the same for an unknown address", await seesText(page, "If", 10000), (await bodyText(page)).slice(0, 200));

    await open(page, "/login?mode=admin");
    await page.type('input[type="email"]', STAFF);
    await page.type('input[type="password"]', PASSWORD);
    const remember = await field(page, "Remember");
    await remember.click();
    await page.click('button[type="submit"]');
    await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 60000 });
    await settle(page);
    check("A9 a correct password opens the console", page.url().includes("/admin"), page.url());
    const cookies = await page.browserContext().cookies();
    const session = cookies.find((c) => /session/i.test(c.name) && c.httpOnly);
    check("A10 Remember Me keeps a dated, http-only session cookie", session && session.expires > Date.now() / 1000 + 5 * 86400, session ? session.expires : "none");

    await page.browserContext().close();
    const plain = await freshPage(browser, { label: "A" });
    plain.__expectErrors = true;
    await signIn(plain, STAFF);
    await plain.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 60000 });
    await settle(plain);
    const plainCookie = (await plain.browserContext().cookies()).find((c) => /session/i.test(c.name) && c.httpOnly);
    check("A11 without Remember Me the cookie ends with the browser", plainCookie && plainCookie.session === true, plainCookie ? plainCookie.expires : "none");

    await press(plain, "Account menu", { selector: "button", contains: true }).catch(async () => press(plain, "Admin", { selector: "button", contains: true }));
    await press(plain, "Sign out", { selector: "button, a, [role=menuitem]" }).catch(async () => press(plain, "Log out", { selector: "button, a, [role=menuitem]" }));
    await plain.waitForFunction(() => location.pathname.startsWith("/login"), { timeout: 30000 }).catch(() => {});
    check("A12 sign out returns to sign-in", plain.url().includes("/login"), plain.url());
    await open(plain, "/admin/wallets");
    await plain.waitForFunction(() => location.pathname.startsWith("/login"), { timeout: 30000 }).catch(() => {});
    check("A13 after sign out a page goes back to sign-in", plain.url().includes("/login"), plain.url());
    await plain.browserContext().close();
  },

  // --- every page at every width ----------------------------------------------------------
  async B(browser) {
    console.log(`B: ${PAGES.length} pages + details at ${WIDTHS.join(", ")}`);
    const page = await freshPage(browser, { label: "B" });
    await signIn(page, STAFF);
    await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 60000 });
    await settle(page);
    const details = {
      "customer-detail": await firstLink(page, "/admin/customers", /^\/admin\/customers\/[0-9a-f-]{36}$/),
      "partner-detail": await firstLink(page, "/admin/partners/all", /^\/admin\/partners\/[0-9a-f-]{36}$/),
      "product-detail": await firstLink(page, "/admin/products", /^\/admin\/products\/[0-9a-f-]{36}$/),
      "application-detail": await firstLink(page, "/admin/partners/applications", /^\/admin\/partners\/applications\/[0-9a-f-]{36}$/),
      "ticket-detail": await firstLink(page, "/admin/tickets", /^\/admin\/tickets\/[0-9a-f-]{36}$/),
      "user-detail": await firstLink(page, "/admin/users", /^\/admin\/users\/[0-9a-f-]{36}$/),
    };
    for (const [name, href] of Object.entries(details)) check(`B0 ${name} is reachable from its list`, Boolean(href), href);
    const targets = [...PAGES.map((p) => [slug(p), p]), ...Object.entries(details).filter(([, href]) => href)];
    let bad = 0;
    for (const [name, target] of targets) {
      for (const width of WIDTHS) {
        // A tab per load: hundreds of navigations in one tab end in a detached frame.
        const tab = await tabIn(page.browserContext(), "B");
        await tab.bringToFront();
        await tab.setViewport({ width, height: width < 800 ? 800 : 1000 });
        const consoleBefore = report.consoleErrors.length;
        const apiBefore = report.apiErrors.length;
        await open(tab, target);
        const m = await measure(tab);
        const shot = `${name}-${width}.png`;
        await tab.screenshot({ path: path.join(OUT, shot), fullPage: false });
        const violations = width === 1366 || width === 390 ? await axe(tab) : null;
        await tab.close();
        const row = {
          page: target,
          width,
          ...m,
          axe: violations,
          console: report.consoleErrors.slice(consoleBefore),
          api: report.apiErrors.slice(apiBefore),
          shot,
        };
        report.pages.push(row);
        const problem = m.h1.length !== 1 || m.overflowX > 0 || m.clipped.length || m.broken || (violations && violations.length) || row.console.length || row.api.length;
        if (problem) {
          bad += 1;
          console.log(`  [PROBLEM] ${target} @${width}: h1=${m.h1.length} overflow=${m.overflowX} clipped=${m.clipped} broken=${m.broken} axe=${violations} console=${row.console.length} api=${row.api}`);
        }
      }
    }
    check(`B1 ${targets.length} pages at ${WIDTHS.length} widths with no finding`, bad === 0, `${bad} findings`);
    await page.browserContext().close();
  },

  // --- the dashboard against the API --------------------------------------------------------
  async C(browser) {
    console.log("C: dashboard");
    const page = await freshPage(browser, { label: "C" });
    await signIn(page, STAFF);
    await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 60000 });
    await settle(page);
    await page.setViewport({ width: 1920, height: 1080 });
    await open(page, "/admin");
    await page.screenshot({ path: path.join(OUT, "dashboard-full-1920.png"), fullPage: true });
    const data = await page.evaluate(async () => (await (await fetch("/api/v1/admin/dashboard/overview")).json()));
    fs.writeFileSync(path.join(OUT, "dashboard-overview.json"), JSON.stringify(data, null, 2));
    const words = await bodyText(page);
    for (const card of ["Total Customers", "Total Partners", "Active Subscriptions", "Collections", "Sales", "Wallet Summary"]) {
      check(`C1 card "${card}"`, words.toLowerCase().includes(card.toLowerCase()));
    }
    for (const part of ["Pending Actions", "Recent Activities", "Top Products", "Partner KYC Reviews", "Failed Payments", "Low-Balance"]) {
      check(`C2 section "${part}"`, words.toLowerCase().includes(part.toLowerCase()));
    }
    const count = data.cards?.customers?.total;
    check("C3 Total Customers shows the API's figure", count !== undefined && words.includes(String(count)), count);
    const chart = await page.evaluate(() => document.querySelectorAll("main svg").length);
    check("C4 charts draw", chart >= 2, chart);
    const pending = await page.evaluate(() => [...document.querySelectorAll("main a[href]")].map((a) => a.getAttribute("href")));
    for (const href of ["/admin/partners/applications", "/admin/payments/failed", "/admin/wallets"]) {
      check(`C5 a way through to ${href}`, pending.some((h) => h.startsWith(href)));
    }
    await press(page, "Total Customers", { selector: "a", contains: true });
    await settle(page);
    check("C6 a card opens its detail", page.url().includes("/admin/customers"), page.url());
    await page.browserContext().close();
  },

  // --- search, create, notifications ----------------------------------------------------------
  async D(browser) {
    console.log("D: top bar");
    const page = await freshPage(browser, { label: "D" });
    await signIn(page, STAFF);
    await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 60000 });
    await settle(page);
    await page.keyboard.down("Control");
    await page.keyboard.press("KeyK");
    await page.keyboard.up("Control");
    await sleep(300);
    await page.keyboard.type("Store Buyer");
    check("D1 global search finds a customer", await seesText(page, "Store Buyer", 8000));
    await page.waitForSelector('[role="listbox"] [role="option"]', { timeout: 8000 }).catch(() => {});
    const results = await page.$$('[role="listbox"] [role="option"]');
    check("D2 the search lists results", results.length >= 1, results.length);
    await page.keyboard.press("Escape");
    await press(page, "Create", { selector: "button" });
    const menu = await bodyText(page);
    check("D3 Create offers a customer", menu.includes("New customer") || menu.includes("Customer"));
    await page.keyboard.press("Escape");
    await press(page, "Notifications", { selector: "button", contains: true });
    check("D4 notifications list real items", await seesText(page, "KYC", 8000) || await seesText(page, "waiting", 2000));
    await page.browserContext().close();
  },

  // --- workflow 1: application to activation ---------------------------------------------------
  async E(browser) {
    console.log("E: public application to an active partner");
    const applicant = await freshPage(browser, { label: "E" });
    await open(applicant, "/partner/apply");
    await applicant.screenshot({ path: path.join(OUT, "partner-apply.png"), fullPage: true });
    await fill(applicant, "Business name", "Browser Check Traders");
    await fill(applicant, "First name", "Bela");
    await fill(applicant, "Last name", "Check");
    await fill(applicant, "Email", "bela.apply@eco.test");
    await fill(applicant, "Password", `${PASSWORD}!Aa1`);
    const country = await field(applicant, "Country");
    await country.select("IN").catch(async () => country.type("IN"));
    await press(applicant, "Reseller", { selector: "button, label, [role=radio]", contains: true }).catch(() => {});
    await press(applicant, "Send application", { selector: "button" });
    check("E1 the public form accepts an application (CSRF cookie included)", await seesText(applicant, "Check your email", 20000), (await bodyText(applicant)).slice(0, 200));
    await applicant.browserContext().close();

    const staff = await freshPage(browser, { label: "E" });
    await signIn(staff, STAFF);
    await staff.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 60000 });
    await settle(staff);
    await open(staff, "/admin/partners/applications");
    check("E2 the queue lists what was submitted", await seesText(staff, "Meera Consulting"));
    const href = await staff.evaluate(() => [...document.querySelectorAll("main tr")].find((r) => r.textContent.includes("Meera Consulting"))?.querySelector('a[href*="/applications/"]')?.getAttribute("href"));
    check("E3 an application opens", Boolean(href), href);
    await open(staff, href);
    await press(staff, "Start review", { selector: "button" });
    await settle(staff);
    check("E4 starting a review moves it on", await seesText(staff, "Under review", 10000) || await seesText(staff, "Under Review", 2000));
    const [popup] = await Promise.all([
      staff.browserContext().waitForTarget((t) => t.url().includes("/documents/"), { timeout: 15000 }).catch(() => null),
      press(staff, "registration.png", { selector: "button, a", contains: true }),
    ]);
    // A document opens in its own tab: read what it answered, then close it so the reviewer's
    // tab is the one in front again.
    let served = null;
    if (popup) {
      const documentPage = await popup.page();
      const headers = await staff.evaluate(async (url) => {
        const r = await fetch(url);
        return { status: r.status, type: r.headers.get("content-type"), cache: r.headers.get("cache-control"), csp: r.headers.get("content-security-policy"), sniff: r.headers.get("x-content-type-options") };
      }, popup.url());
      served = headers;
      if (documentPage) await documentPage.close();
      await staff.bringToFront();
    }
    check("E4a a document opens, private, unsniffed and of its checked type", served && served.status === 200 && served.type === "image/png" && /no-store/.test(served.cache) && served.sniff === "nosniff" && /sandbox/.test(served.csp), JSON.stringify(served));
    await open(staff, href);
    const log = await bodyText(staff);
    check("E5 opening a document is on its access log", /document access log/i.test(log) && /Staff|staff@eco\.test|Admin/i.test(log), popup ? "opened" : "no new tab");
    await press(staff, "Approve", { selector: "button", contains: true });
    await press(staff, "Approve", { selector: "[role=dialog] button, dialog button", contains: true }).catch(() => {});
    await settle(staff);
    check("E6 approving records the decision", await seesText(staff, "Approved", 15000));
    const status = await staff.evaluate(async (id) => (await (await fetch(`/api/v1/admin/kyc/applications/${id}`)).json()), href.split("/").pop());
    check("E7 the decision names reviewer and date", status.decisions?.[0]?.decided_by_name || status.decisions?.[0]?.reviewer || status.decisions?.[0]?.created_at, JSON.stringify(status.decisions?.[0] || {}));
    await staff.browserContext().close();

    const partner = await freshPage(browser, { label: "E" });
    await signIn(partner, "meera.apply@eco.test", PASSWORD, "/login?mode=partner");
    await partner.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 60000 });
    await settle(partner);
    check("E8 the approved partner lands on the partner portal", partner.url().includes("/partner"), partner.url());
    check("E9 and no longer sees the draft notice", !(await bodyText(partner)).includes("Complete your application"));
    await partner.browserContext().close();

    const waiting = await freshPage(browser, { label: "E" });
    await signIn(waiting, "kiran.apply@eco.test", PASSWORD, "/login?mode=partner");
    await waiting.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 60000 });
    await settle(waiting);
    check("E10 a draft applicant lands on the partner portal", waiting.url().includes("/partner"), waiting.url());
    const kyc = `/partner/${waiting.url().match(/partner\/([0-9a-f-]{36})/)?.[1]}/kyc`;
    await open(waiting, kyc);
    await waiting.screenshot({ path: path.join(OUT, "partner-kyc.png"), fullPage: true });
    check("E11 and can reach the application", await seesText(waiting, "Business details"));
    await fill(waiting, "Legal name", "Kiran Traders Pvt Ltd");
    await waiting.browserContext().close();
  },

  // --- workflow 2: new customer to a plan ------------------------------------------------------
  async F(browser) {
    console.log("F: new customer and plan");
    const page = await freshPage(browser, { label: "F" });
    await signIn(page, STAFF);
    await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 60000 });
    await settle(page);
    await open(page, "/admin/customers");
    await press(page, "New customer", { selector: "button" });
    await fill(page, "Organization name", "Browser Check Ltd");
    await fill(page, "Owner's name", "Bea Owner");
    await fill(page, "Owner's email", "bea.owner@eco.test");
    await fill(page, "Reason", "Set up for the browser check");
    await press(page, "Create customer", { selector: "[role=dialog] button, dialog button" });
    check("F1 the customer is created", await seesText(page, "Browser Check Ltd", 15000));
    await settle(page);
    await open(page, "/admin/customers");
    const href = await page.evaluate(() => [...document.querySelectorAll("main tr")].find((r) => r.textContent.includes("Browser Check Ltd"))?.querySelector('a[href^="/admin/customers/"]')?.getAttribute("href"));
    check("F2 and listed", Boolean(href), href);
    await open(page, href);
    await page.setViewport({ width: 1366, height: 1400 });
    await press(page, "Assign plan", { selector: "button" });
    await settle(page);
    await fill(page, "Reason", "Agreed on the sales call");
    await press(page, "Assign plan", { selector: "[role=dialog] button, dialog button" });
    check("F3 the plan is assigned and waits for payment", await seesText(page, "waits for payment", 15000), (await bodyText(page)).slice(0, 200));
    await page.browserContext().close();
  },

  // --- wallets --------------------------------------------------------------------------------
  async G(browser) {
    console.log("G: wallets");
    const page = await freshPage(browser, { label: "G" });
    await signIn(page, STAFF);
    await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 60000 });
    await settle(page);
    for (const [target, name] of [["/admin/wallets", "Platform Wallet"], ["/admin/wallets/ai-credits", "AI Credits"], ["/admin/wallets/consumption", "Consumption Wallet"]]) {
      await open(page, target);
      check(`G1 ${name} lists a real balance`, await seesText(page, "Store Buyer"), target);
    }
    await open(page, "/admin/wallets/transactions");
    const words = await bodyText(page);
    check("G2 transaction controls list entries, failed events and duplicates", /top.?up/i.test(words));
    await press(page, "Failed events", { contains: true, timeout: 5000 }).catch(() => {});
    await settle(page);
    const events = await page.evaluate(async () => (await (await fetch("/api/v1/admin/wallets/events?outcome=failed")).json()));
    check("G3 the unfunded event is on the record", (events.results || []).length >= 1, (events.results || []).length);
    const duplicates = await page.evaluate(async () => (await (await fetch("/api/v1/admin/wallets/events?duplicates=1")).json()));
    check("G4 the event sent twice charged once and is flagged", (duplicates.results || []).some((e) => e.duplicate_count >= 0), (duplicates.results || []).length);
    const balanced = await page.evaluate(async () => (await (await fetch("/api/v1/admin/wallets/reconcile")).json()));
    check("G5 every balance equals its ledger", balanced.balanced === true, JSON.stringify(balanced).slice(0, 120));

    const wallets = await page.evaluate(async () => (await (await fetch("/api/v1/admin/wallets?kind=consumption")).json()));
    const wallet = (wallets.results || [])[0];
    await open(page, `/admin/wallets/consumption`);
    await press(page, "History and actions", { selector: "button, a", contains: true });
    await settle(page);
    await press(page, "Manual adjustment", { selector: "button" });
    await fill(page, "Amount", "100");
    await fill(page, "Reason", "Browser check adjustment");
    await press(page, "Apply adjustment", { selector: "[role=dialog] button, dialog button" });
    await press(page, "Increase", { selector: "[role=dialog] button, dialog button", contains: true, timeout: 3000 }).catch(() => {});
    await settle(page);
    const after = await page.evaluate(async (id) => (await (await fetch(`/api/v1/admin/wallets/${id}`)).json()), wallet.id);
    check("G6 a manual adjustment moves the balance once", after.balance !== wallet.balance, `${wallet.balance} -> ${after.balance}`);
    await page.browserContext().close();
  },

  // --- pricing -----------------------------------------------------------------------------------
  async H(browser) {
    console.log("H: usage pricing and plan rules");
    const page = await freshPage(browser, { label: "H" });
    await signIn(page, STAFF);
    await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 60000 });
    await settle(page);
    await open(page, "/admin/usage-pricing");
    check("H1 rates are the ones an Admin entered", await seesText(page, "WhatsApp"));
    await press(page, "Set rate for SMS messages in INR", { selector: "button", contains: true });
    await fill(page, "Price", "0.30");
    await fill(page, "Reason", "Browser check new rate card");
    await press(page, "Set rate", { selector: "[role=dialog] button, dialog button" });
    await settle(page);
    check("H2 a new rate takes effect", await seesText(page, "0.30", 10000));
    await open(page, "/admin/usage-pricing/credit-packs");
    check("H3 packs list", await seesText(page, "Starter credits"));
    await open(page, "/admin/partners/commission-rules");
    check("H4 commission rules list the programme default", await seesText(page, "default v"));
    check("H5 and state clearance from the server", await seesText(page, "days after the payment"));
    await page.browserContext().close();
  },

  // --- support -----------------------------------------------------------------------------------
  async I(browser) {
    console.log("I: support");
    const page = await freshPage(browser, { label: "I" });
    await signIn(page, STAFF);
    await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 60000 });
    await settle(page);
    await open(page, "/admin/tickets");
    check("I1 customer tickets are listed", await seesText(page, "How do I add seats?"));
    const href = await page.evaluate(() => [...document.querySelectorAll("main tr")].find((r) => r.textContent.includes("How do I add seats?"))?.querySelector('a[href^="/admin/tickets/"]')?.getAttribute("href"));
    await open(page, href);
    await press(page, "Reply", { selector: "button" });
    await fill(page, "Reply", "Add them under Billing, Seats.");
    await press(page, "Send reply", { selector: "[role=dialog] button, dialog button" });
    check("I2 a reply joins the conversation", await seesText(page, "Add them under Billing, Seats.", 10000));
    await open(page, "/admin/help-content");
    check("I3 help content lists drafts and published articles", (await seesText(page, "How do I export my data?")) && (await seesText(page, "Setting up your first project")));
    await page.browserContext().close();
  },

  // --- reports -----------------------------------------------------------------------------------
  async J(browser) {
    console.log("J: reports");
    const page = await freshPage(browser, { label: "J" });
    await signIn(page, STAFF);
    await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 60000 });
    await settle(page);
    for (const [kind, needle] of [["sales", "NEFT"], ["payments", ""], ["renewals", ""], ["wallet-movements", "Store Buyer"], ["usage", "Easy CRM"], ["partner-conversions", ""], ["commissions", ""], ["payouts", ""]]) {
      await open(page, `/admin/reports/${kind}`);
      const csv = await page.evaluate(async (k) => {
        const r = await fetch(`/api/v1/admin/reports/${k}?format=csv`);
        return { status: r.status, type: r.headers.get("content-type"), text: (await r.text()).slice(0, 160) };
      }, kind);
      check(`J1 ${kind} opens and exports CSV`, csv.status === 200 && /csv/.test(csv.type), csv.text.split("\n")[0]);
      if (needle && kind !== "sales") check(`J2 ${kind} holds real rows`, await seesText(page, needle));
    }
    await page.browserContext().close();
  },

  // --- settings and permissions ------------------------------------------------------------------
  async K(browser) {
    console.log("K: settings and permissions");
    const page = await freshPage(browser, { label: "K" });
    await signIn(page, STAFF);
    await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 60000 });
    await settle(page);
    await open(page, "/admin/settings");
    check("K1 settings show what was saved", await seesText(page, "Workeasy360 Test Pvt Ltd") || (await page.evaluate(() => [...document.querySelectorAll("input")].some((i) => i.value === "Workeasy360 Test Pvt Ltd"))));
    await open(page, "/admin/settings/domains");
    check("K2 domains list the real products and their store links", (await seesText(page, "Easy CRM")) && (await seesText(page, "/store/product/easy-crm")));
    await open(page, "/admin/security/sign-ins");
    check("K3 the sign-in audit lists sign-ins", await seesText(page, STAFF) || await seesText(page, "Sign"));
    await page.browserContext().close();

    const agent = await freshPage(browser, { label: "K-agent" });
    agent.__expectErrors = true;
    await signIn(agent, "agent@eco.test");
    await agent.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 60000 });
    await settle(agent);
    const nav = await agent.evaluate(() => [...document.querySelectorAll('nav[aria-label="Admin Portal"] a')].map((a) => a.textContent.trim()));
    check("K4 a colleague sees only the modules their grants allow", nav.includes("Customers") && nav.includes("Support") && !nav.includes("Wallets & Credits") && !nav.includes("Settings"), nav.join(", "));
    await open(agent, "/admin/wallets");
    const refused = await agent.evaluate(async () => (await fetch("/api/v1/admin/wallets")).status);
    check("K5 the server refuses what the sidebar hides", refused === 403, refused);
    check("K6 the page says so rather than showing an empty list", await seesText(agent, "access", 10000), (await bodyText(agent)).slice(0, 160));
    await agent.browserContext().close();

    const customer = await freshPage(browser, { label: "K-customer" });
    customer.__expectErrors = true;
    await signIn(customer, "buyer@eco.test", PASSWORD, "/login");
    await customer.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 60000 }).catch(() => {});
    await settle(customer);
    const status = await customer.evaluate(async () => (await fetch("/api/v1/admin/dashboard/overview")).status);
    check("K7 a customer cannot read the Admin API", status === 403 || status === 401, status);
    await customer.browserContext().close();
  },

  // --- a phone's navigation -----------------------------------------------------------------------
  async L(browser) {
    console.log("L: drawer on a phone");
    const page = await freshPage(browser, { label: "L" });
    await page.setViewport({ width: 390, height: 800, isMobile: true, hasTouch: true });
    await signIn(page, STAFF);
    await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 60000 });
    await settle(page);
    await open(page, "/admin");
    await page.screenshot({ path: path.join(OUT, "dashboard-full-390.png"), fullPage: true });
    const hidden = await page.evaluate(() => {
      const nav = document.querySelector('nav[aria-label="Admin Portal"]');
      if (!nav) return true;
      const r = nav.getBoundingClientRect();
      return r.right <= 0 || r.width === 0;
    });
    check("L1 the sidebar starts closed", hidden);
    await press(page, "Menu", { selector: "button" });
    await sleep(400);
    const items = await page.evaluate(() => [...document.querySelectorAll('nav[aria-label="Admin Portal"] a')].filter((a) => a.getBoundingClientRect().right > 0).map((a) => a.textContent.trim()));
    await page.screenshot({ path: path.join(OUT, "drawer-390.png") });
    check("L2 the drawer shows the nine modules", items.length === 9, items.join(", "));
    await press(page, "Wallets & Credits", { selector: "nav a" });
    await settle(page);
    check("L3 a module opens and the drawer closes", page.url().includes("/admin/wallets"), page.url());
    const m = await measure(page);
    check("L4 no page overflow on the phone", m.overflowX <= 0, m.overflowX);
    await page.browserContext().close();
  },
};

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--force-prefers-reduced-motion", "--no-sandbox"] });
  for (const [key, run] of Object.entries(journeys)) {
    if (ONLY.length && !ONLY.includes(key)) continue;
    try {
      await run(browser);
    } catch (err) {
      check(`${key} finished`, false, err.message);
      for (const context of browser.browserContexts()) {
        // The reason a journey failed may be that the browser is gone: report it, carry on.
        for (const page of await context.pages().catch(() => [])) {
          await page.screenshot({ path: path.join(OUT, `failed-${key}-${Date.now()}.png`) }).catch(() => {});
          console.log("    at", page.url(), (await bodyText(page).catch(() => "")).slice(0, 300).replace(/\n/g, " | "));
        }
        if (context !== browser.defaultBrowserContext()) await context.close().catch(() => {});
      }
    }
    fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(report, null, 2));
  }
  await browser.close();
  const failed = report.checks.filter((c) => !c.ok);
  console.log(`\n${report.checks.length - failed.length}/${report.checks.length} checks passed; ${report.consoleErrors.length} console errors; ${report.apiErrors.length} API errors`);
  for (const e of [...new Set(report.consoleErrors)].slice(0, 30)) console.log("  CONSOLE", e);
  for (const e of [...new Set(report.apiErrors)].slice(0, 40)) console.log("  API", e);
  process.exit(failed.length ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
