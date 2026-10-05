// Phase 12 load test (Steps 25-26) - k6, against the staging edge over TLS.
//
//   docker run --rm --network <stack>_public --add-host <canonical>:<edge ip> \
//     -e SSL_CERT_FILE=/data/caddy-root.crt -e LEVEL=100 -e DURATION=90s \
//     -v <scripts>:/scripts -v <data>:/data grafana/k6 run /scripts/load.js
//
// Representative, non-destructive flows only (the brief: never hammer purchase):
//   session, My Products / launcher directory, public catalog, subscription read,
//   product launch (entitlement + seat verification, JSON mode), partner customer
//   list, admin customer list. Every customer VU signs in once as its own seeded
//   account (dedicated test data, scripts/phase12_staging/seed_load.py).
//
// Staff: since D-037 a staff session must verify a second factor, so the staff VU
// signs in and steps up with a TOTP code (STAFF_TOTP, the staging account's
// secret). One staff VU: a code is accepted once, so two VUs cannot share one.

import http from "k6/http";
import { check, sleep } from "k6";
import { SharedArray } from "k6/data";
import crypto from "k6/crypto";

const HOST = __ENV.HOST || "app.staging.workeasy360.com";
const BASE = `https://${HOST}`;
const LEVEL = parseInt(__ENV.LEVEL || "50", 10);
const DURATION = __ENV.DURATION || "90s";
const PASSWORD = __ENV.LOAD_PASSWORD;

const users = new SharedArray("users", () => JSON.parse(open("/data/load-users.json")));
const partner = JSON.parse(open("/data/load-partner.json"));

export const options = {
  discardResponseBodies: false,
  // Each VU signs in once and keeps its session, like a browser. k6 otherwise
  // empties the cookie jar every iteration - the first run measured 59 %
  // "failures" that were only the harness signing itself out.
  noCookiesReset: true,
  scenarios: {
    customers: { executor: "constant-vus", exec: "customer", vus: LEVEL, duration: DURATION },
    partner: { executor: "constant-vus", exec: "partnerList", vus: 2, duration: DURATION },
    staff: { executor: "constant-vus", exec: "adminList", vus: 1, duration: DURATION },
  },
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max", "count"],
  // Informational only: a threshold per endpoint tag is what makes k6 report
  // that endpoint's own latency and error rate in the summary.
  thresholds: Object.fromEntries(
    ["session", "login", "my_products", "catalog", "subscription_read", "launch_entitlement",
      "partner_customers", "admin_customers", "step_up"].flatMap((name) => [
      [`http_req_duration{name:${name}}`, ["p(95)<60000"]],
      [`http_req_failed{name:${name}}`, ["rate<1"]],
    ]),
  ),
};

function headers(extra) {
  const jar = http.cookieJar().cookiesForURL(BASE);
  const csrf = jar.w360_csrf ? jar.w360_csrf[0] : "";
  return Object.assign(
    {
      "Content-Type": "application/json",
      Accept: "application/json",
      Origin: BASE,
      Referer: `${BASE}/`,
      "X-CSRFToken": csrf,
    },
    extra || {},
  );
}

function signIn(email, password) {
  http.get(`${BASE}/api/v1/auth/session`, { tags: { name: "session" } });
  const r = http.post(`${BASE}/api/v1/auth/login`, JSON.stringify({ email, password }), {
    headers: headers(),
    tags: { name: "login" },
  });
  check(r, { "login 200": (x) => x.status === 200 });
  return r.status === 200;
}

// RFC 6238 TOTP (SHA-1, 30 s, 6 digits), checked against the RFC test vectors.
function base32Bytes(text) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = 0;
  let value = 0;
  const out = [];
  for (const ch of text.replace(/=+$/, "").toUpperCase()) {
    const idx = alphabet.indexOf(ch);
    if (idx < 0) continue;
    value = ((value << 5) | idx) & 0xffff;
    bits += 5;
    if (bits >= 8) {
      out.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return new Uint8Array(out).buffer;
}

function totp(secret, at) {
  let counter = Math.floor(at / 30);
  const msg = new Uint8Array(8);
  for (let i = 7; i >= 0; i--) {
    msg[i] = counter % 256;
    counter = Math.floor(counter / 256);
  }
  const mac = new Uint8Array(crypto.hmac("sha1", base32Bytes(secret), msg.buffer, "binary"));
  const off = mac[mac.length - 1] & 0x0f;
  const code = ((mac[off] & 0x7f) << 24) | (mac[off + 1] << 16) | (mac[off + 2] << 8) | mac[off + 3];
  return String(code % 1000000).padStart(6, "0");
}

let signedIn = false;
let me = null;

export function customer() {
  if (!signedIn) {
    me = users[(__VU - 1) % users.length];
    signedIn = signIn(me.email, PASSWORD);
    if (!signedIn) {
      sleep(1);
      return;
    }
  }
  const s = me.slug;
  const t = (name) => ({ tags: { name }, headers: headers() });
  const results = [
    http.get(`${BASE}/api/v1/auth/session`, t("session")),
    http.get(`${BASE}/api/v1/orgs/${s}/apps`, t("my_products")),
    http.get(`${BASE}/api/v1/public/catalog?currency=GBP`, t("catalog")),
    http.get(`${BASE}/api/v1/orgs/${s}/subscriptions`, t("subscription_read")),
    http.post(`${BASE}/api/v1/orgs/${s}/apps/easy_crm/launch`, null, t("launch_entitlement")),
  ];
  check(results[1], { "apps 200": (x) => x.status === 200 });
  check(results[4], { "launch 200": (x) => x.status === 200 });
  sleep(1);
}

let partnerIn = false;
export function partnerList() {
  if (!partnerIn) partnerIn = signIn(partner.email, PASSWORD);
  const r = http.get(`${BASE}/api/v1/partner/${partner.partner_id}/customers`, {
    tags: { name: "partner_customers" },
    headers: headers(),
  });
  check(r, { "partner customers 200": (x) => x.status === 200 });
  sleep(1);
}

let staffIn = false;
export function adminList() {
  if (!staffIn) {
    staffIn = signIn(__ENV.STAFF_EMAIL, __ENV.STAFF_PASSWORD);
    if (staffIn) {
      const v = http.post(
        `${BASE}/api/v1/auth/mfa/step-up`,
        JSON.stringify({ code: totp(__ENV.STAFF_TOTP, Date.now() / 1000) }),
        { headers: headers(), tags: { name: "step_up" } },
      );
      staffIn = check(v, { "staff step-up 200": (x) => x.status === 200 });
    }
    if (!staffIn) {
      sleep(1);
      return;
    }
  }
  const r = http.get(`${BASE}/api/v1/admin/customers`, {
    tags: { name: "admin_customers" },
    headers: headers(),
  });
  check(r, { "admin customers 200": (x) => x.status === 200 });
  sleep(1);
}

export function handleSummary(data) {
  return { [`/data/k6-summary-${LEVEL}.json`]: JSON.stringify(data, null, 1) };
}
