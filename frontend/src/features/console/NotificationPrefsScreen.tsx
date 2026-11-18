"use client";

import { useState } from "react";
import { orgAccountApi, type NotificationPreferences } from "@/lib/api/orgAccount";
import { Alert, Button, Card, Page, PageHeader } from "@/features/controlplane/kit";
import { describeError, useLoad } from "@/features/controlplane/ui";
import { useOrgAccess } from "./OrgAccess";
import styles from "./wallets/Wallets.module.css";

/**
 * Notification Preferences (User Admin Panel, 2026-11-11): which alerts the bell
 * shows the signed-in member, per category. Their own choice in this organization
 * only - it never changes what anyone else sees, and an alert they may not see
 * (billing without the billing permission) stays hidden whatever they choose.
 */
export function NotificationPrefsScreen() {
  const { slug } = useOrgAccess();
  const [load, reload] = useLoad(() => orgAccountApi.notificationPreferences(slug), [slug]);
  const [draft, setDraft] = useState<Record<string, boolean> | null>(null);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<{ ok: boolean; text: string } | null>(null);

  const current = (prefs: NotificationPreferences) =>
    Object.fromEntries(prefs.categories.map((c) => [c.key, draft?.[c.key] ?? c.in_app]));
  const changed = (prefs: NotificationPreferences) => prefs.categories.some((c) => (draft?.[c.key] ?? c.in_app) !== c.in_app);

  async function save(prefs: NotificationPreferences) {
    setBusy(true);
    setOutcome(null);
    const values = current(prefs);
    try {
      await orgAccountApi.updateNotificationPreferences(slug, {
        categories: prefs.categories.map((c) => ({ key: c.key, in_app: Boolean(values[c.key]) })),
      });
      setDraft(null);
      setOutcome({ ok: true, text: "Notification preferences saved." });
      reload();
    } catch (err) {
      setOutcome({ ok: false, text: describeError(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Page>
      <PageHeader title="Notification Preferences" subtitle="Choose which alerts appear under the bell for you in this organization." />
      {outcome && <Alert tone={outcome.ok ? "success" : "danger"} title={outcome.text} />}
      {load.status === "loading" && (
        <Card title="In-app alerts">
          <p aria-live="polite">Loading your preferences…</p>
        </Card>
      )}
      {load.status === "error" && <Alert tone="danger" title="Your preferences could not be loaded">{load.message}</Alert>}
      {load.status === "forbidden" && <Alert tone="danger" title="Your preferences could not be loaded" />}
      {load.status === "ready" && (
        <Card
          title="In-app alerts"
          description="Turning a category off hides it from your bell only. Emails about payments and your account are still sent."
          actions={
            <Button variant="primary" onClick={() => void save(load.data)} disabled={busy || !changed(load.data)} loading={busy}>
              Save preferences
            </Button>
          }
        >
          <ul className={styles.prefList}>
            {load.data.categories.map((c) => {
              const on = current(load.data)[c.key];
              return (
                <li key={c.key} className={styles.prefRow}>
                  <span className={styles.prefText}>
                    <strong id={`pref-${c.key}`}>{c.label}</strong>
                    <span id={`pref-${c.key}-text`}>{c.description}</span>
                  </span>
                  <label className={styles.switch}>
                    <input
                      type="checkbox"
                      role="switch"
                      checked={on}
                      aria-labelledby={`pref-${c.key}`}
                      aria-describedby={`pref-${c.key}-text`}
                      onChange={(e) => setDraft((d) => ({ ...(d ?? {}), [c.key]: e.target.checked }))}
                    />
                    <span aria-hidden="true">{on ? "On" : "Off"}</span>
                  </label>
                </li>
              );
            })}
          </ul>
        </Card>
      )}
    </Page>
  );
}
