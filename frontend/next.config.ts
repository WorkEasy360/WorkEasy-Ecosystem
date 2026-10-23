import type { NextConfig } from "next";
import { SHORT_ADDRESSES } from "./src/features/partnerpanel/experiences";
import { LEGACY_REDIRECTS } from "./src/features/partnerpanel/menu";

// The Django platform API. Same-origin from the browser's point of view -
// rewrites are a transparent server-side HTTP proxy, so Set-Cookie headers
// (the session, the CSRF cookie) land on THIS origin, not the backend's.
// That is what lets the session cookie stay SameSite=Lax and HttpOnly
// without any cross-origin credentialed-CORS configuration at all (Phase 4
// Task 36: "no wildcard credentialed CORS ... same-origin where practical").
const BACKEND_ORIGIN = process.env.BACKEND_ORIGIN ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // No `X-Powered-By: Next.js` on every response (Phase 12, G-21): it only
  // tells a scanner which framework's advisories to try first.
  poweredByHeader: false,
  // The partner portal used to serve a longer menu (ADR-046). Each old address
  // answers with a permanent redirect to the page that now holds the feature,
  // listed once in features/partnerpanel/menu.ts. Query strings pass through.
  async redirects() {
    return [
      // `:partnerId` is a UUID. A workspace address (`/partner/reseller/...`,
      // ADR-047) is not one, so the old menu's redirects never touch a workspace's
      // own pages.
      ...LEGACY_REDIRECTS.map(({ from, to }) => ({
        source: `/partner/:partnerId([0-9a-fA-F-]{36})${from}`,
        destination: `/partner/:partnerId${to}`,
        permanent: true,
      })),
      // The address a person would guess: /reseller/dashboard, /affiliate/leads.
      // A plain (temporary) redirect to a page that checks the session and the
      // account's grants like any other; the short address opens nothing itself.
      ...SHORT_ADDRESSES.map(({ from, to }) => ({ source: from, destination: to, permanent: false })),
    ];
  },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${BACKEND_ORIGIN}/api/:path*` },
    ];
  },
};

export default nextConfig;
