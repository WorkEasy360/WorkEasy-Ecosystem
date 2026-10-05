"use client";

import { AcceptInvitation } from "@/features/console/AcceptInvitation";

/** Public: an invitee opens the emailed link (`/invitations/accept#token=...`).
 * Listed in `src/proxy.ts`'s public paths - they may not have an account yet. */
export default function AcceptInvitationPage() {
  return <AcceptInvitation />;
}
