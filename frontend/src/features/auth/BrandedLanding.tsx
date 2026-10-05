"use client";

import { useBranding } from "@/features/controlplane/useBranding";
import brandStyles from "@/features/controlplane/ControlPlane.module.css";

/**
 * A partner's custom domain (Phase 12, D-035 decision B - ADR-039).
 *
 * Authentication is deliberately **not** offered here. The domain shows the
 * partner's brand and hands the visitor to the canonical Workeasy360 sign-in
 * page, which presents the same brand; after signing in they are on the
 * canonical host, where their organization's console is branded through its
 * live partner link. So no session cookie, CSRF trust, OIDC redirect or logout
 * ever exists on a partner-controlled hostname - the host a partner can point
 * at anything, or lose control of, never holds a credential.
 *
 * The destination is computed on the server (`handoff.ts`) from the
 * deployment's canonical origin and the request's own host, never from
 * anything in the URL, so the link cannot be turned into an open redirect.
 */
export function BrandedLanding({ handoffUrl }: { handoffUrl: string }) {
  const brand = useBranding();

  const name = brand?.brand_name ?? "Workeasy360";
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
      <div style={{ width: "100%", maxWidth: 380, textAlign: "center" }}>
        {brand?.logo_url?.startsWith("/api/v1/") && (
          // eslint-disable-next-line @next/next/no-img-element -- same-origin, scanned PNG/JPEG
          <img src={brand.logo_url} alt="" className={brandStyles.logo} />
        )}
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>{name}</h1>
        <p style={{ color: "var(--w360-text-muted)", marginTop: "var(--w360-space-2)" }}>
          Sign in with your Workeasy360 account to reach your products.
        </p>
        <div className="w360-card" style={{ padding: "var(--w360-space-6)", marginTop: "var(--w360-space-5)" }}>
          <a className="w360-btn w360-btn-primary" href={handoffUrl} style={{ display: "inline-block" }}>
            Continue to sign in
          </a>
          <p style={{ color: "var(--w360-text-muted)", fontSize: 13, marginTop: "var(--w360-space-4)", marginBottom: 0 }}>
            Sign-in happens on the secure Workeasy360 page, where {name} is shown too.
          </p>
        </div>
        {(brand?.support_email || brand?.support_url) && (
          <p style={{ fontSize: 13, marginTop: "var(--w360-space-4)" }}>
            Need help?{" "}
            {brand?.support_url?.startsWith("https://") ? (
              <a href={brand.support_url} rel="noopener noreferrer">
                Support
              </a>
            ) : (
              brand?.support_email && <a href={`mailto:${brand.support_email}`}>{brand.support_email}</a>
            )}
          </p>
        )}
      </div>
    </main>
  );
}
