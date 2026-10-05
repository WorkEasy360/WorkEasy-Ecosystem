"use client";

import { useEffect } from "react";

/**
 * Task 26: a failed section must never take down the whole shell. This is
 * the net for the routes that sit outside the organization layout - `/`,
 * `/login`, `/organizations/select`; `/o/[orgSlug]/error.tsx` catches more
 * specifically inside the shell, where the header and navigation keep
 * working.
 *
 * No `<html>`/`<body>` here: this boundary renders *inside* the root layout,
 * so emitting them produces a nested document. Those tags belong only in
 * `global-error.tsx`, which replaces the root layout - a different file, for
 * the case where the layout itself is what threw, and one Next.js already
 * provides a default for.
 */
export default function RouteError({
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
    <div
      role="alert"
      style={{
        minHeight: "100dvh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "column",
        gap: 16,
        padding: 24,
        textAlign: "center",
      }}
    >
      <p>Something went wrong loading Workeasy360.</p>
      <button className="w360-btn w360-btn-secondary" onClick={() => reset()}>
        Try again
      </button>
    </div>
  );
}
