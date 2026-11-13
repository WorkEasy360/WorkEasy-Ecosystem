import { headers } from "next/headers";
import { BrandedLanding } from "@/features/auth/BrandedLanding";
import { signInHandoffUrl } from "@/features/auth/handoff";
import { LoginScreen } from "@/features/auth/LoginScreen";
import { StoreSignInScreen } from "@/features/store/StoreAuth";
import { portalForHost, readPortalOrigins } from "@/lib/portals";

/**
 * Where a person signs in - and, on a partner's custom domain, where they are
 * handed over to the canonical sign-in page instead (Phase 12, D-035 decision
 * B, ADR-039).
 *
 * Multi-portal mode (ADR-059): the Admin, Partner and customer portals each
 * serve their own sign-in form, which signs in on that host directly; the
 * central sign-in host serves it only for a product sign-in (`?resume=`). Any
 * host that is none of the platform's portals is a partner's white-label
 * domain and renders the branded landing, handing over to the customer
 * portal's sign-in.
 *
 * Single-host mode: `W360_CANONICAL_HOST` is the platform's own host name
 * (runtime, server-side only). When it is set and this request arrived on any
 * other host, that host is a partner's white-label domain. Unset (development,
 * tests), every host is canonical.
 *
 * Either way no credential may be entered on - or any session cookie set for -
 * a host a partner controls. The `Host` header only chooses which of two
 * *public* pages to render, so a spoofed one gains nothing: neither page
 * trusts it for anything else.
 */
export default async function LoginPage() {
  const host = ((await headers()).get("host") ?? "").split(":")[0].toLowerCase();
  const origins = readPortalOrigins(process.env);
  if (origins) {
    const requestHost = (await headers()).get("host") ?? "";
    const portal = portalForHost(requestHost, origins);
    if (portal === null) {
      return <BrandedLanding handoffUrl={signInHandoffUrl(origins.app, host)} />;
    }
    // ADR-065: the Store signs its customers in in its own frame (a product sign-in
    // never comes here - it is the central sign-in host's).
    if (portal === "store") return <StoreSignInScreen />;
    return <LoginScreen portal={portal} />;
  }
  const canonical = (process.env.W360_CANONICAL_HOST ?? "").trim().toLowerCase();
  if (canonical && host && host !== canonical) {
    return <BrandedLanding handoffUrl={signInHandoffUrl(`https://${canonical}`, host)} />;
  }
  return <LoginScreen />;
}
