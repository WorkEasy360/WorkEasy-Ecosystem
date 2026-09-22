"use client";

import { useSession } from "@/lib/auth/useSession";

/**
 * Task 38: shared identity information only. No product-specific profile
 * settings live here - those belong to the product that owns them.
 */
export default function ProfilePage() {
  const session = useSession();

  return (
    <div>
      <h1 style={{ fontSize: 22, marginBottom: "var(--w360-space-5)" }}>Your profile</h1>
      <div className="w360-card" style={{ padding: "var(--w360-space-5)", maxWidth: 480 }}>
        {session.status === "loading" && <p>Loading…</p>}
        {session.status === "ready" && session.session.authenticated && (
          <dl style={{ display: "grid", gap: "var(--w360-space-4)", margin: 0 }}>
            <div>
              <dt className="w360-label">Name</dt>
              <dd style={{ margin: 0 }}>{session.session.name || "—"}</dd>
            </div>
            <div>
              <dt className="w360-label">Email</dt>
              <dd style={{ margin: 0 }}>{session.session.email}</dd>
            </div>
          </dl>
        )}
      </div>
    </div>
  );
}
