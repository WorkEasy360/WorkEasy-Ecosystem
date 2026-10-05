"use client";

import { AcceptOwnerInvitation } from "@/features/console/AcceptOwnerInvitation";

/** Public: a customer's owner opens the emailed link (`/owner-invitations/accept#token=...`).
 * Listed in `src/proxy.ts`'s public paths - the person has no password yet. */
export default function AcceptOwnerInvitationPage() {
  return <AcceptOwnerInvitation />;
}
