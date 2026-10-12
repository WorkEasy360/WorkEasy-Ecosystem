"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import { usePortals } from "./portalContext";
import { pageOwner, portalForHost, servesPage } from "./portals";

/**
 * `useRouter()`, aware of the portals (ADR-043).
 *
 * In multi-portal mode a page that another portal serves is a different
 * **origin**: client-side navigation to it would fetch the page's payload
 * across origins, which this app's own CSP (`connect-src 'self'`) refuses. So
 * such a navigation is a full one, straight to the owning portal's origin.
 * `/login` on a portal is a full navigation on this host, so the proxy can set
 * the portal's binding cookie before sending the browser to the central
 * sign-in. Everything else - and everything in single-host mode - is the
 * ordinary client-side router, unchanged.
 *
 * Navigation only: which portal serves a page decides nothing about access.
 */
export function usePortalRouter(): ReturnType<typeof useRouter> {
  const router = useRouter();
  const origins = usePortals();
  return useMemo(() => {
    if (!origins) return router;
    const route = (method: "push" | "replace") => (href: string) => {
      const here = portalForHost(window.location.host, origins);
      const path = href.split(/[?#]/, 1)[0];
      if (here && href.startsWith("/") && !href.startsWith("//")) {
        // A full load on purpose: the proxy answers it, not the client router.
        if (here !== "auth" && path === "/login") return void window.location.assign(href);
        const owner = pageOwner(path);
        if (owner && !servesPage(here, path)) {
          const target = `${origins[owner]}${href}`; // another origin: absolute
          // eslint-disable-next-line @next/next/no-location-assign-relative-destination
          return void (method === "replace" ? window.location.replace(target) : window.location.assign(target));
        }
      }
      router[method](href);
    };
    return { ...router, push: route("push"), replace: route("replace") };
  }, [router, origins]);
}
