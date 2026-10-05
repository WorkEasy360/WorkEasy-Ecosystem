/**
 * Build-output gate: no route may be statically prerendered.
 *
 * `src/proxy.ts` emits a per-request nonce in `script-src` alongside
 * `'strict-dynamic'`, which makes a CSP3 browser ignore `'self'` and every
 * host-source in that directive - a script tag then loads only if it carries
 * the matching nonce. Next.js injects that nonce while server-rendering a
 * request, so a page generated at build time carries none, and every script
 * on it is blocked. The page renders as an inert shell: no hydration, no
 * session fetch, no working sign-in form.
 *
 * `app/layout.tsx` sets `export const dynamic = "force-dynamic"` for the
 * whole tree, and `src/proxy.test.ts` asserts that line still exists. This
 * script checks the thing that actually matters - what the build produced -
 * because a future `export const dynamic`/`revalidate` on one route, or a
 * Next.js default that changes, would defeat the source-level check while
 * leaving it passing.
 *
 * Run after `next build`, from the `frontend` directory.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const MANIFEST = resolve(process.cwd(), ".next/prerender-manifest.json");

let manifest;
try {
  manifest = JSON.parse(readFileSync(MANIFEST, "utf8"));
} catch (error) {
  console.error(
    `Could not read ${MANIFEST}. Run \`npm run build\` before this check.\n${error.message}`,
  );
  process.exit(1);
}

/**
 * Two entries Next.js always emits, neither of which this rule can reach.
 *
 * `/favicon.ico` is an asset, not a page: no markup, no scripts, nothing for
 * a nonce to apply to.
 *
 * `/_global-error` is Next.js's own last-resort page, rendered *outside* the
 * root layout - which is the whole point of it, since the root layout may be
 * what threw - so the layout's `dynamic` setting cannot apply to it and
 * there is no route file to set one on. The consequence is bounded and
 * acceptable: its static markup still renders the failure message, its
 * "try again" script does not run, and it is only ever reached once the
 * application has already failed hard.
 */
const NOT_A_NONCED_PAGE = new Set(["/favicon.ico", "/_global-error"]);

// `routes` holds fully prerendered paths; `dynamicRoutes` holds prerendered
// parameterised paths. Both are build-time HTML, and both would ship without
// a nonce.
const prerendered = [
  ...Object.keys(manifest.routes ?? {}),
  ...Object.keys(manifest.dynamicRoutes ?? {}),
].filter((route) => !NOT_A_NONCED_PAGE.has(route));

if (prerendered.length > 0) {
  console.error(
    "These routes are statically prerendered, so their script tags carry no\n" +
      "CSP nonce and 'strict-dynamic' will block every one of them in a\n" +
      "browser:\n" +
      prerendered.map((route) => `  ${route}`).join("\n") +
      "\n\nEither keep them dynamic (see frontend/src/app/layout.tsx) or stop\n" +
      "using a nonce-based script-src (see frontend/src/proxy.ts).",
  );
  process.exit(1);
}

console.log("No route is statically prerendered; every page can carry a CSP nonce.");
