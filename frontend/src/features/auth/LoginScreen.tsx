"use client";

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useSession } from "@/lib/auth/useSession";
import { LoginForm } from "@/features/auth/LoginForm";
import { useBranding } from "@/features/controlplane/useBranding";
import brandStyles from "@/features/controlplane/ControlPlane.module.css";

/**
 * A partner host named in `?brand=` (Phase 12, D-035): a visitor handed over
 * from a partner's custom domain to this canonical sign-in page sees that
 * partner's brand here. Only a plain host name is accepted, and it is only a
 * lookup key - the server shows a brand solely for an *active, verified*
 * partner domain, and nothing here ever becomes a redirect target.
 */
export function brandHostParam(raw: string | null): string | undefined {
  const value = (raw ?? "").trim().toLowerCase();
  return /^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$/.test(value)
    ? value
    : undefined;
}

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
      router.replace("/dashboard");
    }
  }, [session, router, searchParams]);

  return null;
}

function Heading() {
  const searchParams = useSearchParams();
  // A partner's white-label brand: the partner domain this visitor was handed
  // over from, or - on a verified partner host - that host itself.
  const brand = useBranding(brandHostParam(searchParams.get("brand")));
  return (
    <div style={{ textAlign: "center", marginBottom: "var(--w360-space-6)" }}>
      {brand?.logo_url?.startsWith("/api/v1/") && (
        // eslint-disable-next-line @next/next/no-img-element -- same-origin, scanned PNG/JPEG
        <img src={brand.logo_url} alt="" className={brandStyles.logo} />
      )}
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>{brand?.brand_name ?? "Workeasy360"}</h1>
      <p style={{ color: "var(--w360-text-muted)", marginTop: "var(--w360-space-2)" }}>
        One ecosystem to run your entire business.
      </p>
    </div>
  );
}

export function LoginScreen() {
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
        <Suspense
          fallback={
            <div style={{ textAlign: "center", marginBottom: "var(--w360-space-6)" }}>
              <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Workeasy360</h1>
            </div>
          }
        >
          <Heading />
        </Suspense>
        <div className="w360-card" style={{ padding: "var(--w360-space-6)" }}>
          <h2 style={{ fontSize: 17, marginTop: 0, marginBottom: "var(--w360-space-5)" }}>Sign in</h2>
          <Suspense fallback={<p>Loading…</p>}>
            <LoginForm />
          </Suspense>
        </div>
      </div>
    </main>
  );
}
