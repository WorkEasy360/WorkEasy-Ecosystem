"use client";

import { Icon } from "@/components/sidebar/icons";
import { accountApi, type AccountEvent, type AccountProfile } from "@/lib/api/console";
import { LoadGate, useLoad, when } from "@/features/controlplane/ui";
import { Badge, CopyButton } from "@/features/controlplane/kit";
import { countryName } from "@/features/controlplane/countries";
import { describeAccountEvent } from "@/features/console/MySecurityScreen";
import { ProfilePicture, languageName, timeZoneLabel } from "./ProfileForm";
import styles from "./AccountOverview.module.css";

/**
 * Home's overview (2026-11-27, the owner's reference image "User Account
 * Dashboard", to be matched exactly): a greeting, the person's card, the four
 * sections as cards, their account security at a glance and their recent security
 * activity.
 *
 * The reference's mobile number is drawn as "Not added": Accounts stores no phone
 * number and has no SMS provider, so nothing may claim a verified phone or imply
 * phone sign-in (V3 8.2; the owner's choice, 2026-11-27). Everything shown here is
 * read from the person's own account; every change still happens on its section's
 * page.
 */

/** Morning, afternoon or evening where the person is: their time zone when they set one. */
export function greetingFor(now: Date, timeZone?: string): string {
  let hour = now.getHours();
  if (timeZone) {
    try {
      hour = Number(new Intl.DateTimeFormat("en-GB", { hour: "numeric", hourCycle: "h23", timeZone }).format(now));
    } catch {
      /* an unknown zone: the device's hour */
    }
  }
  if (hour >= 5 && hour < 12) return "Good morning";
  if (hour >= 12 && hour < 17) return "Good afternoon";
  return "Good evening";
}

const firstName = (profile: Pick<AccountProfile, "name" | "email">) => (profile.name.trim().split(/\s+/)[0] || "").trim();

export function Greeting({ profile }: { profile: AccountProfile }) {
  const name = firstName(profile);
  return (
    <div className={styles.greeting}>
      <h1>
        {greetingFor(new Date(), profile.time_zone || undefined)}
        {name ? `, ${name}` : ""}!{" "}
        <span className={styles.wave} aria-hidden="true">
          {"\u{1F44B}"}
        </span>
      </h1>
      <p>This is your personal WorkEasy360 account.</p>
    </div>
  );
}

/** The mobile row: Accounts has no phone number to show yet (see above). */
export const MOBILE_NOT_ADDED = "Not added - coming soon";

/** The person's card: photo, name, verified email, mobile, and where they are; on
 * My Profile also the WorkEasy360 ID. */
export function ProfileSummary({ profile, onEdit, showId = false }: { profile: AccountProfile; onEdit: () => void; showId?: boolean }) {
  const facts = [
    { icon: "globe", label: "Country", value: profile.country ? countryName(profile.country) : "Not set" },
    { icon: "translate", label: "Language", value: profile.language ? languageName(profile.language) : "Not set" },
    { icon: "clock", label: "Time Zone", value: profile.time_zone ? timeZoneLabel(profile.time_zone) : "Not set" },
  ];
  return (
    <section className={styles.hero} aria-label="Your WorkEasy360 account">
      <div className={styles.photoWrap}>
        <ProfilePicture profile={profile} />
        <button type="button" className={styles.photoButton} onClick={onEdit} aria-label="Change photo">
          <Icon name="upload" size={16} />
        </button>
      </div>
      <div className={styles.who}>
        <h2 className={styles.name}>{profile.name || "Name not set"}</h2>
        {profile.email_verified ? (
          <Badge tone="success" icon="checkCircle">
            Verified Email
          </Badge>
        ) : (
          <Badge tone="warning">Email not verified</Badge>
        )}
        <p className={styles.email}>{profile.email}</p>
        {profile.pending_email && (
          <p className={styles.inline}>
            <Badge tone="info">Pending verification</Badge>
            <span>
              Changing to {profile.pending_email.new_email} - <a href="/account/security">see Login &amp; Security</a>
            </span>
          </p>
        )}
        <p className={styles.phoneRow}>
          <Icon name="device" size={18} />
          <span className={styles.muted}>Mobile: {MOBILE_NOT_ADDED}</span>
        </p>
        {showId && (
          <p className={styles.idRow}>
            <span className={styles.muted}>WorkEasy360 ID</span>
            <span className={styles.mono}>{profile.user_id}</span>
            <CopyButton value={profile.user_id} label="Copy WorkEasy360 ID" />
          </p>
        )}
      </div>
      <dl className={styles.facts}>
        {facts.map((f) => (
          <div key={f.label}>
            <Icon name={f.icon} size={26} />
            <dd>{f.value}</dd>
            <dt>{f.label}</dt>
          </div>
        ))}
      </dl>
      <button type="button" className={styles.editButton} onClick={onEdit}>
        <Icon name="edit" size={18} /> Edit Profile
      </button>
    </section>
  );
}

const SECTION_CARDS = [
  { tone: "green", icon: "user", title: "My Profile", text: "View and update your personal details.", href: "/account/profile" },
  { tone: "blue", icon: "shield", title: "Login & Security", text: "Manage login, password and two-step verification.", href: "/account/security" },
  { tone: "purple", icon: "monitor", title: "Devices & Sessions", text: "View active sessions and sign out from devices.", href: "/account/sessions" },
  { tone: "orange", icon: "settings", title: "Settings", text: "Notifications, security activity and preferences.", href: "/account/settings" },
] as const;

