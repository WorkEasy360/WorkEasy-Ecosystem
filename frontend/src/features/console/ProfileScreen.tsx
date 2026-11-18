"use client";

import Link from "next/link";
import { useSession } from "@/lib/auth/useSession";
import { Avatar, Card, DescriptionList, LoadingBlock, Page } from "@/features/controlplane/kit";
import local from "./ConsoleScreens.module.css";
import { PageHeader, roleLabel } from "./consoleUi";
import { useOrgAccess } from "./OrgAccess";

/**
 * My Profile: the signed-in person's shared identity - the same in every organization
 * - and their role in this one. Product-specific profile settings belong to the
 * product that owns them.
 */
export function ProfileScreen() {
  const { slug, access } = useOrgAccess();
  const session = useSession();
  const role = access?.membership.is_owner ? "Owner" : (access?.membership.roles ?? []).map(roleLabel).join(", ");
  return (
    <Page>
      <PageHeader title="My Profile" lead="Your name and email are the same in every organization you belong to." />
      <div className={local.cardRow}>
        <Card title="Account">
          {session.status === "loading" && <LoadingBlock what="your profile" />}
          {session.status === "error" && <p role="alert">Couldn&apos;t load your profile right now.</p>}
          {session.status === "ready" && session.session.authenticated && (
            <div className={local.section}>
              <Avatar name={session.session.name || session.session.email || "?"} size="lg" />
              <DescriptionList
                items={[
                  { label: "Name", value: session.session.name || null },
                  { label: "Email", value: session.session.email },
                  { label: "Role here", value: role || null },
                  { label: "Organization", value: access?.organization.display_name },
                ]}
              />
            </div>
          )}
        </Card>
        <Card title="Security and preferences">
          <ul className={local.featureList}>
            <li>
              <Link href={`/o/${slug}/settings/security`}>Password and sessions</Link>
            </li>
            <li>
              <Link href={`/o/${slug}/settings/sign-in-preferences`}>How you sign in to each product</Link>
            </li>
            <li>
              <Link href={`/o/${slug}/settings/notifications`}>Notification preferences</Link>
            </li>
          </ul>
        </Card>
      </div>
    </Page>
  );
}
