import type { Metadata } from "next";
import { Plus_Jakarta_Sans } from "next/font/google";
import { PortalsProvider } from "@/lib/portalContext";
import { readPortalOrigins } from "@/lib/portals";
import "./globals.css";

/** The website's typeface (workeasy360.com). `next/font` downloads it at build
 * time and serves it from `/_next/static/media`, so the browser never contacts
 * Google and the CSP's `font-src 'self'` holds. Exposed as a variable that
 * globals.css puts first in `--w360-font`. */
const brandFont = Plus_Jakarta_Sans({
  subsets: ["latin", "latin-ext"],
  display: "swap",
  variable: "--w360-font-brand",
});

export const metadata: Metadata = {
  title: "Workeasy360",
  description: "One ecosystem to run your entire business.",
};

/**
 * Every route in this app is rendered per request, and that is a **security**
 * setting, not a performance one.
 *
 * `src/proxy.ts` emits a per-request nonce in `script-src` alongside
 * `'strict-dynamic'`. `'strict-dynamic'` makes a CSP3 browser ignore `'self'`
 * and every host-source in that directive, so a `<script>` tag is loadable
 * **only** if it carries the matching nonce. Next.js injects that nonce while
 * server-rendering a request - so a statically prerendered page, generated at
 * build time when no request and no nonce exist, ships script tags with no
 * nonce at all and every one of them is blocked. The page then renders as an
 * inert shell: no hydration, no session fetch, no working sign-in form.
 *
 * Before this line, `/`, `/login`, `/organizations/select` and `/_not-found`
 * were prerendered (`○ (Static)` in `next build`) while `/o/[orgSlug]/*` were
 * not - which is exactly the set of pages an unauthenticated visitor reaches
 * first. Next.js states the rule directly: "To use a nonce, your page must be
 * dynamically rendered" (`node_modules/next/dist/docs/01-app/02-guides/
 * content-security-policy.md`).
 *
 * Declared once here rather than per route: a route added later must not have
 * to remember this, and a root-layout segment config applies to the whole
 * tree. Nothing in this app benefits from prerendering anyway - every page
 * fetches its own session and organization state client-side after hydration
 * (docs/phase-4/01-frontend-architecture.md), so the prerendered HTML was
 * never more than the same empty shell.
 *
 * Pinned by `src/proxy.test.ts` (source) and
 * `scripts/assert-dynamic-rendering.mjs` (build output).
 */
export const dynamic = "force-dynamic";

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={brandFont.variable}>
      <body>
        <a href="#w360-main" className="w360-skip-link">
          Skip to main content
        </a>
        {/* Runtime configuration, read per request (ADR-043): links to the
            other portals and the sign-in hand-off. Never a permission. */}
        <PortalsProvider origins={readPortalOrigins(process.env)}>{children}</PortalsProvider>
      </body>
    </html>
  );
}
