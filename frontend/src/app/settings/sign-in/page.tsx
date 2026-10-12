"use client";

import { useSession } from "@/lib/auth/useSession";
import { StatusScreen } from "@/components/StatusScreen";
import { SignInPreferencesScreen } from "@/features/signin/SignInPreferencesScreen";

/**
 * The same sign-in preferences outside any organization (ADR-044): where the
 * access chooser on the central sign-in host links to, since a preference
 * belongs to the person and not to the organization they happen to be in.
 * Inside the console it is Security -> Sign-in preferences.
 */
export default function StandaloneSignInPreferencesPage() {
  const session = useSession();
  if (session.status === "loading") return <StatusScreen>Loading…</StatusScreen>;
  if (session.status === "ready" && !session.session.authenticated) {
    return (
      <StatusScreen role="alert">
        <p>
          <a href="/login">Sign in</a> to change your sign-in preferences.
        </p>
      </StatusScreen>
    );
  }
  return (
    <main id="w360-main" className="w360-container" style={{ paddingBlock: "var(--w360-space-7)", maxWidth: 760 }}>
      <SignInPreferencesScreen />
      <p style={{ marginTop: "var(--w360-space-6)" }}>
        <a href="/dashboard">Back to Workeasy360</a>
      </p>
    </main>
  );
}
