"use client";

import { ResetPasswordScreen } from "@/features/auth/SelfService";

/** Public (2026-11-10): the link in a new partner's invitation email,
 * `/partner/accept-invitation#token=...`. The person sets their own password, then
 * signs in to the partner portal to send their KYC application. Accepting signs
 * in nobody and activates nothing (`proxy.ts`, PUBLIC_PATHS). */
export default function Page() {
  return <ResetPasswordScreen invitation />;
}
