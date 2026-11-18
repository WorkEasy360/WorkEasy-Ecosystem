import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

/**
 * Every old Admin address still works (admin-ux phase 1, the brief's hard rule 2):
 * each one is visited and must send the browser to its new home with the right tab
 * or filter chosen - path and query exactly - and that home must be a real page.
 * A redirecting page this list forgets fails the last test.
 */

class Redirected extends Error {
  constructor(readonly to: string) {
    super(`redirect ${to}`);
  }
}

vi.mock("next/navigation", () => ({
  redirect: (to: string) => {
    throw new Redirected(to);
  },
}));

const APP = path.resolve(__dirname, "../../app");
const ADMIN = path.join(APP, "admin");

type Params = Record<string, string | string[] | undefined>;
type PageModule = { default: (props: { params: Promise<Record<string, string>>; searchParams: Promise<Params> }) => unknown };

/** The page file an address is served by, dynamic segments included. */
function pageFileOf(address: string): string | null {
  const segments = address.replace(/^\/admin/, "").split("/").filter(Boolean);
  let dir = ADMIN;
  for (const segment of segments) {
    const exact = path.join(dir, segment);
    if (existsDir(exact)) {
      dir = exact;
      continue;
    }
    const dynamic = readdirSync(dir).find((name) => /^\[[^\].]+\]$/.test(name) && existsDir(path.join(dir, name)));
    if (!dynamic) return null;
    dir = path.join(dir, dynamic);
  }
  const file = path.join(dir, "page.tsx");
  return existsFile(file) ? file : null;
}

function existsDir(p: string): boolean {
  try {
    return statSync(p).isDirectory();
  } catch {
    return false;
  }
}

function existsFile(p: string): boolean {
  try {
    return statSync(p).isFile();
  } catch {
    return false;
  }
}

/** Visit `address` (with its query) and say where it sends the browser. */
async function visit(address: string): Promise<string> {
  const url = new URL(address, "https://admin.example.com");
  const file = pageFileOf(url.pathname);
  if (!file) throw new Error(`no page serves ${url.pathname}`);
  const relative = path.relative(APP, file).replace(/\\/g, "/").replace(/\.tsx$/, "");
  const mod = (await import(/* @vite-ignore */ `../../app/${relative}`)) as PageModule;
  // Dynamic segments, by name, from the address.
  const params: Record<string, string> = {};
  const fileSegments = path.relative(ADMIN, path.dirname(file)).split(path.sep).filter(Boolean);
  const addressSegments = url.pathname.replace(/^\/admin/, "").split("/").filter(Boolean);
  fileSegments.forEach((segment, i) => {
    const match = /^\[([^\]]+)\]$/.exec(segment);
    if (match) params[match[1]] = addressSegments[i];
  });
  const search: Params = {};
  for (const [key, value] of url.searchParams) {
    const before = search[key];
    search[key] = before === undefined ? value : [...[before].flat(), value];
  }
  try {
    await mod.default({ params: Promise.resolve(params), searchParams: Promise.resolve(search) });
  } catch (error) {
    if (error instanceof Redirected) return error.to;
    throw error;
  }
  throw new Error(`${address} did not redirect`);
}

const PARTNER = "0b8f6a52-6c1e-4f39-9d55-1f0f6f0e2a11";

/** The Step-0 map: every old address -> where it opens now. */
const MAP: [string, string][] = [
  // Partners → Affiliate / Reseller / White Label (ADR-062) -> the Partners list's type chips.
  ["/admin/partners/affiliate", "/admin/partners/all?type=affiliate"],
  ["/admin/partners/reseller", "/admin/partners/all?type=reseller"],
  ["/admin/partners/white-label", "/admin/partners/all?type=white_label"],
  // ... keeping the filters the list understands, never an unknown one.
  [
    "/admin/partners/reseller?q=acme&status=active&profile=incomplete&kyc=ready&unpaid=1",
    "/admin/partners/all?type=reseller&q=acme&status=active&profile=incomplete&kyc=ready",
  ],
  ["/admin/partners/affiliate?status=open", "/admin/partners/all?type=affiliate"],
  // ... and a guest session's return (the server returns to the path alone).
  ["/admin/partners/white-label?guest=ended", "/admin/partners/all?type=white_label&guest=ended"],
  ["/admin/partners/reseller?guest=expired", "/admin/partners/all?type=reseller&guest=expired"],
  ["/admin/partners/reseller?guest=<script>", "/admin/partners/all?type=reseller"],
  // Older still (ADR-048): now in one hop.
  ["/admin/partners/affiliates", "/admin/partners/all?type=affiliate"],
  ["/admin/partners/resellers?q=acme", "/admin/partners/all?type=reseller&q=acme"],
  ["/admin/partners/resellers/new", "/admin/partners/new?type=reseller"],
  ["/admin/partners/white-label/new", "/admin/partners/new?type=white_label"],
  [`/admin/partners/white-label/${PARTNER}`, `/admin/partners/${PARTNER}/profile?step=brand`],
  ["/admin/partners/white-label/not-an-id", "/admin/partners/all"],
  ["/admin/partners/commissions", "/admin/partners/all"],
  ["/admin/partners/payouts?q=acme&status=active", "/admin/partners/all?q=acme&status=active"],
  ["/admin/partners/products", "/admin/partners/all"],
  ["/admin/partners/customers?status=bogus", "/admin/partners/all"],
  // Support › Partner tickets -> Support › Tickets, Who asked: Partners.
  ["/admin/partners/tickets", "/admin/tickets?audience=partner"],
  // The eight report pages -> the one Reports page with that report chosen.
  ["/admin/reports/sales", "/admin/reports?report=sales"],
  ["/admin/reports/payments", "/admin/reports?report=payments"],
  ["/admin/reports/renewals", "/admin/reports?report=renewals"],
  ["/admin/reports/wallet-movements", "/admin/reports?report=wallet-movements"],
  ["/admin/reports/usage", "/admin/reports?report=usage"],
  ["/admin/reports/partner-conversions", "/admin/reports?report=partner-conversions"],
  ["/admin/reports/commissions", "/admin/reports?report=commissions"],
  ["/admin/reports/payouts", "/admin/reports?report=payouts"],
];

describe("old Admin addresses", () => {
  it.each(MAP)("%s opens %s", async (from, to) => {
    expect(await visit(from)).toBe(to);
  });

  it("sends every old address to a page that exists and does not redirect again", async () => {
    for (const [, to] of MAP) {
      const file = pageFileOf(new URL(to, "https://admin.example.com").pathname);
      expect(file, to).not.toBeNull();
      expect(readFileSync(file!, "utf8"), to).not.toMatch(/\bredirect\(/);
    }
  });

  it("covers every page that redirects", () => {
    const redirecting: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const full = path.join(dir, name);
        if (existsDir(full)) walk(full);
        else if (name === "page.tsx" && /\bredirect\(/.test(readFileSync(full, "utf8"))) {
          redirecting.push(`/${path.relative(APP, path.dirname(full)).replace(/\\/g, "/")}`);
        }
      }
    };
    walk(ADMIN);
    const visited = new Set(MAP.map(([from]) => pageFileOf(new URL(from, "https://admin.example.com").pathname)));
    for (const route of redirecting) {
      expect(visited.has(path.join(APP, route, "page.tsx")), route).toBe(true);
    }
  });
});
