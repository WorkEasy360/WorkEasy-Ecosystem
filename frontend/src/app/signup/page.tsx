"use client";

import { Suspense } from "react";
import { SignupScreen } from "@/features/auth/SelfService";

/** Public (Phase 9). Suspense: the page reads `?ref=` and `?plan=`. */
export default function SignupPage() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading…</p>}>
      <SignupScreen />
    </Suspense>
  );
}
