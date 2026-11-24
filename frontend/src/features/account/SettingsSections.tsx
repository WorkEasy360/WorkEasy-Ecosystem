"use client";

import { useId, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { accountApi, accountCenterApi, type ClosureCheck, type NotificationPreference } from "@/lib/api/console";
import { LoadGate, describeError, useLoad } from "@/features/controlplane/ui";
import { Alert, Badge, Button } from "@/features/controlplane/kit";
import { Panel, roleLabel } from "@/features/console/consoleUi";
import { ActivityTable } from "@/features/console/MySecurityScreen";
import { normalizeCode } from "@/features/auth/TwoStepSignIn";
import { ACCOUNT_HELP_HREF } from "./centerConfig";
import styles from "./Account.module.css";

/**
 * Settings (Accounts V3, the note's 10.1-10.3): personal notification preferences,
 * recent security activity, links to the User Admin views this person may open, and
 * account closure. Organizations, subscriptions and billing stay User Admin's - this
 * only links there.
 */

// --- notifications ----------------------------------------------------------------------

/** Each choice saves as it is switched. Security notices are always sent: shown on and locked. */
export function NotificationSettings() {
  const [load] = useLoad(() => accountCenterApi.notifications(), []);
  const [current, setCurrent] = useState<NotificationPreference[] | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<{ ok: boolean; text: string } | null>(null);
  const idBase = useId();

  function toggle(pref: NotificationPreference, enabled: boolean) {
    setSaving(pref.key);
    setOutcome(null);
    accountCenterApi
      .updateNotifications({ [pref.key]: enabled })
      .then((r) => {
        setCurrent(r.preferences);
        setOutcome({ ok: true, text: `Saved. ${pref.label}: ${enabled ? "on" : "off"}.` });
      })
      .catch((err: unknown) => setOutcome({ ok: false, text: `Not saved. ${describeError(err)}` }))
      .finally(() => setSaving(null));
  }

  return (
    <Panel title="Notifications" description="Which emails WorkEasy360 sends you. Security and recovery notices are always sent.">
      <LoadGate load={load} what="your notification settings">
        {(d) => {
          const prefs = current ?? d.preferences;
          return (
            <>
              <ul className={styles.prefs}>
                {prefs.map((p, i) => {
                  const labelId = `${idBase}-${i}`;
                  return (
                    <li key={p.key}>
                      <span className={styles.prefText}>
                        <strong id={labelId}>{p.label}</strong>
                        <span id={`${labelId}-text`} className={styles.muted}>
                          {p.description}
                        </span>
                      </span>
                      <label className={styles.switch}>
                        <input
                          type="checkbox"
                          role="switch"
                          checked={p.required ? true : p.enabled}
                          disabled={p.required || saving !== null}
                          aria-labelledby={labelId}
                          aria-describedby={`${labelId}-text`}
                          onChange={(e) => toggle(p, e.target.checked)}
                        />
                        <span>{p.required ? "Always sent" : p.enabled ? "On" : "Off"}</span>
                      </label>
                    </li>
                  );
                })}
              </ul>
              {outcome && (
                <p role={outcome.ok ? "status" : "alert"} className={outcome.ok ? undefined : "w360-error-text"}>
                  {outcome.text}
                </p>
              )}
            </>
          );
        }}
      </LoadGate>
    </Panel>
  );
}

// --- recent security activity --------------------------------------------------------------

/** Only this person's own trail, with redacted values - never an organization's audit log. */
export function RecentSecurityActivity() {
  const [load] = useLoad(() => accountApi.activity(), []);
  return (
    <Panel
      title="Recent security activity"
      description="Sign-ins and changes to your account. A network is shown as a short prefix, never your full address."
    >
      <LoadGate load={load} what="your recent activity">
        {(d) => <ActivityTable events={d.events} />}
      </LoadGate>
    </Panel>
  );
}

// --- your organizations ---------------------------------------------------------------------

const orgHref = (appOrigin: string, slug: string, path = "") => `${appOrigin}/o/${encodeURIComponent(slug)}${path}`;

/** Links to the User Admin views this person may open: an organization, and its
 * subscriptions and billing only where they may see billing. */
export function YourOrganizations({ appOrigin }: { appOrigin: string }) {
  const [load] = useLoad(() => accountCenterApi.organizations(), []);
  return (
    <Panel title="Your organizations" description="Members, product access, subscriptions and billing are managed in User Admin.">
      <LoadGate load={load} what="your organizations">
        {(d) =>
          d.organizations.length === 0 ? (
            <p>
              You are not a member of any organization. <a href={`${appOrigin}/onboarding`}>Set one up in User Admin</a>.
            </p>
          ) : (
            <ul className={styles.productList}>
              {d.organizations.map((o) => (
                <li key={o.organization_id}>
                  <span className={styles.productName}>
                    <strong>{o.name}</strong>
                    <span className={styles.chips}>
                      <Badge tone={o.is_owner ? "brand" : "neutral"}>
                        {o.is_owner ? "Owner" : o.roles.length > 0 ? o.roles.map(roleLabel).join(", ") : "Member"}
                      </Badge>
                      {o.status !== "active" && <Badge tone="warning">{o.status.replace(/_/g, " ")}</Badge>}
                    </span>
                  </span>
                  <span className={styles.links}>
                    <a href={orgHref(appOrigin, o.slug)} aria-label={`Open ${o.name} in User Admin`}>
                      Open in User Admin
                    </a>
                    {o.can_view_billing && (
                      <a href={orgHref(appOrigin, o.slug, "/settings/billing")} aria-label={`Subscriptions and billing of ${o.name}`}>
                        Subscriptions and billing
                      </a>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )
        }
      </LoadGate>
    </Panel>
  );
}

// --- closing the account --------------------------------------------------------------------

/** After closing: the server has ended the session; the sign-in page says so. */
export const closedNavigation = { assign: (url: string) => window.location.assign(url) };

/** What stops the account closing: organizations it is the only owner of, partners it is the last administrator of. */
interface ClosureBlockers {
  organizations: ClosureCheck["blocking_organizations"];
  partners: NonNullable<ClosureCheck["blocking_partners"]>;
}

function BlockingOrganizations({ blockers, appOrigin }: { blockers: ClosureBlockers; appOrigin: string }) {
  const owned = blockers.organizations.filter((o) => !o.pending);
  const invited = blockers.organizations.filter((o) => o.pending);
  return (
    <>
      {owned.length > 0 && (
        <Alert tone="warning" title="You are the only owner of an organization">
          <p>Before you can close your account, make someone else an owner of each of these organizations, or close the organization, in User Admin:</p>
          <ul className={styles.list}>
            {owned.map((o) => (
              <li key={o.slug}>
                <a href={orgHref(appOrigin, o.slug, "/settings/members")}>{o.name}</a>
              </li>
            ))}
          </ul>
        </Alert>
      )}
      {invited.length > 0 && (
        <Alert tone="warning" title="You were invited to own an organization">
          <p>
            You are the owner named for each of these organizations and have not accepted yet. Accept the invitation from your email and make someone else the owner, or ask the
            partner that set it up to name another owner:
          </p>
          <ul className={styles.list}>
            {invited.map((o) => (
              <li key={o.slug}>{o.name}</li>
            ))}
          </ul>
        </Alert>
      )}
      {blockers.partners.length > 0 && (
        <Alert tone="warning" title="You are the last administrator of a partner">
          <p>Before you can close your account, add another administrator to each of these partners in the Partner portal:</p>
          <ul className={styles.list}>
            {blockers.partners.map((p) => (
              <li key={p.name}>{p.name}</li>
            ))}
          </ul>
        </Alert>
      )}
    </>
  );
}

export function CloseAccount({ appOrigin }: { appOrigin: string }) {
  const [load] = useLoad(() => accountCenterApi.closure(), []);
  const [open, setOpen] = useState(false);
  const [blocked, setBlocked] = useState<ClosureBlockers | null>(null);
  return (
    <Panel title="Close account" description="Permanently close this WorkEasy360 account.">
      <div className={`${styles.section} ${styles.danger}`}>
        <p>Closing your account:</p>
        <ul className={styles.list}>
          <li>stops this account signing in to WorkEasy360 Accounts, User Admin, the Store and every WorkEasy360 product;</li>
          <li>signs you out everywhere, and tells the products you are signed in to;</li>
          <li>ends your memberships. Each organization keeps its own records - invoices, orders and what you created for it;</li>
          <li>does not cancel an organization&apos;s subscriptions or close an organization. Those are separate steps in User Admin.</li>
        </ul>
        <LoadGate load={load} what="whether this account can be closed">
          {(check) => {
            const blocking = blocked ?? (check.can_close ? { organizations: [], partners: [] } : { organizations: check.blocking_organizations, partners: check.blocking_partners ?? [] });
            if (blocking.organizations.length > 0 || blocking.partners.length > 0) return <BlockingOrganizations blockers={blocking} appOrigin={appOrigin} />;
            if (!check.can_close) return <p role="note">This account cannot be closed right now. Ask Help &amp; Support.</p>;
            return open ? (
              <ClosureForm twoStep={check.two_step_required} onCancel={() => setOpen(false)} onBlocked={setBlocked} />
            ) : (
              <div className={styles.actions}>
                <Button variant="danger" onClick={() => setOpen(true)}>
                  Close my account
                </Button>
              </div>
            );
          }}
        </LoadGate>
      </div>
    </Panel>
  );
}

function ClosureForm({
  twoStep,
  onCancel,
  onBlocked,
}: {
  twoStep: boolean;
  onCancel: () => void;
  onBlocked: (blockers: ClosureBlockers) => void;
}) {
  const id = useId();
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<{ text: string; field?: string } | null>(null);
  const ready = Boolean(password) && confirmation === "CLOSE" && (!twoStep || Boolean(normalizeCode(code)));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready) {
      setProblem({ text: "Enter your password, then type CLOSE to confirm." });
      return;
    }
    setBusy(true);
    setProblem(null);
    try {
      await accountCenterApi.closeAccount({ password, ...(twoStep ? { code: normalizeCode(code) } : {}), confirmation: "CLOSE" });
      closedNavigation.assign("/login?closed=1");
    } catch (err) {
      setBusy(false);
      if (err instanceof ApiError) {
        const body = err.body as
          | { detail?: string; blocking_organizations?: ClosureCheck["blocking_organizations"]; blocking_partners?: ClosureCheck["blocking_partners"] }
          | undefined;
        if (err.code === "sole_owner" && body?.blocking_organizations?.length) {
          onBlocked({ organizations: body.blocking_organizations, partners: [] });
          return;
        }
        if (err.code === "last_partner_admin" && body?.blocking_partners?.length) {
          onBlocked({ organizations: [], partners: body.blocking_partners });
          return;
        }
        if (err.code === "password_invalid" || err.code === "current_password_incorrect") {
          setProblem({ text: "The password is not correct.", field: "password" });
          return;
        }
        if (err.code === "two_step_code_invalid") {
          setProblem({ text: "That code isn't right. Check your authenticator app, or use a recovery code.", field: "code" });
          return;
        }
        if (err.status === 429) {
          setProblem({ text: "Too many attempts. Wait a minute, then try again." });
          return;
        }
      }
      setProblem({ text: describeError(err) });
    }
  }

  const errorId = `${id}-error`;
  const mark = (field: string) => (problem?.field === field ? { "aria-invalid": true as const, "aria-describedby": errorId } : {});
  return (
    <form method="post" className={styles.form} onSubmit={submit} noValidate>
      <p>
        <strong>This cannot be undone.</strong> Confirm it is you.
      </p>
      <div className={styles.fields}>
        <div className={styles.field}>
          <label className="w360-label" htmlFor={`${id}-password`}>
            Password
          </label>
          <input
            id={`${id}-password`}
            className="w360-input"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            {...mark("password")}
          />
        </div>
        {twoStep && (
          <div className={styles.field}>
            <label className="w360-label" htmlFor={`${id}-code`}>
              6-digit code or a recovery code
            </label>
            <input
              id={`${id}-code`}
              className="w360-input"
              type="text"
              autoComplete="one-time-code"
              autoCapitalize="off"
              spellCheck={false}
              maxLength={32}
              required
              value={code}
              onChange={(e) => setCode(e.target.value)}
              {...mark("code")}
            />
          </div>
        )}
        <div className={styles.field}>
          <label className="w360-label" htmlFor={`${id}-confirm`}>
            Type CLOSE to confirm
          </label>
          <input
            id={`${id}-confirm`}
            className="w360-input"
            type="text"
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            required
            value={confirmation}
            onChange={(e) => setConfirmation(e.target.value)}
          />
        </div>
      </div>
      {problem && (
        <p id={errorId} role="alert" className="w360-error-text">
          {problem.text}
        </p>
      )}
      <div className={styles.actions}>
        <Button type="submit" variant="danger" loading={busy} disabled={!ready}>
          Close account permanently
        </Button>
        <Button onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

// --- help -------------------------------------------------------------------------------------

export function HelpAndSupport() {
  return (
    <Panel title="Help & Support">
      <p>
        Can&apos;t sign in, lost your authenticator app and recovery codes, or think someone else is using your account? Requests about your
        account&apos;s security go to WorkEasy360&apos;s own account support - never to a partner.
      </p>
      <p>
        <a href={ACCOUNT_HELP_HREF}>Get help with your account</a>
      </p>
    </Panel>
  );
}
