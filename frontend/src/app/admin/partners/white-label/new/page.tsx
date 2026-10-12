"use client";

import { WhiteLabelSetup } from "@/features/controlplane/WhiteLabelSetup";

/** Partners → White-label → Set up white-label: the ten-step wizard, starting
 * by choosing an existing partner. Nothing is created: the wizard configures a
 * partner that already exists. */
export default function Page() {
  return <WhiteLabelSetup />;
}
