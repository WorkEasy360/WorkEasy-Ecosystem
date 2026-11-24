"use client";

import { useState } from "react";
import { getOrganizations, logout } from "@/lib/api/auth";
import { useSession } from "@/lib/auth/useSession";
import { LoadGate, useLoad } from "@/features/controlplane/ui";
import { Panel } from "@/features/console/consoleUi";
import { AccountSecurityPanels } from "@/features/console/MySecurityScreen";
import { AccountsBrand } from "@/features/signin/AccountsBrand";
import { SignInPreferencesScreen } from "@/features/signin/SignInPreferencesScreen";
import styles from "./Account.module.css";

type Section = "account" | "preferences";

/**
 * WorkEasy360 Accounts' own pages (ADR-071): the person's account, in every
 * organization and every product - one header with the Accounts mark, the two
 * sections, and "Sign out". Organization administration stays on the customer
 * portal (User Admin); this is only the person.
 */
export function AccountShell({ current, children }: { current: Section; children: React.ReactNode }) {
  const [signingOut, setSigningOut] = useState(false);
  return (
    <div className={styles.shell}>
      <header className={styles.bar}>
        <AccountsBrand />
        <nav aria-label="Your account" className={styles.nav}>
          <a href="/account" aria-current={current === "account" ? "page" : undefined}>
            Account &amp; security
          </a>
          <a href="/account/sign-in-preferences" aria-current={current === "preferences" ? "page" : undefined}>
            Product sign-in
          </a>
        </nav>
        <button
          type="button"
          className="w360-btn w360-btn-secondary"
          disabled={signingOut}
          onClick={() => {
            setSigningOut(true);
            // Ends this WorkEasy360 Accounts sign-in - and with it the customer
            // portal session it handed over and its products' sessions (ADR-069).
            void logout()
              .catch(() => undefined)
              .finally(() => window.location.replace("/login"));
          }}
        >
          Sign out
        </button>
      </header>
      <main id="w360-main" className={styles.main}>
        {children}
      </main>
    </div>
  );
}

/** The account page: who is signed in, their organizations, and their security. */
export function AccountHome({ appOrigin }: { appOrigin: string }) {
  const session = useSession();
  const [organizations] = useLoad(() => getOrganizations({ limit: 50 }), []);
  const who = session.status === "ready" ? session.session.name || session.session.email || "" : "";
  const email = session.status === "ready" ? session.session.email ?? "" : "";
  return (
    <AccountShell current="account">
      <div className={styles.intro}>
        <h1>Your WorkEasy360 account</h1>
        <p>
          {who ? (
            <>
              Signed in as <strong>{who}</strong>
              {email && email !== who ? ` (${email})` : ""}.{" "}
            </>
          ) : null}
          One account for User Admin, the Store and every WorkEasy360 product.
        </p>
      </div>

      <Panel title="Your organizations" description="Open an organization's administration in User Admin.">
        <LoadGate load={organizations} what="your organizations">
          {(page) =>
            page.organizations.length === 0 ? (
              <p>
                You are not a member of any organization yet. <a href={`${appOrigin}/onboarding`}>Set one up in User Admin</a>.
              </p>
            ) : (
              <ul className={styles.orgs}>
                {page.organizations.map((org) => (
                  <li key={org.organization_id}>
                    <a href={`${appOrigin}/o/${encodeURIComponent(org.slug)}`}>{org.display_name}</a>
                    {org.is_owner && <span className={styles.tag}>Owner</span>}
                  </li>
                ))}
              </ul>
            )
          }
        </LoadGate>
      </Panel>

      <AccountSecurityPanels preferencesHref="/account/sign-in-preferences" />
    </AccountShell>
  );
}

/** Which account a product opens with (ADR-044/066), on Accounts itself. */
export function AccountSignInPreferences() {
  return (
    <AccountShell current="preferences">
      <SignInPreferencesScreen />
    </AccountShell>
  );
}
