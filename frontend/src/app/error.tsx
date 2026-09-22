"use client";

import { useEffect } from "react";

/** Task 26: a failed section must never take down the whole shell. This is
 * the outermost net - route-level error.tsx files catch more specifically
 * where it matters (the launcher). */
export default function GlobalError({
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
    <html lang="en">
      <body>
        <div
          role="alert"
          style={{
            minHeight: "100dvh",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexDirection: "column",
            gap: 16,
            fontFamily: "sans-serif",
            padding: 24,
            textAlign: "center",
          }}
        >
          <p>Something went wrong loading Workeasy360.</p>
          <button onClick={() => reset()} style={{ padding: "8px 16px" }}>
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}
