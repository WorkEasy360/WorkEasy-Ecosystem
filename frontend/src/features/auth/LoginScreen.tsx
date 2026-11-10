"use client";

import { Suspense, useEffect } from "react";
import Link from "next/link";
import { Logo } from "@/components/brand/Logo";
import { useSearchParams } from "next/navigation";
import { StatusScreen } from "@/components/StatusScreen";
import { useSession } from "@/lib/auth/useSession";
import { usePortalRouter } from "@/lib/portalRouter";
import { PORTAL_TO_MODE, type Portal } from "@/lib/portals";
import { AdminLoginScreen } from "@/features/auth/AdminLogin";
import { LoginForm } from "@/features/auth/LoginForm";
import { destinationAfterSignIn, parseMode, type SignInMode } from "@/features/auth/signInMode";
import { SignInSupport } from "@/features/controlplane/brandDisplay";
import { useBranding, type HostBrand } from "@/features/controlplane/useBranding";
import brandStyles from "@/features/controlplane/ControlPlane.module.css";
import styles from "./LoginScreen.module.css";

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

/** A product asked for the password again (`prompt=login`, ADR-044). */
export function reauthenticating(params: URLSearchParams): boolean {
  return Boolean(params.get("resume")) && params.get("reauth") === "1";
}

/**
 * Which mode the page is in, as the address names it. A product sign-in
 * (`?resume=`) and a partner's branded sign-in (`?brand=`) are always the User
 * mode: the visitor is on their way to a product or a partner's customer
 * workspace, never the platform's console. Null when nothing names a mode -
 * the page then signs in as a User (there is no chooser: each portal's own
 * address names its mode).
 */
export function effectiveMode(params: URLSearchParams): SignInMode | null {
  if (params.get("resume") || brandHostParam(params.get("brand"))) return "user";
  return parseMode(params.get("mode"));
}

/** The portal this sign-in page is served on (multi-portal mode), or
 * `undefined` in single-host mode. */
type HostPortal = Portal | undefined;

/**
 * The mode the page signs in for. ADR-059: on the Admin, Partner and customer
 * portals the host itself names it - each is its own sign-in page, and the
 * session it creates is that host's. Elsewhere, what the address names, else
 * User.
 */
export function modeFor(portal: HostPortal, params: URLSearchParams): SignInMode {
  if (params.get("resume") || brandHostParam(params.get("brand"))) return "user";
  if (portal && portal !== "auth") return PORTAL_TO_MODE[portal];
  return effectiveMode(params) ?? "user";
}

function AlreadySignedInRedirect({ portal }: { portal: HostPortal }) {
  const router = usePortalRouter();
  const searchParams = useSearchParams();
  const session = useSession();

  useEffect(() => {
    if (session.status !== "ready" || !session.session.authenticated) return;
    // Already signed in. A resume link still needs completing (a second tab
    // may have logged in first); otherwise go where this sign-in page leads.
    const resume = searchParams.get("resume");
    // ADR-044: a product asked for a fresh sign-in (`prompt=login`). The
    // existing session is exactly what it does not want: the form stays, and
    // the server refuses to resume for a session older than the request.
    if (reauthenticating(searchParams)) return;
    if (resume) {
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.href = `/api/v1/integrations/oauth/resume?resume=${encodeURIComponent(resume)}`;
    } else if (portal !== "auth") {
      // Single-host mode: what the address names, as before (no mode lets the
      // dashboard decide).
      const mode = portal ? modeFor(portal, searchParams) : effectiveMode(searchParams);
      router.replace(destinationAfterSignIn(mode, searchParams.get("next")));
    }
  }, [session, router, searchParams, portal]);

  return null;
}

/**
 * Looks up the partner's white-label brand once - the partner domain this
 * visitor was handed over from, or, on a verified partner host, that host
 * itself - and gives it to everything on the page that draws it (the heading
 * above the card, the support details below it).
 */
function BrandScope({ children }: { children: (brand: HostBrand | null) => React.ReactNode }) {
  const searchParams = useSearchParams();
  const brand = useBranding(brandHostParam(searchParams.get("brand")));
  return <>{children(brand)}</>;
}

