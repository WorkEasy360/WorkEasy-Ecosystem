"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "@/lib/auth/useSession";
import { StatusScreen } from "@/components/StatusScreen";
import { CreateOrganizationForm } from "@/features/onboarding/CreateOrganizationForm";

/**
 * First-run flow (Task 2/14): a signed-in user with zero organizations lands
 * here (see app/page.tsx and organizations/select/page.tsx, both of which
 * route an empty chooser result here rather than a dead end) and creates
 * their first organization. Kept deliberately to one step - welcome copy,
 * one field, submit - not a multi-page wizard (Task 14: "keep it simple").
 */
export default function OnboardingPage() {
  const router = useRouter();
  const session = useSession();

  useEffect(() => {
    if (session.status !== "ready") return;
    if (!session.session.authenticated) {
      router.replace("/login");
    }
  }, [session, router]);

  if (session.status === "loading") {
    return <StatusScreen>Loading…</StatusScreen>;
  }
  if (session.status === "error") {
    return (
      <StatusScreen role="alert">
        <p>Workeasy360 could not be reached right now. Please try again shortly.</p>
      </StatusScreen>
    );
  }
  if (!session.session.authenticated) {
    // useEffect above is already redirecting; render nothing rather than a
    // flash of the form.
    return null;
  }

  return (
    <main
      id="w360-main"
      className="w360-container"
      style={{
        minHeight: "100dvh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div style={{ width: "100%", maxWidth: 420 }}>
        <div style={{ textAlign: "center", marginBottom: "var(--w360-space-6)" }}>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Welcome to Workeasy360</h1>
          <p style={{ color: "var(--w360-text-muted)", marginTop: "var(--w360-space-2)" }}>
            Create your organization to get started. You can invite teammates and explore
            available products afterwards.
          </p>
        </div>
        <div className="w360-card" style={{ padding: "var(--w360-space-6)" }}>
          <h2 style={{ fontSize: 17, marginTop: 0, marginBottom: "var(--w360-space-5)" }}>
            Create your organization
          </h2>
          <CreateOrganizationForm />
        </div>
      </div>
    </main>
  );
}
