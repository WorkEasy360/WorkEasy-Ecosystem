"use client";

import { useState } from "react";
import { accountCenterApi, type AccountProfile } from "@/lib/api/console";
import { LoadGate, useLoad } from "@/features/controlplane/ui";
import { Panel } from "@/features/console/consoleUi";
import { ChangePasswordForm } from "@/features/console/ChangePasswordForm";
import { TwoStepPanel } from "@/features/console/TwoStepPanel";
import { SignInPreferencesScreen } from "@/features/signin/SignInPreferencesScreen";
import { CenterShell } from "./CenterShell";
import styles from "./AccountShell.module.css";
import { ActiveSessions, LoginHistory } from "./CenterSessions";
import { ConnectedLogins } from "./ConnectedLogins";
import { ProfileCard } from "./ProfileForm";
import { Greeting, ProfileSummary, SectionCards, SecurityOverview } from "./ProfileOverview";
import { SignInEmailCard } from "./SignInEmailCard";
import { CloseAccount, HelpAndSupport, NotificationSettings, RecentSecurityActivity, YourOrganizations } from "./SettingsSections";

/**
 * The Accounts Control Center's four pages (Accounts V3, the note's section 04):
 * My Profile (opens first), Login & Security, Devices & Sessions and Settings. Each is
 * a column of simple cards; everything organizational is a link to User Admin.
 */

/** Where Home's "Edit Profile" goes: My Profile with its details open for editing. */
export const EDIT_PROFILE_HREF = "/account/profile?edit=1";

/**
 * Home (2026-11-27, the owner's reference image "User Account Dashboard"): the page
 * that opens first - a greeting, the person's card, the four sections as cards,
 * security at a glance and recent security activity. Read only: every change happens
 * on its section's page.
 */
export function CenterHome() {
  const [load] = useLoad(() => accountCenterApi.profile(), []);
  const shown = load.status === "ready" ? load.data : null;
  return (
    <CenterShell current="home" person={shown}>
      <LoadGate load={load} what="your account">
        {(profile) => (
          <>
            <Greeting profile={profile} />
            <ProfileSummary profile={profile} onEdit={() => window.location.assign(EDIT_PROFILE_HREF)} />
            <SectionCards />
            <SecurityOverview profile={profile} />
          </>
        )}
      </LoadGate>
    </CenterShell>
  );
}

/**
 * My Profile: who you are - photo, name, verified email, WorkEasy360 ID - and the
 * details you can edit. Opened from Home's "Edit Profile" (`?edit=1`), the details
 * open for editing.
 */
export function CenterProfile() {
  const [load] = useLoad(() => accountCenterApi.profile(), []);
  // What the last save answered, until the page is opened again.
  const [saved, setSaved] = useState<AccountProfile | null>(null);
  // Home's "Edit Profile" opens this page with the details open (`?edit=1`). The
  // details render only after the profile loads in the browser, so the server's
  // first render never shows them either way.
  const [editing, setEditing] = useState(() => typeof window !== "undefined" && new URLSearchParams(window.location.search).get("edit") === "1");
  const shown = saved ?? (load.status === "ready" ? load.data : null);
  const edit = () => {
    setEditing(true);
    // The details card sits below the person's card: bring it into view.
    requestAnimationFrame(() => document.getElementById("profile-details")?.scrollIntoView?.({ behavior: "smooth", block: "start" }));
  };
  return (
    <CenterShell current="profile" title="My Profile" lead="Your photo, name and the details products show." person={shown}>
      <LoadGate load={load} what="your profile">
        {(data) => {
          const profile = saved ?? data;
          return (
            <>
              <ProfileSummary profile={profile} onEdit={edit} showId />
              <div id="profile-details" className={styles.anchor}>
                <ProfileCard profile={profile} onSaved={setSaved} editing={editing} onEditingChange={setEditing} />
              </div>
            </>
          );
        }}
      </LoadGate>
    </CenterShell>
  );
}

/** Login & Security: the sign-in email, the password, two-step verification and how products sign you in. */
export function CenterLoginSecurity() {
  const [load, reload] = useLoad(() => accountCenterApi.profile(), []);
  return (
    <CenterShell
      current="security"
      title="Login & Security"
      lead="How you sign in: your email, your password, two-step verification, and how products sign you in."
    >
      <LoadGate load={load} what="your sign-in email">
        {(p) => <SignInEmailCard email={p.email} verified={p.email_verified} twoStep={p.two_step_enabled} onChanged={reload} />}
      </LoadGate>
      <Panel title="Password" description="Changing it signs out every other device; this one stays signed in.">
        <ChangePasswordForm popup />
        <p>
          Forgotten your password? <a href="/forgot-password">Reset it by email</a>: a link goes to your sign-in email, and resetting it signs
          you out everywhere.
        </p>
      </Panel>
      <TwoStepPanel />
      <SignInPreferencesScreen embedded />
    </CenterShell>
  );
}

/** Devices & Sessions: where you are signed in, the products you signed in to, and your sign-in history. */
export function CenterDevices() {
  return (
    <CenterShell
      current="sessions"
      title="Devices & Sessions"
      lead="Every device and browser where this account is signed in, the products you signed in to with it, and your sign-in history."
    >
      <ActiveSessions />
      <ConnectedLogins />
      <LoginHistory />
    </CenterShell>
  );
}

/** Settings: notifications, recent security activity, your organizations (in User Admin) and closing the account. */
export function CenterSettings({ appOrigin }: { appOrigin: string }) {
  return (
    <CenterShell current="settings" title="Settings" lead="Your notifications, recent security activity, your organizations and closing your account.">
      <NotificationSettings />
      <div id="security-activity" className={styles.anchor}>
        <RecentSecurityActivity />
      </div>
      <YourOrganizations appOrigin={appOrigin} />
      <CloseAccount appOrigin={appOrigin} />
      <HelpAndSupport />
    </CenterShell>
  );
}
