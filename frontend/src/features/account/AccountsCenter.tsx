"use client";

import { useState } from "react";
import { switchAccount } from "@/lib/api/auth";
import { accountCenterApi, type CenterOrganization } from "@/lib/api/console";
import { LoadGate, useLoad } from "@/features/controlplane/ui";
import { Badge, Button, formatDate, formatDateTime } from "@/features/controlplane/kit";
import { Panel, roleLabel } from "@/features/console/consoleUi";
import { AccountSecurityPanels, describeAccountEvent } from "@/features/console/MySecurityScreen";
import { SignInPreferencesScreen } from "@/features/signin/SignInPreferencesScreen";
import { CenterShell } from "./CenterShell";
import { assignmentLabel, holdingLabel } from "./centerCopy";
import { OpenProductButton } from "./CenterProducts";
import { ProfileForm } from "./ProfileForm";
import styles from "./Account.module.css";

/** Overview: who you are, your organizations, what you can open, and what happened lately. */
export function CenterOverview({ appOrigin }: { appOrigin: string }) {
  const [profile] = useLoad(() => accountCenterApi.profile(), []);
  const [products] = useLoad(() => accountCenterApi.products(), []);
  const [recent] = useLoad(() => accountCenterApi.history(undefined, "all"), []);
  return (
    <CenterShell current="overview" title="Your WorkEasy360 account" lead="One account for User Admin, the Store and every WorkEasy360 product.">
      <div className={styles.grid}>
        <Panel title="Profile" actions={<a href="/account/settings">Edit</a>}>
          <LoadGate load={profile} what="your profile">
            {(p) => (
              <dl className={styles.facts}>
                <dt>Name</dt>
                <dd>{p.name || "Not set"}</dd>
                <dt>Sign-in email</dt>
                <dd>
                  {p.email} {p.email_verified ? <Badge tone="success">Verified</Badge> : <Badge tone="warning">Not verified</Badge>}
                </dd>
                <dt>WorkEasy360 ID</dt>
                <dd className={styles.mono}>{p.user_id}</dd>
                <dt>Member since</dt>
                <dd>{p.created_at ? formatDate(p.created_at) : "—"}</dd>
              </dl>
            )}
          </LoadGate>
        </Panel>
        <Panel title="Your organizations" actions={<a href="/account/organizations">All organizations</a>}>
          <LoadGate load={products} what="your organizations">
            {(d) =>
              d.organizations.length === 0 ? (
                <p>
                  You are not a member of any organization yet. <a href={`${appOrigin}/onboarding`}>Set one up in User Admin</a>.
                </p>
              ) : (
                <ul className={styles.orgs}>
                  {d.organizations.map((o) => (
                    <li key={o.organization_id}>
                      <a href={`${appOrigin}/o/${encodeURIComponent(o.slug)}`}>{o.name}</a>
                      {o.is_owner && <span className={styles.tag}>Owner</span>}
                    </li>
                  ))}
                </ul>
              )
            }
          </LoadGate>
        </Panel>
      </div>

      <Panel title="Products you can open" actions={<a href="/account/products">My Products</a>}>
        <LoadGate load={products} what="your products">
          {(d) => {
            const open = d.organizations.flatMap((o) => o.apps.filter((a) => a.can_launch).map((a) => ({ org: o, app: a })));
            if (open.length === 0) {
              return <p>No product is open to you yet. Products your organizations hold are listed on My Products.</p>;
            }
            return (
              <ul className={styles.productList}>
                {open.map(({ org, app }) => (
                  <li key={`${org.organization_id}-${app.product_code}`}>
                    <span>
                      <strong>{app.display_name}</strong>
                      <span className={styles.muted}> in {org.name}</span>
                    </span>
                    <OpenProductButton organization={org} productCode={app.product_code} productName={app.display_name} />
                  </li>
                ))}
              </ul>
            );
          }}
        </LoadGate>
      </Panel>

      <Panel title="Recent activity" actions={<a href="/account/history">Login History</a>}>
        <LoadGate load={recent} what="your recent activity">
          {(d) =>
            d.results.length === 0 ? (
              <p>No recent activity.</p>
            ) : (
              <ul className={styles.activity}>
                {d.results.slice(0, 6).map((e, i) => (
                  <li key={`${e.occurred_at}-${i}`}>
                    <span>
                      {describeAccountEvent(e)}
                      {e.product_name ? `: ${e.product_name}` : ""}
                    </span>
                    <span className={styles.muted}>{formatDateTime(e.occurred_at)}</span>
                  </li>
                ))}
              </ul>
            )
          }
        </LoadGate>
      </Panel>
    </CenterShell>
  );
}

