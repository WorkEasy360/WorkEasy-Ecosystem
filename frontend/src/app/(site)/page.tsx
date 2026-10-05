"use client";

import { LandingPage } from "@/features/site/SitePages";

/** `/` - the public front page, for visitors and signed-in people alike (a
 * signed-in person sees "Go to dashboard" in the header instead of sign-up). */
export default function HomePage() {
  return <LandingPage />;
}