function Heading({ brand }: { brand: HostBrand | null }) {
  const searchParams = useSearchParams();
  // The partner's own welcome message replaces the platform's tagline when it
  // chose to show one on the sign-in page; otherwise the tagline stays.
  const welcome = brand?.portal.login_show_welcome && brand.portal.welcome_message.trim();
  // The WorkEasy360 logo on the platform's own sign-in only. A partner-branded
  // sign-in never shows it - not even while that partner's brand is loading.
  const platform = !brand && !brandHostParam(searchParams.get("brand"));
  return (
    <div className={styles.heading}>
      {platform && (
        <div className={styles.logoRow}>
          <Logo size="lg" />
        </div>
      )}
      {brand?.logo_url?.startsWith("/api/v1/") && (
        // eslint-disable-next-line @next/next/no-img-element -- same-origin, scanned PNG/JPEG
        <img src={brand.logo_url} alt="" className={brandStyles.logo} />
      )}
      <h1 className={styles.title}>{brand?.brand_name ?? "Workeasy360"}</h1>
      {welcome ? <p className={styles.tagline}>{welcome}</p> : <p className={styles.tagline}>One ecosystem to run your entire business.</p>}
    </div>
  );
}

const SIGN_IN_TITLE: Record<SignInMode, string> = {
  admin: "Admin Login",
  partner: "Sign in as Partner",
  user: "Sign in as User",
};

function SignInCard({ portal }: { portal: HostPortal }) {
  const searchParams = useSearchParams();
  const mode = modeFor(portal, searchParams);
  // The platform's own sign-in names its portal; a product's or a partner's says "Sign in".
  const platform = !searchParams.get("resume") && !brandHostParam(searchParams.get("brand"));
  return (
    <>
      <div className={styles.formHeader}>
        <h2 className={styles.cardTitle}>
          {reauthenticating(searchParams) ? "Confirm it's you" : platform || mode === "admin" ? SIGN_IN_TITLE[mode] : "Sign in"}
        </h2>
      </div>
      {reauthenticating(searchParams) && (
        <p className={styles.note}>
          The product you are opening asked Workeasy360 to check it is really you. Enter your Workeasy360 email and password
          again - or sign in as someone else.
        </p>
      )}
      <LoginForm mode={mode} />
      {mode === "partner" && (
        <>
          <p className="w360-hint">
            <Link href="/partner/apply">Apply to become a partner</Link>
          </p>
          {/* 2026-11-10: a read-only preview with example data. A guest is never a
              partner: no account, no session, no partner rights. */}
          <p className="w360-hint">
            <Link href="/partner/preview" className="w360-btn w360-btn-secondary">
              Continue as Guest
            </Link>{" "}
            A read-only preview with example data. Not a partner account.
          </p>
        </>
      )}
    </>
  );
}

/** The Admin portal's sign-in has its own screen (AdminLogin.tsx); a product's
 * or a partner's sign-in on that host (`?resume=`, `?brand=`) never does. */
function ScreenForMode({ portal }: { portal: HostPortal }) {
  const searchParams = useSearchParams();
  if (modeFor(portal, searchParams) === "admin") {
    return (
      <>
        <AlreadySignedInRedirect portal={portal} />
        <AdminLoginScreen />
      </>
    );
  }
  return <StandardSignIn portal={portal} />;
}

export function LoginScreen({ portal }: { portal?: Portal } = {}) {
  return (
    <Suspense fallback={<StatusScreen>Loading…</StatusScreen>}>
      <ScreenForMode portal={portal} />
    </Suspense>
  );
}

function StandardSignIn({ portal }: { portal?: Portal }) {
  return (
    <main id="w360-main" className={`w360-container ${styles.main}`}>
      <Suspense fallback={null}>
        <AlreadySignedInRedirect portal={portal} />
      </Suspense>
      <div className={styles.column}>
        <Suspense
          fallback={
            <>
              <div className={styles.heading}>
                <h1 className={styles.title}>Workeasy360</h1>
              </div>
              <div className={`w360-card ${styles.card}`}>
                <p>Loading…</p>
              </div>
            </>
          }
        >
          <BrandScope>
            {(brand) => (
              <>
                <Heading brand={brand} />
                <div className={`w360-card ${styles.card}`}>
                  <Suspense fallback={<p>Loading…</p>}>
                    <SignInCard portal={portal} />
                  </Suspense>
                </div>
                {/* Support details when the partner chose to show them, and its website. */}
                <SignInSupport brand={brand} centered />
              </>
            )}
          </BrandScope>
        </Suspense>
      </div>
    </main>
  );
}
