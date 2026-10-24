"use client";

import Link from "next/link";
import { useSession } from "@/lib/auth/useSession";
import styles from "./Console.module.css";
import local from "./ConsoleScreens.module.css";
import { PageHeader, Panel } from "./consoleUi";
import { useOrgAccess } from "./OrgAccess";

/**
 * The signed-in person's shared identity - the same in every organization.
 * Product-specific profile settings belong to the product that owns them.
 */
export function ProfileScreen() {
  const { slug } = useOrgAccess();
  const session = useSession();
  return (
    <div className={styles.page}>
      <PageHeader title="Your profile" lead="Your name and email are the same in every organization you belong to." />
      <Panel title="Account">
        {session.status === "loading" && <p aria-live="polite">Loading…</p>}
        {session.status === "error" && <p role="alert">Couldn&apos;t load your profile right now.</p>}
        {session.status === "ready" && session.session.authenticated && (
          <dl className={local.details}>
            <div>
              <dt>Name</dt>
              <dd>{session.session.name || "—"}</dd>
            </div>
            <div>
              <dt>Email</dt>
              <dd>{session.session.email}</dd>
            </div>
          </dl>
        )}
        <p className={local.textBlock}>
          <Link href={`/o/${slug}/settings/security`}>Password and sessions</Link>
        </p>
      </Panel>
    </div>
  );
}
