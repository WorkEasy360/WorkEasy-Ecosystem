"use client";

import { useEffect } from "react";

/**
 * Task 25/26: a failed section inside the shell (a launcher fetch that
 * throws instead of being caught, a rendering bug in one settings page)
 * must not take the whole platform shell down with it. This boundary sits
 * *inside* the org layout, so the header/nav/org switcher keep working
 * even when the page content under it fails.
 */
export default function OrgSectionError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div role="alert" className="w360-card" style={{ padding: "var(--w360-space-5)" }}>
      <p>This section couldn&apos;t be loaded.</p>
      <button className="w360-btn w360-btn-secondary" onClick={() => reset()}>
        Try again
      </button>
    </div>
  );
}
