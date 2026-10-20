import { headers } from "next/headers";
import { BrandedLanding } from "@/features/auth/BrandedLanding";
import { signInHandoffUrl } from "@/features/auth/handoff";
import { LoginScreen } from "@/features/auth/LoginScreen";
import { readPartnerPanelUrl } from "@/features/auth/partnerPanel";
import { portalForHost, readPortalOrigins } from "@/lib/portals";

/**
 * Where a person signs in - and, on a partner's custom domain, where they are
 * handed over to the canonical sign-in page instead (Phase 12, D-035 decision
 * B, ADR-039).
 *
 * Multi-portal mode (ADR-043): the central sign-in host serves the form; any
 * host that is none of the platform's portals is a partner's white-label
 * domain and renders the branded landing, handing over to the central sign-in.
 * (The proxy has already sent `/login` on the other portals to the central
 * sign-in.)
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
    if (portalForHost(requestHost, origins) === null) {
      return <BrandedLanding handoffUrl={signInHandoffUrl(origins.auth, host)} />;
    }
    return <LoginScreen partnerPanelUrl={readPartnerPanelUrl(process.env)} />;
  }
  const canonical = (process.env.W360_CANONICAL_HOST ?? "").trim().toLowerCase();
  if (canonical && host && host !== canonical) {
    return <BrandedLanding handoffUrl={signInHandoffUrl(`https://${canonical}`, host)} />;
  }
  return <LoginScreen partnerPanelUrl={readPartnerPanelUrl(process.env)} />;
}
