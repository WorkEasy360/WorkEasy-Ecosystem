"use client";

import { useId, useState } from "react";
import { consoleApi } from "@/lib/api/console";
import { Status } from "@/features/controlplane/ui";
import { FormPopup } from "@/features/controlplane/Modal";
import cp from "@/features/controlplane/ControlPlane.module.css";
import styles from "./Console.module.css";
import local from "./ConsoleScreens.module.css";
import { PageHeader, Panel } from "./consoleUi";
import { useOrgAccess } from "./OrgAccess";

/**
 * The organization's profile. Read from the access the shell already loaded
 * (no request of its own); a rename reloads that access so the header and
 * this page show the new name together. The slug is URL identity and never
 * changes. The name is changed in a popup (2026-11-05, every form in a popup).
 */
export function OrganizationScreen() {
  const { slug, access, reload, can } = useOrgAccess();
  const org = access?.organization;

  return (
    <div className={styles.page}>
      <PageHeader title="Organization profile" lead="Your organization's name and status." />
      {org && (
        <Panel title="Details">
          <dl className={local.details}>
            <div>
              <dt>Name</dt>
              <dd>{org.display_name}</dd>
            </div>
            <div>
              <dt>Web address</dt>
              <dd className={cp.mono}>/o/{org.slug}</dd>
            </div>
            <div>
              <dt>Status</dt>
              <dd>
                <Status value={org.status} />
                {!org.write_allowed && <div className={cp.muted}>Read-only: changes are paused.</div>}
              </dd>
            </div>
          </dl>
          {can("platform.organization.update") && (
            <div className={local.actions}>
              <RenamePopup current={org.display_name} onSave={(name) => consoleApi.renameOrganization(slug, name)} onDone={reload} />
            </div>
          )}
        </Panel>
      )}
    </div>
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
