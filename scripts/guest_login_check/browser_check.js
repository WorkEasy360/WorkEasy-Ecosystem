// Real-browser check: Partners → Affiliate / Reseller / White Label, and Login as Guest
// across hosts (ADR-062; 2026-11-11: View and Login as Guest side by side, the detail
// page's header actions, Return to Super Admin / End Guest Session). Chrome sees the
// real portal host names; edge.js keeps the
// Host header, so the backend's per-host rules, the frontend's per-host CSP and the
// host-only cookies are the production ones.
//
// NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> CHECK_PASSWORD=<seed's>
//   IDS='<the seed's JSON>' EDGE=127.0.0.1:8088 OUT=<dir> node scripts/guest_login_check/browser_check.js

const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");

const PASSWORD = process.env.CHECK_PASSWORD;
const IDS = JSON.parse(process.env.IDS);
const EDGE = process.env.EDGE || "127.0.0.1:8088";
const OUT = process.env.OUT || ".";
const AXE = fs.readFileSync(process.env.AXE_PATH, "utf8");
const STAFF = "staff@eco.test";
const HOSTS = ["admin", "partner", "app", "auth", "api"].map((p) => `${p}.workeasy360.com`);
const ADMIN = "http://admin.workeasy360.com";
const PARTNER = "http://partner.workeasy360.com";
const NAMES = { affiliate: "Guest Affiliate Co", reseller: "Guest Reseller Co", white_label: "Guest WL Co", draft: "Guest Draft Co" };
const HOME = { affiliate: "/partner/affiliate/dashboard", reseller: "/partner/reseller/dashboard", white_label: "/partner/white-label/dashboard" };
const PAGE = { affiliate: "/admin/partners/affiliate", reseller: "/admin/partners/reseller", white_label: "/admin/partners/white-label" };
const TYPE = { affiliate: "Affiliate", reseller: "Reseller", white_label: "White-label" };

