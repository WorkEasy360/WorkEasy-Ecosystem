"use client";

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useSession } from "@/lib/auth/useSession";
import { LoginForm } from "@/features/auth/LoginForm";

function AlreadySignedInRedirect() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const session = useSession();

  useEffect(() => {
    if (session.status !== "ready" || !session.session.authenticated) return;
    // Already signed in. A resume link still needs completing (a second tab
    // may have logged in first); otherwise go to the ordinary landing logic.
    const resume = searchParams.get("resume");
    if (resume) {
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.href = `/api/v1/integrations/oauth/resume?resume=${encodeURIComponent(resume)}`;
    } else {
      router.replace("/");
    }
  }, [session, router, searchParams]);

  return null;
}

export default function LoginPage() {
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
      <Suspense fallback={null}>
        <AlreadySignedInRedirect />
      </Suspense>
      <div style={{ width: "100%", maxWidth: 380 }}>
        <div style={{ textAlign: "center", marginBottom: "var(--w360-space-6)" }}>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Workeasy360</h1>
          <p style={{ color: "var(--w360-text-muted)", marginTop: "var(--w360-space-2)" }}>
            One ecosystem to run your entire business.
          </p>
        </div>
        <div className="w360-card" style={{ padding: "var(--w360-space-6)" }}>
          <h2 style={{ fontSize: 17, marginTop: 0, marginBottom: "var(--w360-space-5)" }}>
            Sign in
          </h2>
          <Suspense fallback={<p>Loading…</p>}>
            <LoginForm />
          </Suspense>
        </div>
      </div>
    </main>
  );
}
