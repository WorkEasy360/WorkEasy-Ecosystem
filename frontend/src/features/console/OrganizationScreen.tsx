"use client";

import { useId, useState } from "react";
import { consoleApi } from "@/lib/api/console";
import { Status, useAction } from "@/features/controlplane/ui";
import cp from "@/features/controlplane/ControlPlane.module.css";
import styles from "./Console.module.css";
import local from "./ConsoleScreens.module.css";
import { PageHeader, Panel } from "./consoleUi";
import { useOrgAccess } from "./OrgAccess";

/**
 * The organization's profile. Read from the access the shell already loaded
 * (no request of its own); a rename reloads that access so the header and
 * this page show the new name together. The slug is URL identity and never
 * changes.
 */
export function OrganizationScreen() {
  const { slug, access, reload, can } = useOrgAccess();
  const { act, busy, feedback } = useAction(reload);
  const org = access?.organization;

  return (
    <div className={styles.page}>
      <PageHeader title="Organization profile" lead="Your organization's name and status." />
      {feedback}
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
        </Panel>
      )}
      {org && can("platform.organization.update") && (
        <Panel title="Rename the organization">
          <RenameForm
            key={org.display_name}
            current={org.display_name}
            busy={busy}
            onSave={(name) =>
              void act(() => consoleApi.renameOrganization(slug, name), `The organization is now called ${name}.`)
            }
          />
        </Panel>
      )}
    </div>
  );
}

function RenameForm({
  current,
  busy,
  onSave,
}: {
  current: string;
  busy: boolean;
  onSave: (name: string) => void;
}) {
  const id = useId();
  const hintId = useId();
  const [name, setName] = useState(current);
  const trimmed = name.trim();
  return (
    <form
      className={cp.form}
      onSubmit={(e) => {
        e.preventDefault();
        onSave(trimmed);
      }}
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
      <div>
        <button
          type="submit"
          className="w360-btn w360-btn-primary"
          disabled={busy || !trimmed || trimmed === current}
        >
          Save name
        </button>
      </div>
    </form>
  );
}
