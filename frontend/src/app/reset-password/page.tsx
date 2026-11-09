import { headers } from "next/headers";
import { ResetPasswordScreen } from "@/features/auth/SelfService";
import { portalForHost, readPortalOrigins } from "@/lib/portals";

/** Public (Phase 9): the emailed link, `/reset-password#token=...`. On the Admin
 * portal it wears the Admin sign-in's frame. */
export default async function ResetPasswordPage({ searchParams }: { searchParams: Promise<{ mode?: string }> }) {
  const origins = readPortalOrigins(process.env);
  const host = (await headers()).get("host") ?? "";
  const admin = origins ? portalForHost(host, origins) === "admin" : (await searchParams).mode === "admin";
  return <ResetPasswordScreen admin={admin} />;
}
