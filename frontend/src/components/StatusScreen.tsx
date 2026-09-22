/** A full-viewport loading/error/empty state. Used wherever a route has
 * nothing else to render yet (Task 26: loading/empty/error states, never a
 * blank screen). Text-based, not spinner-only, so a screen reader has
 * something to announce (Task 28). */
export function StatusScreen({
  role = "status",
  children,
}: {
  role?: "status" | "alert";
  children: React.ReactNode;
}) {
  return (
    <div
      role={role}
      aria-live={role === "alert" ? "assertive" : "polite"}
      style={{
        minHeight: "100dvh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "var(--w360-space-5)",
        textAlign: "center",
        color: "var(--w360-text-muted)",
      }}
    >
      {children}
    </div>
  );
}
