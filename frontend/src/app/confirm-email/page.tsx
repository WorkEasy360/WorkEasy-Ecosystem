import { headers } from "next/headers";
import { ConfirmEmailScreen } from "@/features/auth/SelfService";
import { portalForHost, readPortalOrigins } from "@/lib/portals";

/** Public (ADR-066): the emailed change-of-sign-in-email link,
 * `/confirm-email#token=...`, in the frame of the portal it was asked on. */
export default async function ConfirmEmailPage({ searchParams }: { searchParams: Promise<{ mode?: string }> }) {
  const origins = readPortalOrigins(process.env);
  const host = (await headers()).get("host") ?? "";
  const portal = origins ? portalForHost(host, origins) : null;
  const mode = origins ? null : (await searchParams).mode;
  const admin = portal === "admin" || mode === "admin";
  const partner = portal === "partner" || mode === "partner";
  const user = portal === "app" || portal === "store" || mode === "user";
  return <ConfirmEmailScreen admin={admin} partner={partner} user={user} />;
}