const report = { checks: [], csp: [], consoleErrors: [], tickets: [] };
function check(name, ok, detail = "") {
  report.checks.push({ name, ok: Boolean(ok), detail: String(detail).slice(0, 500) });
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? ` - ${String(detail).slice(0, 200)}` : ""}`);
}

function watch(page) {
  page.on("console", (msg) => {
    const text = msg.text();
    if (/Content Security Policy|Refused to/i.test(text)) report.csp.push(text);
    else if (msg.type() === "error" && !/Failed to load resource: the server responded with a status of (401|403|404)/.test(text)) report.consoleErrors.push(`${page.url()} ${text}`);
  });
  page.on("request", (req) => {
    for (const t of report.tickets) if (req.url().includes(t)) report.csp.push(`TICKET IN URL ${req.url()}`);
  });
  page.on("response", async (res) => {
    if (/\/api\/v1\/admin\/partners\/[^/]+\/guest-view$/.test(res.url()) && res.request().method() === "POST") {
      try {
        const body = await res.json();
        if (body.entry?.ticket) report.tickets.push(body.entry.ticket);
      } catch {
        /* not json */
      }
    }
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitText(page, text, timeout = 20000) {
  await page.waitForFunction((t) => document.body && document.body.innerText.includes(t), { timeout }, text);
}

async function h1(page) {
  return page.$$eval("h1", (els) => els.map((e) => e.textContent.trim()));
}

async function rowNames(page) {
  await page.waitForSelector('table[aria-label="Partners"], table', { timeout: 20000 });
  await sleep(500);
  return page.$$eval("table tbody tr", (rows) => rows.map((r) => r.querySelector("a")?.textContent?.trim()).filter(Boolean));
}

async function headers(page) {
  return page.$$eval("table thead th", (ths) => ths.map((t) => t.textContent.trim()));
}

async function rowMenu(page, name) {
  const button = await page.waitForSelector(`button[aria-label="Actions for ${name}"]`, { timeout: 20000 });
  // As a person does on a narrow screen: scroll the table sideways to the row's actions.
  await button.evaluate((b) => b.scrollIntoView({ block: "center", inline: "center" }));
  await button.click();
  await page.waitForSelector(`[role="menu"][aria-label="Actions for ${name}"]`);
  return page.$$eval(`[role="menu"][aria-label="Actions for ${name}"] [role="menuitem"]`, (items) =>
    items.map((i) => ({ text: i.textContent.trim(), disabled: i.hasAttribute("disabled") || i.getAttribute("aria-disabled") === "true" })),
  );
}

async function chooseMenuItem(page, name, label) {
  const items = await page.$$(`[role="menu"][aria-label="Actions for ${name}"] [role="menuitem"]`);
  for (const item of items) {
    if ((await item.evaluate((e) => e.textContent.trim())) === label) return item.click();
  }
  throw new Error(`no menu item ${label}`);
}

/** Login as Guest from the button beside View: the reason dialog, then the partner host. */
async function startGuest(page, name) {
  const button = await page.waitForSelector(`button[aria-label="Login as Guest to ${name}"]`, { timeout: 20000 });
  await button.click();
  await page.waitForSelector('[role="dialog"]');
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle2", timeout: 30000 }), clickIn(page, '[role="dialog"] button', "Start guest session")]);
}

async function clickIn(page, selector, label) {
  for (const el of await page.$$(selector)) {
    if ((await el.evaluate((e) => e.textContent.trim())) === label) return el.click();
  }
  throw new Error(`no ${selector} "${label}"`);
}

const BANNER = '[role="status"][aria-label="Guest Session"]';

/** Every row's View and Login as Guest are on screen as the page opens: nothing to
 * scroll sideways, neither the page nor the table, and each is the topmost element
 * at its own centre (not under the sticky column or cut off by the table's edge). */
async function viewAndGuestInView(page) {
  return page.evaluate(() => {
    const box = document.querySelector("table")?.parentElement;
    const rows = [...document.querySelectorAll("table tbody tr")];
    const seen = (el) => {
      if (!el) return false;
      const r = el.getBoundingClientRect();
      if (r.right > window.innerWidth || r.left < 0) return false;
      const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      return Boolean(hit && el.contains(hit));
    };
    const results = rows.map((row) => {
      const view = row.querySelector('a[aria-label^="View "]');
      const guest = row.querySelector('button[aria-label^="Login as Guest to"]');
      return {
        name: row.querySelector("a")?.textContent?.trim(),
        view: seen(view),
        guest: seen(guest),
        adjacent: Boolean(view && guest && view.nextElementSibling && (view.nextElementSibling === guest || view.nextElementSibling.contains(guest))),
      };
    });
    return {
      pageScroll: document.documentElement.scrollWidth - window.innerWidth,
      tableScrollLeft: box ? box.scrollLeft : 0,
      ok: results.every((r) => r.view && r.guest && r.adjacent),
      results,
    };
  });
}

async function axe(page, context = "document") {
  await page.addScriptTag({ content: AXE });
  return page.evaluate(async (ctx) => {
    const target = ctx === "document" ? document : document.querySelector(ctx);
    const result = await window.axe.run(target, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] });
    return result.violations.map((v) => `${v.id}: ${v.nodes.length}`);
  }, context);
}

async function session(browser, origin) {
  const page = await browser.newPage();
  await page.goto(`${origin}/api/v1/auth/session`, { waitUntil: "domcontentloaded" });
  const body = JSON.parse(await page.evaluate(() => document.body.innerText));
  await page.close();
  return body;
}

async function sidebarOrder(page) {
  return page.$$eval('nav[aria-label="Admin Portal"] ul ul a', (links) => links.map((a) => a.textContent.trim()));
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
    headless: true,
    args: [
      `--host-resolver-rules=${HOSTS.map((h) => `MAP ${h} ${EDGE}`).join(",")}`,
      "--disable-features=HttpsUpgrades,HttpsFirstBalancedModeAutoEnable",
      `--unsafely-treat-insecure-origin-as-secure=${HOSTS.map((h) => `http://${h}`).join(",")}`,
      "--no-first-run",
    ],
    defaultViewport: { width: 1366, height: 900 },
  });
  const page = await browser.newPage();
  watch(page);
  try {
    // --- sign in on admin. itself (ADR-059) ------------------------------------------
    await page.goto(`${ADMIN}/login`, { waitUntil: "networkidle2" });
    await page.type('input[name="email"]', STAFF);
    await page.type('input[name="password"]', PASSWORD);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle2" }), page.click('button[type="submit"]')]);
    check("S1 the Super Admin signs in on admin.workeasy360.com", new URL(page.url()).pathname.startsWith("/admin"), page.url());

    // --- the Partners menu -------------------------------------------------------------
    const partnersButton = await page.waitForSelector('nav[aria-label="Admin Portal"] button[aria-expanded]');
    const label = await partnersButton.evaluate((b) => b.textContent.trim());
    if ((await partnersButton.evaluate((b) => b.getAttribute("aria-expanded"))) !== "true") await partnersButton.click();
    await sleep(300);
    check("M1 Partners opens into Affiliate, Reseller, White Label, All Partners", JSON.stringify(await sidebarOrder(page)) === JSON.stringify(["Affiliate", "Reseller", "White Label", "All Partners"]), `${label}: ${await sidebarOrder(page)}`);

    // --- each type lists only its partners; All partners lists all ----------------------
    let allHeaders = null;
    await page.goto(`${ADMIN}/admin/partners/all`, { waitUntil: "networkidle2" });
    const everyone = await rowNames(page);
    allHeaders = await headers(page);
    check("C0 All partners lists every seeded partner", Object.values(NAMES).every((n) => everyone.includes(n)), everyone.join(", "));
    check(
      "T1 the owner's layout: Partner Name | Partner Type | Status | View / Actions first; no Earnings, no trailing Guest Login",
      JSON.stringify(allHeaders.slice(0, 4)) === JSON.stringify(["Partner Name", "Type / Permissions", "Account Status", "View / Actions"]) &&
        !allHeaders.includes("Earnings") &&
        !allHeaders.includes("Guest Login"),
      allHeaders.join("|"),
    );
    const buttons = await page.$$eval("table tbody tr", (rows) =>
      rows.map((r) => {
        const guest = r.querySelector('button[aria-label^="Login as Guest to"]');
        return {
          name: r.querySelector("a")?.textContent?.trim(),
          view: Boolean(r.querySelector('a[aria-label^="View "]')),
          login: Boolean(guest),
          unavailable: guest?.getAttribute("aria-disabled") === "true",
          money: /[₹£$€]\s?\d/.test(r.innerText),
        };
      }),
    );
    check(
      "T2 every row has View and Login as Guest; the draft's is shown but unavailable; no row shows money",
      buttons.every((b) => b.view && b.login && (b.name === NAMES.draft ? b.unavailable : !b.unavailable)) && buttons.every((b) => !b.money),
      JSON.stringify(buttons.map((b) => [b.name, b.view, b.login, b.unavailable, b.money])),
    );
    const scroll = await page.evaluate(() => {
      const box = document.querySelector("table")?.parentElement;
      return box ? getComputedStyle(box).overflowX : "none";
    });
    check("T3 the table still scrolls sideways (inside its card) when it must", ["auto", "scroll"].includes(scroll), scroll);
    for (const width of [1920, 1366, 1280]) {
      await page.setViewport({ width, height: 900 });
      await page.reload({ waitUntil: "networkidle2" });
      await page.waitForSelector("table tbody tr");
      const seen = await viewAndGuestInView(page);
      check(
        `T4 at ${width} px View and Login as Guest are side by side and in view in every row, with nothing to scroll`,
        seen.ok && seen.pageScroll <= 0 && seen.tableScrollLeft === 0,
        JSON.stringify(seen),
      );
      await page.screenshot({ path: path.join(OUT, `all-partners-${width}.png`) });
    }
    await page.setViewport({ width: 390, height: 844 });
    await page.reload({ waitUntil: "networkidle2" });
    await page.waitForSelector("table tbody tr");
    const phone = await page.evaluate(() => ({
      pageScroll: document.documentElement.scrollWidth - window.innerWidth,
      reachable: [...document.querySelectorAll('table tbody tr button[aria-label^="Login as Guest to"]')].every((b) => b.getBoundingClientRect().width > 0),
    }));
    check("T5 at 390 px the page does not scroll sideways and every Login as Guest is there", phone.pageScroll <= 0 && phone.reachable, JSON.stringify(phone));
    await page.screenshot({ path: path.join(OUT, "all-partners-390.png") });
    await page.setViewport({ width: 1366, height: 900 });
    await page.reload({ waitUntil: "networkidle2" });
    await page.waitForSelector("table tbody tr");
    for (const kind of ["affiliate", "reseller", "white_label"]) {
      await page.goto(`${ADMIN}${PAGE[kind]}`, { waitUntil: "networkidle2" });
      const names = await rowNames(page);
      const expected = kind === "affiliate" ? [NAMES.affiliate, NAMES.draft] : [NAMES[kind]];
      const others = Object.values(NAMES).filter((n) => !expected.includes(n));
      check(`C1 ${kind}: lists exactly its partners`, expected.every((n) => names.includes(n)) && !others.some((n) => names.includes(n)), names.join(", "));
      check(`C2 ${kind}: the same table as All partners`, JSON.stringify(await headers(page)) === JSON.stringify(allHeaders), (await headers(page)).join("|"));
      const titles = await h1(page);
      check(`C3 ${kind}: one h1, its own title`, titles.length === 1 && /partners$/.test(titles[0]), titles.join("|"));
      const current = await page.$$eval('nav[aria-label="Admin Portal"] a[aria-current="page"]', (a) => a.map((x) => x.textContent.trim()));
      check(`C4 ${kind}: the menu marks it`, current.length === 1, current.join(","));
      const tabs = await page.$$eval('nav[aria-label="Approved Partners pages"] a', (a) => a.map((x) => x.textContent.trim()));
      check(`C5 ${kind}: the module tabs are unchanged`, JSON.stringify(tabs) === JSON.stringify(["All partners", "Partner overview", "Product requests", "Upgrade fees"]), tabs.join("|"));
      const violations = await axe(page);
      check(`C6 ${kind}: axe WCAG 2.1 AA`, violations.length === 0, violations.join("; "));
    }

    // --- reorder with the keyboard, remembered after a reload ---------------------------
    await page.goto(`${ADMIN}/admin/partners/all`, { waitUntil: "networkidle2" });
    await page.evaluate(() => localStorage.removeItem("w360.admin.menu.partners"));
    await page.reload({ waitUntil: "networkidle2" });
    await page.focus('nav[aria-label="Admin Portal"] a[href="/admin/partners/white-label"]');
    await page.keyboard.down("Alt");
    await page.keyboard.press("ArrowUp");
    await page.keyboard.up("Alt");
    await sleep(400);
    const moved = await sidebarOrder(page);
    const focused = await page.evaluate(() => document.activeElement?.textContent?.trim());
    await page.reload({ waitUntil: "networkidle2" });
    await sleep(400);
    const kept = await sidebarOrder(page);
    check("R1 Alt+Up moves White Label above Reseller, keeping the focus on it", JSON.stringify(moved) === JSON.stringify(["Affiliate", "White Label", "Reseller", "All Partners"]) && focused === "White Label", `${moved} focus=${focused}`);
    check("R2 the order is remembered after a reload", JSON.stringify(kept) === JSON.stringify(moved), kept.join(","));
    await page.evaluate(() => localStorage.removeItem("w360.admin.menu.partners"));

    // --- the row menu -----------------------------------------------------------------
    await page.goto(`${ADMIN}${PAGE.reseller}`, { waitUntil: "networkidle2" });
    const items = (await rowMenu(page, NAMES.reseller)).map((i) => i.text);
    check(
      "G0 the row menu keeps Manage Guest Access and the other actions; Login as Guest is no longer in it",
      ["View partner", "Manage Guest Access", "Suspend"].every((t) => items.includes(t)) && !items.some((t) => t.startsWith("Login as Guest")),
      items.join(" | "),
    );
    await page.keyboard.press("Escape");
    await page.goto(`${ADMIN}${PAGE.affiliate}`, { waitUntil: "networkidle2" });
    const draftButton = await page.waitForSelector(`button[aria-label="Login as Guest to ${NAMES.draft}"]`, { timeout: 20000 });
    const draftLook = await draftButton.evaluate((b) => ({
      unavailable: b.getAttribute("aria-disabled") === "true",
      tip: document.getElementById(b.getAttribute("aria-describedby") || "")?.textContent?.trim(),
    }));
    await draftButton.click();
    await sleep(400);
    const draftDialog = await page.$('[role="dialog"]');
    check("G1 a draft's Login as Guest says it opens once the partner is active, and opens nothing", draftLook.unavailable && draftLook.tip === "Opens once the partner is active" && !draftDialog, JSON.stringify(draftLook));

    // --- Login as Guest, for each type ----------------------------------------------------
    for (const kind of ["reseller", "affiliate", "white_label"]) {
      // Started from a searched list: Return to Super Admin must bring the search back.
      const listed = `${PAGE[kind]}?q=${encodeURIComponent(NAMES[kind])}`;
      await page.goto(`${ADMIN}${listed}`, { waitUntil: "networkidle2" });
      const loginButton = await page.waitForSelector(`button[aria-label="Login as Guest to ${NAMES[kind]}"]`, { timeout: 20000 });
      const look = await loginButton.evaluate((b) => {
        const view = b.closest("td")?.querySelector('a[aria-label^="View "]');
        return {
          text: b.textContent.trim(),
          cursor: getComputedStyle(b).cursor,
          afterView: Boolean(view && view.nextElementSibling && (view.nextElementSibling === b || view.nextElementSibling.contains(b))),
        };
      });
      check(`B1 ${kind}: a "Login as Guest" button right after View, with a pointer`, look.text === "Login as Guest" && look.cursor === "pointer" && look.afterView, JSON.stringify(look));
      await startGuest(page, NAMES[kind]);
      const url = new URL(page.url());
      check(`L1 ${kind}: after the reason, lands on the partner host, on that partner's dashboard`, url.host === "partner.workeasy360.com" && url.pathname === HOME[kind], page.url());
      const banner = await page.waitForSelector(BANNER, { timeout: 20000 }).catch(() => null);
      const text = banner ? await banner.evaluate((b) => b.innerText) : "";
      const ways = banner ? await banner.$$eval("button", (bs) => bs.map((x) => x.textContent.trim())) : [];
      check(
        `L2 ${kind}: "Guest Session – Viewing Partner: <partner>" with its type, read only, Return to Super Admin and End Guest Session`,
        text.includes(`Guest Session – Viewing Partner: ${NAMES[kind]}`) && text.includes(`Partner type: ${TYPE[kind]}`) && text.includes("Read only") &&
          JSON.stringify(ways) === JSON.stringify(["Return to Super Admin", "End Guest Session"]),
        `${text} | ${ways}`,
      );
      check(`L3 ${kind}: no ticket in the address or the history`, !report.tickets.some((t) => page.url().includes(t)), page.url());
      const cookies = await page.cookies(PARTNER);
      const sessionCookie = cookies.find((c) => /session/i.test(c.name));
      check(`L4 ${kind}: the partner host's session cookie is host-only and HttpOnly`, sessionCookie && !sessionCookie.domain.startsWith(".") && sessionCookie.domain === "partner.workeasy360.com" && sessionCookie.httpOnly, JSON.stringify(sessionCookie && { name: sessionCookie.name, domain: sessionCookie.domain, httpOnly: sessionCookie.httpOnly }));
      const write = await page.evaluate(async (id) => {
        const csrf = document.cookie.split("; ").find((c) => /csrf/i.test(c))?.split("=")[1] ?? "";
        const r = await fetch(`/api/v1/partner/${id}/customers`, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify({ name: "Acme" }) });
        return { status: r.status, body: await r.json().catch(() => ({})) };
      }, IDS[kind]);
      check(`L5 ${kind}: a write is refused by the backend`, write.status === 403 && write.body.reason === "guest_read_only", JSON.stringify(write));
      const adminApi = await page.evaluate(async () => (await fetch("/api/v1/admin/partners/resellers")).status);
      check(`L6 ${kind}: no Admin API on the partner host`, adminApi === 404, adminApi);
      const other = kind === "reseller" ? IDS.affiliate : IDS.reseller;
      const otherStatus = await page.evaluate(async (id) => (await fetch(`/api/v1/partner/${id}/dashboard`)).status, other);
      check(`L7 ${kind}: another partner's data is not reachable`, otherStatus === 404, otherStatus);
      if (kind === "reseller") {
        const violations = await axe(page);
        check("L8 the guest dashboard: axe WCAG 2.1 AA", violations.length === 0, violations.join("; "));
        await page.setViewport({ width: 390, height: 844 });
        await page.reload({ waitUntil: "networkidle2" });
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        const bannerVisible = await page.$(BANNER);
        await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
        await sleep(300);
        const held = await page.evaluate((sel) => {
          const b = document.querySelector(sel);
          if (!b) return false;
          const r = b.getBoundingClientRect();
          return r.top >= 0 && r.bottom <= window.innerHeight;
        }, BANNER);
        check("L9 the guest dashboard at 390 px: banner shown, held in view while scrolling, no sideways scroll", bannerVisible && held && overflow <= 0, `overflow=${overflow} held=${held}`);
        await page.screenshot({ path: path.join(OUT, "guest-reseller-390.png"), fullPage: false });
        await page.setViewport({ width: 1366, height: 900 });
        await page.reload({ waitUntil: "networkidle2" });
        await page.screenshot({ path: path.join(OUT, "guest-reseller-1366.png"), fullPage: false });
      }
      // Reseller and affiliate: Return to Super Admin (the list, its search kept).
      // White label: End Guest Session (that partner's own page in the Admin).
      const way = kind === "white_label" ? "End Guest Session" : "Return to Super Admin";
      await page.waitForSelector(`${BANNER} button`);
      await Promise.all([page.waitForNavigation({ waitUntil: "networkidle2", timeout: 30000 }), clickIn(page, `${BANNER} button`, way)]);
      if (kind === "white_label") {
        const back = new URL(page.url());
        check(`X1 ${kind}: End Guest Session opens the partner's page in the Admin`, back.host === "admin.workeasy360.com" && back.pathname === `/admin/partners/${IDS[kind]}`, page.url());
      } else {
        await page.waitForFunction(() => new URLSearchParams(location.search).has("q"), { timeout: 15000 }).catch(() => {});
        const back = new URL(page.url());
        const search = await page.$eval('input[type="search"], [role="search"] input', (i) => i.value).catch(() => "");
        check(
          `X1 ${kind}: Return to Super Admin opens the list it started from, its search kept`,
          back.host === "admin.workeasy360.com" && back.pathname === PAGE[kind] && back.searchParams.get("q") === NAMES[kind] && !back.searchParams.has("guest") && search === NAMES[kind],
          `${page.url()} search=${search}`,
        );
      }
      const titles = await h1(page);
      check(`X2 ${kind}: the Admin session is still signed in`, kind === "white_label" ? titles.includes(NAMES[kind]) : titles.some((t) => /partners$/.test(t)), titles.join("|"));
      const partnerSession = await session(browser, PARTNER);
      check(`X3 ${kind}: nothing of the guest view stays signed in on the partner host`, partnerSession.authenticated === false, JSON.stringify(partnerSession));
    }

    // --- the partner's own page: its header actions ----------------------------------------
    const searched = `${PAGE.reseller}?q=${encodeURIComponent(NAMES.reseller)}`;
    await page.goto(`${ADMIN}${searched}`, { waitUntil: "networkidle2" });
    const view = await page.waitForSelector(`a[aria-label="View ${NAMES.reseller}"]`, { timeout: 20000 });
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle2", timeout: 30000 }).catch(() => {}), view.click()]);
    await page.waitForSelector('[role="tablist"]', { timeout: 20000 });
    const head = await page.evaluate(() => {
      const header = document.querySelector("main header");
      const tabs = document.querySelector('[role="tablist"]');
      const controls = header ? [...header.querySelectorAll("a, button")].map((el) => ({ text: el.textContent.trim(), href: el.getAttribute("href") })) : [];
      const guest = header ? [...header.querySelectorAll("button")].find((b) => b.textContent.trim() === "Login as Guest") : null;
      return {
        controls: controls.filter((c) => ["Back to Partners", "Edit Partner", "Login as Guest"].includes(c.text)),
        aboveTabs: Boolean(guest && tabs && guest.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING),
      };
    });
    check(
      "D1 the partner's page: Back to Partners, Edit Partner and Login as Guest at the top, above the tabs",
      JSON.stringify(head.controls.map((c) => c.text)) === JSON.stringify(["Back to Partners", "Edit Partner", "Login as Guest"]) && head.aboveTabs,
      JSON.stringify(head),
    );
    const backHref = head.controls[0]?.href ?? "";
    check(
      "D2 Back to Partners opens the list it came from, its search kept",
      backHref.split("?")[0] === PAGE.reseller && new URLSearchParams(backHref.split("?")[1] ?? "").get("q") === NAMES.reseller,
      backHref,
    );
    await clickIn(page, "main header button", "Login as Guest");
    await page.waitForSelector('[role="dialog"]');
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle2", timeout: 30000 }), clickIn(page, '[role="dialog"] button', "Start guest session")]);
    check("D3 Login as Guest from the partner's page opens the guest session", new URL(page.url()).host === "partner.workeasy360.com", page.url());
    await page.waitForSelector(`${BANNER} button`);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle2", timeout: 30000 }), clickIn(page, `${BANNER} button`, "Return to Super Admin")]);
    check("D4 Return to Super Admin goes back to the partner's page", new URL(page.url()).pathname === `/admin/partners/${IDS.reseller}`, page.url());
    const afterDetail = await session(browser, PARTNER);
    check("D5 nothing of the guest view stays signed in on the partner host", afterDetail.authenticated === false, JSON.stringify(afterDetail));

    // --- Manage Guest Access, and revoking a live view from another tab --------------------
    const guestTab = await browser.newPage();
    watch(guestTab);
    await guestTab.goto(`${ADMIN}${PAGE.reseller}`, { waitUntil: "networkidle2" });
    await startGuest(guestTab, NAMES.reseller);
    check("V0 a second tab is in a guest session", new URL(guestTab.url()).host === "partner.workeasy360.com", guestTab.url());
    await page.bringToFront();
    await page.goto(`${ADMIN}${PAGE.reseller}`, { waitUntil: "networkidle2" });
    await rowMenu(page, NAMES.reseller);
    await chooseMenuItem(page, NAMES.reseller, "Manage Guest Access");
    const dialog = await page.waitForSelector('[role="dialog"]');
    await page.waitForSelector('[role="dialog"] table');
    const statuses = await page.$$eval('[role="dialog"] table tbody tr', (rows) => rows.map((r) => r.innerText.replace(/\s+/g, " ")));
    check("V1 Manage Guest Access lists who looked and how each ended", statuses.length >= 2 && statuses[0].includes("Active") && statuses.some((s) => s.includes("Exited")), statuses.join(" / "));
    const dialogViolations = await axe(page, '[role="dialog"]');
    check("V2 the dialog: axe WCAG 2.1 AA", dialogViolations.length === 0, dialogViolations.join("; "));
    await page.screenshot({ path: path.join(OUT, "manage-guest-access.png") });
    const revoke = await dialog.$("table tbody tr button");
    await revoke.click();
    await page.waitForFunction(() => document.querySelector('[role="dialog"]')?.innerText.includes("Revoked"), { timeout: 15000 });
    check("V3 revoking says so in the list", true);
    await guestTab.bringToFront();
    await guestTab.reload({ waitUntil: "networkidle2" });
    const afterRevoke = await session(browser, PARTNER);
    check("V4 the revoked guest session is signed out on its next request", afterRevoke.authenticated === false && !(await guestTab.$(BANNER)), `${guestTab.url()} ${JSON.stringify(afterRevoke)}`);
    await guestTab.close();

    // --- New Partner asks for the Partner Email -----------------------------------------
    await page.goto(`${ADMIN}/admin/partners/new?type=reseller`, { waitUntil: "networkidle2" });
    await page.waitForSelector('[role="dialog"]');
    const formText = await page.$eval('[role="dialog"]', (d) => d.innerText);
    check("P1 New partner asks for the Partner Email, not an Admin Email", formText.includes("Partner Email") && !/admin email/i.test(formText), formText.slice(0, 200));

    check("Z1 no CSP violation and no ticket in any URL", report.csp.length === 0, report.csp.join(" | "));
    check("Z2 no console error", report.consoleErrors.length === 0, report.consoleErrors.join(" | "));
  } catch (err) {
    check("RUN completed without an exception", false, err.stack);
  } finally {
    report.tickets = report.tickets.length;
    fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(report, null, 2));
    const failed = report.checks.filter((c) => !c.ok).length;
    console.log(`${report.checks.length - failed} passed, ${failed} failed`);
    await browser.close();
    process.exit(failed ? 1 : 0);
  }
})();
