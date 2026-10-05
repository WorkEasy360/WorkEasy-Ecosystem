import { headers } from "next/headers";
import { BrandedLanding } from "@/features/auth/BrandedLanding";
import { signInHandoffUrl } from "@/features/auth/handoff";
import { LoginScreen } from "@/features/auth/LoginScreen";

/**
 * Where a person signs in - and, on a partner's custom domain, where they are
 * handed over to the canonical sign-in page instead (Phase 12, D-035 decision
 * B, ADR-039).
 *
 * `W360_CANONICAL_HOST` is the platform's own host name (runtime, server-side
 * only). When it is set and this request arrived on any other host, that host
 * is a partner's white-label domain (the edge serves no other name to this
 * app): the page renders the branded landing, never a sign-in form, because
 * no credential may be entered on - or any session cookie set for - a host a
 * partner controls. Unset (development, tests), every host is canonical.
 *
 * The `Host` header only chooses which of two *public* pages to render, so a
 * spoofed one gains nothing: neither page trusts it for anything else.
 */
export default async function LoginPage() {
  const canonical = (process.env.W360_CANONICAL_HOST ?? "").trim().toLowerCase();
  const host = ((await headers()).get("host") ?? "").split(":")[0].toLowerCase();
  if (canonical && host && host !== canonical) {
    return <BrandedLanding handoffUrl={signInHandoffUrl(`https://${canonical}`, host)} />;
  }
  return <LoginScreen />;
}
