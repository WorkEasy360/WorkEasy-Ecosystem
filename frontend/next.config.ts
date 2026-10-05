import type { NextConfig } from "next";

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
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${BACKEND_ORIGIN}/api/:path*` },
    ];
  },
};

export default nextConfig;
