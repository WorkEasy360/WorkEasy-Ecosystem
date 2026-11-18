import { Suspense } from "react";
import { headers } from "next/headers";
import { SignupScreen } from "@/features/auth/SelfService";
import { StoreSignUpScreen } from "@/features/store/StoreAuth";
import { portalForHost, readPortalOrigins } from "@/lib/portals";

/** Public (Phase 9). Suspense: the page reads `?ref=` and `?plan=`. On the Store's
 * own host (ADR-065) it is the Store's sign-up, in the Store's frame. */
export default async function SignupPage() {
  const origins = readPortalOrigins(process.env);
  const host = (await headers()).get("host") ?? "";
  if (origins?.store && portalForHost(host, origins) === "store") return <StoreSignUpScreen />;
  return (
    <Suspense fallback={<p aria-live="polite">Loading…</p>}>
      <SignupScreen />
    </Suspense>
  );
}
