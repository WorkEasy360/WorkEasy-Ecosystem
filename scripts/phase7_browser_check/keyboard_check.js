// Keyboard-only operation of the Phase 7 flows, against the production build.
// Every action below is driven with Tab / Shift+Tab / typing / Enter only - no
// mouse, no element.click(). It records whether each flow completes and where
// focus lands after each state change (the part a focus-visibility check cannot see).
const puppeteer = require("puppeteer-core");
const crypto = require("crypto");

const BASE = "http://127.0.0.1:3999";
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const PASSWORD = "Browser-Test-Password-1!";
const SECRETS = JSON.parse(process.env.KB_SECRETS);

function totp(base32, offset = 0) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of base32.replace(/=+$/, "")) bits += alphabet.indexOf(c).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g).map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 1000 / 30) + offset));
  const h = crypto.createHmac("sha1", key).update(counter).digest();
  const o = h[h.length - 1] & 0xf;
  return (((h.readUInt32BE(o) & 0x7fffffff) % 1_000_000) + "").padStart(6, "0");
}

const results = [];
function record(flow, step, ok, detail = "") {
  results.push({ flow, step, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${flow} :: ${step}${detail ? "  - " + detail : ""}`);
}

async function focused(page) {
  return page.evaluate(() => {
    const el = document.activeElement;
    if (!el || el === document.body) return { tag: "BODY", text: "", label: "" };
    const label = el.labels && el.labels[0] ? el.labels[0].innerText : el.getAttribute("aria-label") || "";
    return { tag: el.tagName, type: el.type || "", text: (el.innerText || el.value || "").trim().slice(0, 60), label: label.trim().slice(0, 90) };
  });
}

const settle = (ms = 700) => new Promise((r) => setTimeout(r, ms));

/** Press Tab until the focused element satisfies `match`; false if not reached in `max` presses. */
async function tabTo(page, match, max = 120) {
  for (let i = 0; i < max; i++) {
    await page.keyboard.press("Tab");
    const f = await focused(page);
    if (match(f)) return f;
  }
  return null;
}

async function loginByKeyboard(context, email, flow) {
  const page = await context.newPage();
  await page.setViewport({ width: 1280, height: 900 });
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle0" });
  const emailField = await tabTo(page, (f) => f.tag === "INPUT" && f.type === "email");
  record(flow, "Tab reaches the email field", !!emailField);
  await page.keyboard.type(email);
  await page.keyboard.press("Tab");
  const pw = await focused(page);
  record(flow, "Tab moves to the password field", pw.type === "password", JSON.stringify(pw));
  await page.keyboard.type(PASSWORD);
  await Promise.all([page.keyboard.press("Enter"), page.waitForNetworkIdle({ idleTime: 900, timeout: 20000 }).catch(() => {})]);
  const s = await page.evaluate(() => fetch("/api/v1/auth/session").then((r) => r.json()));
  record(flow, "Enter signs in", !!s.authenticated);
  return page;
}

/** Complete the real step-up prompt from the keyboard. */
async function stepUpByKeyboard(page, secret, flow) {
  await settle(900);
  const f = await focused(page);
  const onCode = f.tag === "INPUT" && /verification|authenticator|code/i.test(f.label);
  record(flow, "the MFA prompt takes focus on its code field", onCode, JSON.stringify(f));
  if (!onCode) {
    const reached = await tabTo(page, (x) => x.tag === "INPUT" && /authenticator|code/i.test(x.label), 60);
    if (!reached) return false;
  }
  for (const offset of [0, 1]) {
    await page.keyboard.type(totp(secret, offset));
    await page.keyboard.press("Enter");
    await settle(1500);
    const stillPrompting = await page.evaluate(() => !!document.querySelector('input[autocomplete="one-time-code"]'));
    if (!stillPrompting) return true;
    // A code already used in this 30 s window is refused (replay protection): clear and wait for the next.
    await page.keyboard.down("Control"); await page.keyboard.press("A"); await page.keyboard.up("Control");
    await page.keyboard.press("Backspace");
    await new Promise((r) => setTimeout(r, 31000 - (Date.now() % 30000)));
  }
  return false;
}

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--no-sandbox", "--disable-gpu"] });
  try {
    // --- Super Admin ---------------------------------------------------------------
    const staffCtx = await browser.createBrowserContext();
    const staff = await loginByKeyboard(staffCtx, "staff@browser.test", "admin");

    await staff.goto(`${BASE}/admin`, { waitUntil: "networkidle0" });
    const nav = await tabTo(staff, (f) => f.tag === "A" && f.text === "Payouts");
    record("admin", "Tab reaches the Payouts link in the navigation", !!nav);
    await Promise.all([staff.keyboard.press("Enter"), staff.waitForNetworkIdle({ idleTime: 800, timeout: 20000 }).catch(() => {})]);
    record("admin", "Enter follows the link", staff.url().endsWith("/admin/payouts"), staff.url());

    // Payout approval: the payout was prepared by another staff member.
    const approve = await tabTo(staff, (f) => f.tag === "BUTTON" && f.text === "Approve");
    record("payout approval", "Tab reaches Approve", !!approve);
    await staff.keyboard.press("Enter");
    await settle();
    let f = await focused(staff);
    record("payout approval", "the reason field takes focus", f.tag === "INPUT" && /Reason/.test(f.label), JSON.stringify(f));
    await staff.keyboard.type("Independent review of the batch");
    await staff.keyboard.press("Enter");
    const stepped = await stepUpByKeyboard(staff, SECRETS["staff@browser.test"], "payout approval");
    record("payout approval", "the step-up completes from the keyboard", stepped);
    await settle(1200);
    const approved = await staff.evaluate(() => [...document.querySelectorAll('[role="status"]')].some((n) => /Payout approved/.test(n.textContent)));
    record("payout approval", "the approval is confirmed (role=status)", approved);
    f = await focused(staff);
    record("payout approval", "focus after completion is on the page, not lost to <body>", f.tag !== "BODY", JSON.stringify(f));

    // Staff grant form, including the new optional expiry date.
    await staff.goto(`${BASE}/admin/security`, { waitUntil: "networkidle0" });
    const email = await tabTo(staff, (x) => x.tag === "INPUT" && /email/i.test(x.label));
    record("staff grant", "Tab reaches the email field", !!email);
    await staff.keyboard.type("root@browser.test");
    await staff.keyboard.press("Tab");
    await staff.keyboard.down("Control"); await staff.keyboard.press("A"); await staff.keyboard.up("Control");
    await staff.keyboard.type("platform.staff_audit.view");
    await staff.keyboard.press("Tab");
    await staff.keyboard.type("Quarter-end audit support");
    await staff.keyboard.press("Tab");
    f = await focused(staff);
    record("staff grant", "Tab reaches the expiry date", f.type === "date", JSON.stringify(f));
    const d = new Date(Date.now() + 7 * 86400000);
    const dd = String(d.getDate()).padStart(2, "0"), mm = String(d.getMonth() + 1).padStart(2, "0");
    const iso = `${d.getFullYear()}-${mm}-${dd}`;
    // Chrome's date field takes the digits in the OS locale's order, which is
    // not navigator.language: try day-month-year, then month-day-year.
    for (const digits of [`${dd}${mm}${d.getFullYear()}`, `${mm}${dd}${d.getFullYear()}`]) {
      await staff.keyboard.type(digits);
      if ((await staff.evaluate(() => document.activeElement.value)) === iso) break;
      await staff.keyboard.down("Shift"); await staff.keyboard.press("Tab"); await staff.keyboard.up("Shift");
      await staff.keyboard.press("Tab");
    }
    record("staff grant", "the expiry date is entered by keyboard", (await staff.evaluate(() => document.activeElement.value)) === iso);
    await staff.keyboard.press("Enter");
    await settle(1500);
    const granted = await staff.evaluate(() => [...document.querySelectorAll('[role="status"]')].some((n) => /Permission granted/.test(n.textContent)));
    record("staff grant", "Enter submits the grant", granted);
    const expiryShown = await staff.evaluate(() => [...document.querySelectorAll("tr")].some((r) => /root@browser\.test/.test(r.textContent) && /platform\.staff_audit\.view/.test(r.textContent) && !/No expiry/.test(r.textContent)));
    record("staff grant", "the new grant is listed with its expiry", expiryShown);

    // Commission rule form (the money form that has a UI; adjustments and reversals are API-only).
    await staff.goto(`${BASE}/admin/commissions`, { waitUntil: "networkidle0" });
    const code = await tabTo(staff, (x) => x.tag === "INPUT" && /code/i.test(x.label));
    record("commission rule", "Tab reaches the rule form", !!code);
    await staff.keyboard.type("kb-rule");
    await staff.keyboard.press("Tab");
    await staff.keyboard.down("Control"); await staff.keyboard.press("A"); await staff.keyboard.up("Control");
    await staff.keyboard.type("750");
    await staff.keyboard.press("Tab");
    await staff.keyboard.type("Keyboard pass rule version");
    await staff.keyboard.press("Enter");
    await settle(1500);
    const ruled = await staff.evaluate(() => [...document.querySelectorAll('[role="status"],[role="alert"]')].map((n) => n.textContent).join(" | "));
    record("commission rule", "Enter submits the rule", /created|version|saved/i.test(ruled), ruled.slice(0, 120));

    // --- partner portal -------------------------------------------------------------------
    const partnerCtx = await browser.createBrowserContext();
    const partner = await loginByKeyboard(partnerCtx, "partner@browser.test", "partner");
    await partner.goto(`${BASE}/partner`, { waitUntil: "networkidle0" });
    const pick = await tabTo(partner, (x) => x.tag === "A" && /Browser Partner/.test(x.text));
    record("partner", "Tab reaches the partner in the portal", !!pick);
    await Promise.all([partner.keyboard.press("Enter"), partner.waitForNetworkIdle({ idleTime: 800, timeout: 20000 }).catch(() => {})]);
    const domainsLink = await tabTo(partner, (x) => x.tag === "A" && x.text === "Domains");
    record("partner", "Tab reaches Domains in the navigation", !!domainsLink);
    await Promise.all([partner.keyboard.press("Enter"), partner.waitForNetworkIdle({ idleTime: 800, timeout: 20000 }).catch(() => {})]);
    const host = await tabTo(partner, (x) => x.tag === "INPUT" && /host|domain/i.test(x.label));
    record("domain form", "Tab reaches the hostname field", !!host, host ? host.label : "");
    await partner.keyboard.type("kb.browser-example.com");
    await partner.keyboard.press("Enter");
    const pStepped = await stepUpByKeyboard(partner, SECRETS["partner@browser.test"], "domain form");
    record("domain form", "the step-up completes from the keyboard", pStepped);
    await settle(1500);
    const listed = await partner.evaluate(() => document.body.innerText.includes("kb.browser-example.com"));
    record("domain form", "the requested domain appears", listed);
    f = await focused(partner);
    record("domain form", "focus after completion is on the page, not lost to <body>", f.tag !== "BODY", JSON.stringify(f));
  } finally {
    await browser.close();
  }
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length} passed, ${failed.length} failed`);
})().catch((e) => {
  console.error("FAILED", e);
  process.exit(1);
});
