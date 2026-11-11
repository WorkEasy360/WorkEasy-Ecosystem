import { headers } from "next/headers";
import { ForgotPasswordScreen } from "@/features/auth/SelfService";
import { portalForHost, readPortalOrigins } from "@/lib/portals";

/** Public (Phase 9). On the Admin and Partner portals it wears that portal's sign-in frame:
 * the host says so, or `?mode=admin` / `?mode=partner` in single-host development. */
export default async function ForgotPasswordPage({ searchParams }: { searchParams: Promise<{ mode?: string }> }) {
  const origins = readPortalOrigins(process.env);
  const host = (await headers()).get("host") ?? "";
  const portal = origins ? portalForHost(host, origins) : null;
  const mode = origins ? null : (await searchParams).mode;
  const admin = portal === "admin" || mode === "admin";
  const partner = portal === "partner" || mode === "partner";
  return <ForgotPasswordScreen admin={admin} partner={partner} />;
}