function OrganizationCard({ org, appOrigin }: { org: CenterOrganization; appOrigin: string }) {
  const held = org.apps.filter((a) => a.entitlement_status || a.state === "active");
  const roles = org.roles ?? [];
  return (
    <Panel
      title={org.name}
      description={
        <>
          {org.is_owner ? "Owner" : roles.length > 0 ? roles.map(roleLabel).join(", ") : "Member"}
          {org.status !== "active" && ` · ${org.status.replace(/_/g, " ")}`}
        </>
      }
      actions={<a href={`${appOrigin}/o/${encodeURIComponent(org.slug)}`}>Manage in User Admin</a>}
    >
      {org.restricted ? (
        <p role="note">
          Your organization does not allow this session right now ({org.restricted.replace(/_/g, " ")}). Open it in User Admin
          from an allowed network, or after signing in again.
        </p>
      ) : held.length === 0 ? (
        <p>This organization holds no products yet.</p>
      ) : (
        <ul className={styles.productList}>
          {held.map((a) => {
            const holding = holdingLabel(a.entitlement_status);
            const assigned = assignmentLabel(a);
            return (
              <li key={a.product_code}>
                <strong>{a.display_name}</strong>
                <span className={styles.chips}>
                  {holding && <Badge tone={holding.tone}>{holding.label}</Badge>}
                  <Badge tone={assigned.tone}>{assigned.label}</Badge>
                </span>
              </li>
            );
          })}
        </ul>
      )}
      {org.may_assign_seats && !org.restricted && (
        <p>
          <a href={`${appOrigin}/o/${encodeURIComponent(org.slug)}/settings/products`}>Members and product access</a>
        </p>
      )}
    </Panel>
  );
}

/** Organizations: each membership, the role in it, and the product access it gives you. */
export function CenterOrganizations({ appOrigin }: { appOrigin: string }) {
  const [products] = useLoad(() => accountCenterApi.products(), []);
  return (
    <CenterShell current="organizations" title="Organizations" lead="Where you are a member, your role, and the products each gives you. Members, seats and billing are managed in User Admin.">
      <LoadGate load={products} what="your organizations">
        {(d) =>
          d.organizations.length === 0 ? (
            <p>
              You are not a member of any organization yet. <a href={`${appOrigin}/onboarding`}>Set one up in User Admin</a>.
            </p>
          ) : (
            <>
              {d.organizations.map((o) => (
                <OrganizationCard key={o.organization_id} org={o} appOrigin={appOrigin} />
              ))}
            </>
          )
        }
      </LoadGate>
    </CenterShell>
  );
}

/** Security: password, recovery, sessions, sign out everywhere and recent security activity. */
export function CenterSecurity() {
  return (
    <CenterShell current="security" title="Security" lead="Your sign-in email and password, where you are signed in, and what has happened to your account.">
      <AccountSecurityPanels preferencesHref="/account/sign-in-preferences" sessionsHref="/account/sessions" />
      <Panel title="Account recovery">
        <p>
          Forgotten your password? <a href="/forgot-password">Reset it by email</a>: a link goes to your sign-in email, and
          resetting it signs you out everywhere.
        </p>
      </Panel>
    </CenterShell>
  );
}

/** Account Settings: your name, your sign-in email, how products sign you in, switching account. */
export function CenterSettings() {
  const [profile, reload] = useLoad(() => accountCenterApi.profile(), []);
  return (
    <CenterShell current="settings" title="Account Settings" lead="Your profile and how WorkEasy360 signs you in to products.">
      <Panel title="Profile" description="Your name as WorkEasy360 and its products show it. Products receive it at your next sign-in.">
        <LoadGate load={profile} what="your profile">
          {(p) => <ProfileForm profile={p} onSaved={reload} />}
        </LoadGate>
      </Panel>
      <Panel title="Sign-in email">
        <p>
          <LoadGate load={profile} what="your sign-in email">
            {(p) => <strong>{p.email}</strong>}
          </LoadGate>{" "}
          - change it on <a href="/account/security">Security</a>; a confirmation link goes to the new address.
        </p>
      </Panel>
      <Panel title="Product sign-in preferences">
        <p>
          Choose whether each product opens with this account automatically or asks first.{" "}
          <a href="/account/sign-in-preferences">Change how products sign you in</a>
        </p>
      </Panel>
      <Panel title="Switch account">
        <p>Signs this account out of WorkEasy360 Accounts and its products, then asks which account to sign in with.</p>
        <SwitchAccountButton />
      </Panel>
    </CenterShell>
  );
}

function SwitchAccountButton() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div>
      <Button
        icon="users"
        loading={busy}
        onClick={() => {
          setBusy(true);
          setError(null);
          switchAccount()
            .then(({ redirect_url }) => window.location.assign(redirect_url))
            .catch(() => {
              setBusy(false);
              setError("Could not switch accounts. Try again.");
            });
        }}
      >
        Use a different account
      </Button>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}

/** Which account each product opens with (ADR-044/066), inside the Control Center. */
export function CenterSignInPreferences() {
  return (
    <CenterShell current="settings">
      <SignInPreferencesScreen />
    </CenterShell>
  );
}
