import { headers } from "next/headers";
import { ForgotPasswordScreen } from "@/features/auth/SelfService";
import { portalForHost, readPortalOrigins } from "@/lib/portals";

/** Public (Phase 9). On the Admin portal it wears the Admin sign-in's frame:
 * the host says so, or `?mode=admin` in single-host development. */
export default async function ForgotPasswordPage({ searchParams }: { searchParams: Promise<{ mode?: string }> }) {
  const origins = readPortalOrigins(process.env);
  const host = (await headers()).get("host") ?? "";
  const admin = origins ? portalForHost(host, origins) === "admin" : (await searchParams).mode === "admin";
  return <ForgotPasswordScreen admin={admin} />;
}
