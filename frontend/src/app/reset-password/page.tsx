import { headers } from "next/headers";
import { ResetPasswordScreen } from "@/features/auth/SelfService";
import { portalForHost, readPortalOrigins } from "@/lib/portals";

/** Public (Phase 9): the emailed link, `/reset-password#token=...`. On the Admin
 * and Partner portals it wears that portal's sign-in frame. */
export default async function ResetPasswordPage({ searchParams }: { searchParams: Promise<{ mode?: string }> }) {
  const origins = readPortalOrigins(process.env);
  const host = (await headers()).get("host") ?? "";
  const portal = origins ? portalForHost(host, origins) : null;
  const mode = origins ? null : (await searchParams).mode;
  const admin = portal === "admin" || mode === "admin";
  const partner = portal === "partner" || mode === "partner";
  // The customer portal (2026-11-11): its own sign-in's frame.
  const user = (portal === "app" || portal === "store") || mode === "user";
  return <ResetPasswordScreen admin={admin} partner={partner} user={user} />;
}
