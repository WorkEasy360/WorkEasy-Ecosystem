/** A full-viewport loading/error/empty state. Used wherever a route has
 * nothing else to render yet (Task 26: loading/empty/error states, never a
 * blank screen). Text-based, not spinner-only, so a screen reader has
 * something to announce (Task 28).
 *
 * Always a whole page, never nested in the shell, so it is the page's
 * `<main>` - and the target of the layout's "skip to content" link, which
 * otherwise pointed at nothing on these screens (Phase 5 final audit, axe in
 * the production build). The live region sits inside it, because a `role`
 * on `<main>` itself would replace its landmark role. */
export function StatusScreen({
  role = "status",
  children,
}: {
  role?: "status" | "alert";
  children: React.ReactNode;
}) {
  return (
    <main
      id="w360-main"
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
      <div role={role} aria-live={role === "alert" ? "assertive" : "polite"}>
        {children}
      </div>
    </main>
  );
}