export function SectionCards() {
  return (
    <ul className={styles.cards} aria-label="Your account's sections">
      {SECTION_CARDS.map((c) => (
        <li key={c.title} className={styles.card} data-tone={c.tone}>
          <span className={styles.cardIcon} aria-hidden="true">
            <Icon name={c.icon} size={28} />
          </span>
          <a href={c.href} className={styles.cardLink}>
            {c.title}
          </a>
          <p>{c.text}</p>
          <span className={styles.chevron} aria-hidden="true">
            <Icon name="chevronRight" size={18} />
          </span>
        </li>
      ))}
    </ul>
  );
}

/** When the password last changed, as far as the recent activity tells - never invented. */
export function lastPasswordChange(events: AccountEvent[]): string | null {
  const hit = events
    .filter((e) => e.kind === "password_changed" || e.kind === "password_reset")
    .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at))[0];
  return hit ? hit.occurred_at : null;
}

const ACTIVITY_ICON: Record<string, string> = {
  sign_in: "monitor",
  sign_in_failed: "alert",
  password_changed: "key", // pragma: allowlist secret
  password_reset: "key", // pragma: allowlist secret
  two_step_enabled: "shield",
  two_step_disabled: "shield",
  session_revoked: "logout",
  other_sessions_revoked: "logout",
  signed_out_everywhere: "logout",
  email_changed: "mail",
  email_change_requested: "mail",
  email_verified: "mail",
  profile_updated: "user",
};

function SecuritySummary({ profile, events }: { profile: AccountProfile; events: AccountEvent[] | null }) {
  const changed = events ? lastPasswordChange(events) : null;
  const secure = profile.email_verified && profile.two_step_enabled;
  return (
    <section className={styles.panel} aria-labelledby="w360-account-security">
      <header className={styles.panelHead}>
        <h2 id="w360-account-security">
          <Icon name="shield" size={24} /> Account Security
        </h2>
        {secure ? (
          <Badge tone="success" icon="checkCircle">
            Secure Account
          </Badge>
        ) : (
          <Badge tone="warning">Review your security</Badge>
        )}
      </header>
      <ul className={styles.rows}>
        <li>
          <Icon name="mail" size={20} />
          <span className={styles.rowLabel}>Email</span>
          <span className={styles.rowValue}>{profile.email}</span>
          {profile.email_verified ? (
            <Badge tone="success" icon="checkCircle">
              Verified
            </Badge>
          ) : (
            <Badge tone="warning">Not verified</Badge>
          )}
        </li>
        <li>
          <Icon name="device" size={20} />
          <span className={styles.rowLabel}>Mobile</span>
          <span className={styles.rowValue}>Not added</span>
          <Badge tone="neutral">Coming soon</Badge>
        </li>
        <li>
          <Icon name="lock" size={20} />
          <span className={styles.rowLabel}>Password</span>
          <span className={styles.rowValue}>{changed ? `Last changed: ${when(changed)}` : "Change it any time"}</span>
          <a href="/account/security" className={styles.rowLink} aria-label="Password - Login & Security">
            <Icon name="chevronRight" size={18} />
          </a>
        </li>
        <li>
          <Icon name="shield" size={20} />
          <span className={styles.rowLabel}>Two-Step Verification (2FA)</span>
          <span className={styles.rowValue} />
          {profile.two_step_enabled ? (
            <Badge tone="success" icon="checkCircle">
              Enabled
            </Badge>
          ) : (
            <Badge tone="warning">Off</Badge>
          )}
          <a href="/account/security" className={styles.rowLink} aria-label="Two-step verification - Login & Security">
            <Icon name="chevronRight" size={18} />
          </a>
        </li>
      </ul>
    </section>
  );
}

function RecentActivity({ events }: { events: AccountEvent[] }) {
  const latest = [...events].sort((a, b) => b.occurred_at.localeCompare(a.occurred_at)).slice(0, 4);
  return latest.length === 0 ? (
    <p className={styles.muted}>No recent activity.</p>
  ) : (
    <ul className={styles.activity}>
      {latest.map((e, i) => (
        <li key={`${e.occurred_at}-${i}`}>
          <Icon name={ACTIVITY_ICON[e.kind] ?? "history"} size={20} />
          <span>
            {describeAccountEvent(e)}
            {e.device_label && <span className={styles.muted}> - {e.device_label}</span>}
          </span>
          <time dateTime={e.occurred_at} className={styles.muted}>
            {when(e.occurred_at)}
          </time>
        </li>
      ))}
    </ul>
  );
}

/** Security at a glance and recent security activity, side by side. */
export function SecurityOverview({ profile }: { profile: AccountProfile }) {
  const [load] = useLoad(() => accountApi.activity(), []);
  const events = load.status === "ready" ? load.data.events : null;
  return (
    <div className={styles.twoUp}>
      <SecuritySummary profile={profile} events={events} />
      <section className={styles.panel} aria-labelledby="w360-recent-activity">
        <header className={styles.panelHead}>
          <h2 id="w360-recent-activity">
            <Icon name="clock" size={24} /> Recent Security Activity
          </h2>
          <a href="/account/settings#security-activity" className={styles.viewAll}>
            View All <Icon name="arrowRight" size={16} />
          </a>
        </header>
        <LoadGate load={load} what="your recent activity">
          {(d) => <RecentActivity events={d.events} />}
        </LoadGate>
      </section>
    </div>
  );
}
