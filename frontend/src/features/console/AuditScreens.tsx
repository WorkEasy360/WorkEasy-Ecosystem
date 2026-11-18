"use client";

import { useId, useState } from "react";
import { consoleApi } from "@/lib/api/console";
import { MoreButton, Status, Table, usePaged, when } from "@/features/controlplane/ui";
import { Card, Page, eventLabel } from "@/features/controlplane/kit";
import cp from "@/features/controlplane/ControlPlane.module.css";
import local from "./ConsoleScreens.module.css";
import { PageHeader, PagedState } from "./consoleUi";
import { useOrgAccess } from "./OrgAccess";

/** Admin Activity: the organization's own audit log (`platform.audit.view`), newest first. */
export function AuditLogScreen() {
  const { slug } = useOrgAccess();
  const filterId = useId();
  const hintId = useId();
  const [draft, setDraft] = useState("");
  const [prefix, setPrefix] = useState("");
  const paged = usePaged(
    (cursor) => consoleApi.audit(slug, { action: prefix || undefined, cursor }),
    [slug, prefix],
  );

  return (
    <Page>
      <PageHeader
        title="Admin Activity"
        lead="Every change made in this organization: who or what made it, to what, and whether it was allowed."
      />
      <Card title="Audit log">
      <div className={local.section}>
      <form
        className={local.toolbar}
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          setPrefix(draft.trim());
        }}
      >
        <div className={cp.field}>
          <label className="w360-label" htmlFor={filterId}>
            Action starts with
          </label>
          <input
            id={filterId}
            className="w360-input"
            maxLength={100}
            spellCheck={false}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            aria-describedby={hintId}
          />
        </div>
        <button type="submit" className="w360-btn w360-btn-secondary">
          Filter
        </button>
        {prefix && (
          <button
            type="button"
            className="w360-btn w360-btn-secondary"
            onClick={() => {
              setDraft("");
              setPrefix("");
            }}
          >
            Show everything
          </button>
        )}
      </form>
      <p id={hintId} className={cp.muted}>
        For example <span className={cp.mono}>membership.</span> or <span className={cp.mono}>security.</span>
      </p>
      <PagedState state={paged.state} what="the audit log" />
      {paged.state === "ready" && (
        <>
          <Table
            caption={prefix ? `Audit entries for actions starting “${prefix}”` : "Audit entries"}
            head={["Time", "Action", "Actor", "Resource", "Outcome", "Reason"]}
            isEmpty={paged.rows.length === 0}
            empty="No entries match."
          >
            {paged.rows.map((a) => (
              <tr key={a.id}>
                <td className={local.nowrap}>{when(a.occurred_at)}</td>
                <th scope="row">
                  {eventLabel(a.action)}
                  <div className={`${cp.muted} ${cp.mono}`}>{a.action}</div>
                </th>
                <td>{a.actor_kind.replace(/_/g, " ")}</td>
                <td>
                  {a.resource_type}
                  {a.resource_id && <div className={`${cp.muted} ${cp.mono}`}>{a.resource_id}</div>}
                </td>
                <td>
                  <Status value={a.outcome} />
                </td>
                <td>{a.reason || "—"}</td>
              </tr>
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
      </div>
      </Card>
    </Page>
  );
}

/** Who entered this organization, and from where (coarse network only). */
export function SignInsScreen() {
  const { slug } = useOrgAccess();
  const paged = usePaged((cursor) => consoleApi.signIns(slug, cursor), [slug]);
  return (
    <Page>
      <PageHeader
        title="Sign-in Activity"
        lead="Each time a member opened this organization. Only entry into this organization is shown - never what members do elsewhere."
      />
      <PagedState state={paged.state} what="sign-in activity" />
      {paged.state === "ready" && (
        <>
          <Table
            caption="Sign-ins, newest first"
            head={["Time", "Member", "Device", "Network"]}
            isEmpty={paged.rows.length === 0}
            empty="No sign-ins yet."
          >
            {paged.rows.map((s, i) => (
              <tr key={`${s.membership_id}-${s.occurred_at}-${i}`}>
                <td className={local.nowrap}>{when(s.occurred_at)}</td>
                <th scope="row">
                  {s.name || s.email}
                  {s.name && <div className={cp.muted}>{s.email}</div>}
                </th>
                <td>{s.device_label || "—"}</td>
                <td className={cp.mono}>{s.ip_prefix || "—"}</td>
              </tr>
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
    </Page>
  );
}
