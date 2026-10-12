"use client";

import { Suspense, useEffect, useId, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useSession } from "@/lib/auth/useSession";
import { usePortals } from "@/lib/portalContext";
import { LoginForm } from "@/features/auth/LoginForm";
import { PortalHandoff } from "@/features/auth/PortalHandoff";
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

/**
 * Which mode the page is in. A product sign-in (`?resume=`) and a partner's
 * branded sign-in (`?brand=`) are always the User mode: the visitor is on
 * their way to a product or a partner's customer workspace, never the
 * platform's console, so the choice is not offered there.
 */
/** A product asked for the password again (`prompt=login`, ADR-044). */
export function reauthenticating(params: URLSearchParams): boolean {
  return Boolean(params.get("resume")) && params.get("reauth") === "1";
}

export function effectiveMode(params: URLSearchParams): SignInMode | null {
  if (params.get("resume") || brandHostParam(params.get("brand"))) return "user";
  return parseMode(params.get("mode"));
}

function AlreadySignedInRedirect() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const session = useSession();

  const portals = usePortals();

  useEffect(() => {
    if (session.status !== "ready" || !session.session.authenticated) return;
    // Already signed in. A resume link still needs completing (a second tab
    // may have logged in first); otherwise go where the chosen mode leads -
    // on the central sign-in host (ADR-043) by handing the sign-in to that
    // portal, which the sign-in card does itself.
    const resume = searchParams.get("resume");
    // ADR-044: a product asked for a fresh sign-in (`prompt=login`). The
    // existing session is exactly what it does not want: the form stays, and
    // the server refuses to resume for a session older than the request.
    if (reauthenticating(searchParams)) return;
    if (portals && !resume) return;
    if (resume) {
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.href = `/api/v1/integrations/oauth/resume?resume=${encodeURIComponent(resume)}`;
    } else {
      router.replace(destinationAfterSignIn(effectiveMode(searchParams), searchParams.get("next")));
    }
  }, [session, router, searchParams, portals]);

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
  const choosing = effectiveMode(searchParams) === null;
  // The partner's own welcome message replaces the platform's tagline when it
  // chose to show one on the sign-in page; otherwise the tagline stays.
  const welcome = !choosing && brand?.portal.login_show_welcome && brand.portal.welcome_message.trim();
  return (
    <div className={styles.heading}>
      {brand?.logo_url?.startsWith("/api/v1/") && (
        // eslint-disable-next-line @next/next/no-img-element -- same-origin, scanned PNG/JPEG
        <img src={brand.logo_url} alt="" className={brandStyles.logo} />
      )}
      <h1 className={styles.title}>{choosing ? "Welcome to Workeasy360" : (brand?.brand_name ?? "Workeasy360")}</h1>
      {welcome ? <p className={styles.tagline}>{welcome}</p> : <p className={styles.tagline}>One ecosystem to run your entire business.</p>}
    </div>
  );
}

const MODES: { mode: SignInMode; label: string; description: string }[] = [
  { mode: "admin", label: "Administrator", description: "Manage the Workeasy360 platform" },
  { mode: "partner", label: "Partner", description: "Manage your customers, sales and earnings" },
  { mode: "user", label: "User", description: "Access your organization and products" },
];

/** The two ways in. Each is an ordinary link (keyboard, middle-click and
 * back-button behave as everywhere else); choosing one only changes the
 * address to `?mode=...`. */
function ModeChooser() {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId} className={styles.cardTitle}>
        How would you like to sign in?
      </h2>
      <ul className={styles.modes}>
        {MODES.map((m) => (
          <ModeOption key={m.mode} {...m} />
        ))}
      </ul>
      <p className="w360-hint">
        <Link href="/forgot-password">Forgotten your password?</Link> · <Link href="/signup">Create an account</Link>
      </p>
    </section>
  );
}

function ModeOption({ mode, label, description }: { mode: SignInMode; label: string; description: string }) {
  const descriptionId = useId();
  return (
    <li>
      <Link href={`/login?mode=${mode}`} className={styles.mode} aria-label={label} aria-describedby={descriptionId}>
        <span className={styles.modeIcon} aria-hidden="true">
          {mode === "admin" ? <ShieldIcon /> : mode === "partner" ? <PartnerIcon /> : <PersonIcon />}
        </span>
        <span className={styles.modeText}>
          <span className={styles.modeLabel}>{label}</span>
          <span id={descriptionId} className={styles.modeDescription}>
            {description}
          </span>
        </span>
        <span className={styles.modeArrow} aria-hidden="true">
          ›
        </span>
      </Link>
    </li>
  );
}

function ShieldIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" focusable="false">
      <path d="M12 3 5 6v5c0 4.4 3 8.3 7 9.5 4-1.2 7-5.1 7-9.5V6l-7-3Zm-3 9 2 2 4-4" />
    </svg>
  );
}

function PartnerIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" focusable="false">
      <path d="M16 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM2 20v-1a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v1M14 15h4a4 4 0 0 1 4 4v1" />
    </svg>
  );
}

function PersonIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" focusable="false">
      <path d="M20 21v-1a5 5 0 0 0-5-5H9a5 5 0 0 0-5 5v1M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z" />
    </svg>
  );
}

const SIGN_IN_TITLE: Record<SignInMode, string> = {
  admin: "Sign in as Administrator",
  partner: "Sign in as Partner",
  user: "Sign in as User",
};

function SignInCard() {
  const searchParams = useSearchParams();
  const portals = usePortals();
  const session = useSession();
  const [signedInHere, setSignedInHere] = useState(false);
  const mode = effectiveMode(searchParams);
  if (mode === null) return <ModeChooser />;
  // The central sign-in (ADR-043): once signed in here, hand the sign-in to
  // the chosen portal. A product sign-in (`resume`) completes here instead.
  const signedIn = signedInHere || (session.status === "ready" && session.session.authenticated);
  if (portals && signedIn && !searchParams.get("resume")) {
    return <PortalHandoff origins={portals} mode={mode} next={searchParams.get("next")} state={searchParams.get("state")} />;
  }
  if (portals && session.status === "loading") return <p>Loading…</p>;
  // The mode chooser is only offered where both destinations make sense.
  const changeable = !searchParams.get("resume") && !brandHostParam(searchParams.get("brand"));
  return (
    <>
      <div className={styles.formHeader}>
        <h2 className={styles.cardTitle}>
          {reauthenticating(searchParams) ? "Confirm it's you" : changeable || mode === "admin" ? SIGN_IN_TITLE[mode] : "Sign in"}
        </h2>
        {changeable && (
          <Link href="/login" className={styles.change}>
            Change<span className="w360-visually-hidden"> how you sign in</span>
          </Link>
        )}
      </div>
      {mode === "admin" && (
        <p className={styles.note}>
          After your password, Workeasy360 staff verify a code from their authenticator. Access to the Admin Console is checked
          for your account after you sign in.
        </p>
      )}
      {reauthenticating(searchParams) && (
        <p className={styles.note}>
          The product you are opening asked Workeasy360 to check it is really you. Enter your Workeasy360 email and password
          again - or sign in as someone else.
        </p>
      )}
      <LoginForm mode={mode} onSignedIn={portals ? () => setSignedInHere(true) : undefined} />
    </>
  );
}

export function LoginScreen() {
  return (
    <main id="w360-main" className={`w360-container ${styles.main}`}>
      <Suspense fallback={null}>
        <AlreadySignedInRedirect />
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
                    <SignInCard />
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
