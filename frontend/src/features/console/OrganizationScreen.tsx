"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { consoleApi } from "@/lib/api/console";
import { useLoad } from "@/features/controlplane/ui";
import { FormPopup } from "@/features/controlplane/Modal";
import { Badge, Card, DescriptionList, Page, StatusBadge } from "@/features/controlplane/kit";
import { useSiteSettings } from "@/features/site/SiteShell";
import cp from "@/features/controlplane/ControlPlane.module.css";
import local from "./ConsoleScreens.module.css";
import { PageHeader, roleLabel } from "./consoleUi";
import { useOrgAccess } from "./OrgAccess";

const COLLECTION_WORDS: Record<string, string> = {
  automatic: "Pays online",
  invoice: "Pays by invoice",
  partner_billed: "Billed by your partner",
  partner_wallet: "Paid by your partner",
};

/**
 * The organization (2026-11-11, the User Admin brief Part 2 section 05): its name and
 * status, its workspace, its business and contact details, the partner assigned to it
 * and who answers its support - each read from what the server already says.
 *
 * The name and status come from the access the shell already loaded; a rename reloads
 * that access so the header and this page show the new name together. The slug is URL
 * identity and never changes. The partner is the organization's live partner link (the
 * support desk's answer); the business details are the billing account's, for those
 * who may see billing.
 */
export function OrganizationScreen() {
  const { slug, access, reload, can } = useOrgAccess();
  const org = access?.organization;
  const site = useSiteSettings();
  const canBilling = can("platform.billing.view");
  // Who looks after this organization: the support desk names its partner.
  const [desk] = useLoad(() => consoleApi.supportTickets(slug).then((r) => r.provider ?? null), [slug]);
  const [account] = useLoad(
    () => (canBilling ? consoleApi.billingAccount(slug).then((r) => r.billing_account) : Promise.resolve(null)),
    [slug, canBilling],
  );
  const partner = desk.status === "ready" ? desk.data : null;
  const billing = account.status === "ready" ? account.data : null;

  return (
    <Page>
      <PageHeader
        title="Organization"
        lead="Your organization's profile, workspace, contacts and the partner that looks after it."
        primary={
          org && can("platform.organization.update") ? (
            <RenamePopup current={org.display_name} onSave={(name) => consoleApi.renameOrganization(slug, name)} onDone={reload} />
          ) : undefined
        }
      />
      {org && (
        <div className={local.cardRow}>
          <Card title="Business profile" description="How your organization appears across WorkEasy360.">
            <DescriptionList
              items={[
                { label: "Name", value: org.display_name },
                {
                  label: "Status",
                  value: (
                    <>
                      <StatusBadge value={org.status} />
                      {!org.write_allowed && <div className={cp.muted}>Read-only: changes are paused.</div>}
                    </>
                  ),
                },
                { label: "Your role", value: access?.membership.is_owner ? "Owner" : (access?.membership.roles ?? []).map(roleLabel).join(", ") },
                { label: "Billing currency", value: billing?.currency ?? null, when: canBilling },
                { label: "Billing region", value: billing?.billing_region || null, when: canBilling },
                { label: "How it pays", value: billing ? (COLLECTION_WORDS[billing.collection_mode] ?? billing.collection_mode) : null, when: canBilling },
              ]}
            />
          </Card>
          <Card title="Workspace" description="Where your team signs in to this organization.">
            <DescriptionList
              items={[
                { label: "Web address", value: <span className={cp.mono}>/o/{org.slug}</span> },
                { label: "Support reference", value: <span className={local.nowrap}>{org.slug}</span> },
                { label: "Access", value: org.write_allowed ? "Full access" : "Read-only" },
              ]}
            />
          </Card>
          <Card title="Contacts" description="Who to reach about this organization.">
            <DescriptionList
              items={[
                { label: "Billing email", value: billing?.billing_email || null, when: canBilling },
                {
                  label: "Assigned partner",
                  value:
                    desk.status === "loading" ? (
                      "Loading…"
                    ) : partner ? (
                      <span className={local.contactLine}>
                        <strong>{partner.name}</strong>
                        <Badge tone="info">Partner</Badge>
                      </span>
                    ) : (
                      "None - WorkEasy360 looks after you directly"
                    ),
                },
                {
                  label: "Support contact",
                  value: partner ? (
                    <Link href={`/o/${slug}/help`}>{partner.name}, through Help</Link>
                  ) : site?.support_email ? (
                    <a href={`mailto:${site.support_email}`}>{site.support_email}</a>
                  ) : (
                    <Link href={`/o/${slug}/help`}>WorkEasy360 support, through Help</Link>
                  ),
                },
              ]}
            />
            {canBilling && (
              <p className={local.muted}>
                Change the billing email and tax details in{" "}
                <Link href={`/o/${slug}/settings/billing/profile`}>Billing Profile</Link>.
              </p>
            )}
          </Card>
        </div>
      )}
    </Page>
  );
}

function RenamePopup({
  current,
  onSave,
  onDone,
}: {
  current: string;
  onSave: (name: string) => Promise<unknown>;
  onDone: () => void;
}) {
  const id = useId();
  const hintId = useId();
  const [name, setName] = useState(current);
  const trimmed = name.trim();
  return (
    <FormPopup
      label="Rename"
      buttonClassName="w360-btn w360-btn-secondary"
      buttonAriaLabel="Rename the organization"
      title="Rename the organization"
      size="sm"
      submitLabel="Save name"
      canSubmit={!!trimmed && trimmed !== current}
      onOpen={() => setName(current)}
      onSubmit={async () => {
        await onSave(trimmed);
        return `The organization is now called ${trimmed}.`;
      }}
      onDone={onDone}
    >
      <div className={cp.field}>
        <label className="w360-label" htmlFor={id}>
          Organization name
        </label>
        <input
          id={id}
          className="w360-input"
          required
          maxLength={255}
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-describedby={hintId}
        />
        <p id={hintId} className="w360-hint">
          Shown to your members across Workeasy360. The web address stays the same.
        </p>
      </div>
    </FormPopup>
  );
}
