import { headers } from "next/headers";
import { portalForHost, readPortalOrigins } from "@/lib/portals";

/**
 * `robots.txt`, per host (ADR-065). The Digital Store's own host
 * (store.workeasy360.com) invites crawlers to its public pages - the marketplace,
 * products, Recharge, Partners, Help and the policies - and points to its sitemap;
 * a buyer's checkout and confirmation stay out. Every other host of this app (the
 * consoles: everything behind a sign-in, and every URL under /o/ names one
 * customer's organization) says Disallow: / exactly as before.
 */
export async function GET(): Promise<Response> {
  const origins = readPortalOrigins(process.env);
  const host = (await headers()).get("host") ?? "";
  const store = origins?.store && portalForHost(host, origins) === "store";
  const body = store
    ? [
        "User-agent: *",
        "Allow: /$",
        "Allow: /store",
        "Disallow: /store/checkout",
        "Disallow: /store/buy",
        "Disallow: /api/",
        "Disallow: /login",
        "Disallow: /signup",
        "Disallow: /",
        "",
        `Sitemap: ${origins.store}/sitemap.xml`,
        "",
      ].join("\n")
    : [
        "# Every page of this application is behind authentication and every URL",
        "# under /o/ names a specific customer's organization. There is nothing here",
        "# to index, and a crawler following links only produces login redirects.",
        "User-agent: *",
        "Disallow: /",
        "",
      ].join("\n");
  return new Response(body, { headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "public, max-age=3600" } });
}

export const dynamic = "force-dynamic";
