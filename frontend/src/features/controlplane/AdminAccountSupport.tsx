"use client";

import { useState } from "react";
import { adminConsoleApi, type AccountSupportCase } from "@/lib/api/adminConsole";
import { can, useAdmin } from "./ControlShell";
import { FormPopup } from "./Modal";
import { LoadGate, Table, useLoad, when } from "./ui";
import { TextAreaField } from "./widgets";
import { Alert, Page, PageHeader, StatusChips } from "./kit";

/**
 * Customers > Account support (Accounts V3, the note's 10.3): personal account-security
 * requests sent from Help & Support - signed out or signed in. Central only: never
 * routed to a partner, never tied to an organization. A request proves nothing about
 * who owns an account; recovery still goes through the account's own channels.
 */

const TOPIC_LABELS: Record<string, string> = {
  cannot_sign_in: "Cannot sign in",
  lost_two_step: "Lost two-step verification",
  account_compromised: "Possible misuse",
  closure: "Account closure",
  other: "Something else",
};

const STATUS_CHIPS: { value: AccountSupportCase["status"]; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "resolved", label: "Resolved" },
];

export function AccountSupportScreen() {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_user.manage");
  const [status, setStatus] = useState<AccountSupportCase["status"]>("open");
  const [load, reload] = useLoad(() => adminConsoleApi.accountSupport(status), [status]);
  const [done, setDone] = useState<string | null>(null);
  return (
    <Page>
      <PageHeader
        title="Account support"
        subtitle="Personal account-security requests: sign-in problems, lost two-step verification, possible misuse and closure."
        info="Partners never see these. A request alone never proves who owns an account: confirm through the account's own sign-in email before anything changes."
      />
      <StatusChips label="Request status" value={status} onChange={setStatus} chips={STATUS_CHIPS} />
      {done && <Alert tone="success" title={done} />}
      <LoadGate load={load} what="account support requests">
        {(d) => (
          <Table
            caption={status === "open" ? "Open requests, newest first" : "Resolved requests, newest first"}
            head={["Reference", "From", "Topic", "Request", "Received", status === "open" ? "Actions" : "Resolution"]}
            isEmpty={d.results.length === 0}
            empty={status === "open" ? "No open requests." : "No resolved requests yet."}
          >
            {d.results.map((c) => (
              <tr key={c.id}>
                <th scope="row">{c.reference}</th>
                <td>
                  {c.name || "—"}
                  <br />
                  {c.email}
                </td>
                <td>{TOPIC_LABELS[c.topic] ?? c.topic.replace(/_/g, " ")}</td>
                <td>{c.message}</td>
                <td>{when(c.created_at)}</td>
                <td>
                  {c.status === "resolved" ? (
                    <>
                      {when(c.resolved_at)}
                      {c.note && (
                        <>
                          <br />
                          {c.note}
                        </>
                      )}
                    </>
                  ) : manage ? (
                    <ResolvePopup
                      request={c}
                      onDone={(message) => {
                        setDone(message ?? null);
                        reload();
                      }}
                    />
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </LoadGate>
    </Page>
  );
}

function ResolvePopup({ request, onDone }: { request: AccountSupportCase; onDone: (message: string | undefined) => void }) {
  const [note, setNote] = useState("");
  return (
    <FormPopup
      label="Resolve"
      buttonClassName="w360-btn w360-btn-secondary"
      buttonAriaLabel={`Resolve ${request.reference}`}
      title={`Resolve ${request.reference}`}
      size="sm"
      submitLabel="Resolve"
      canSubmit={note.trim().length > 0}
      onOpen={() => setNote("")}
      onSubmit={async () => {
        await adminConsoleApi.updateAccountSupport(request.id, { status: "resolved", note: note.trim() });
        return `${request.reference} resolved.`;
      }}
      onDone={onDone}
    >
      <TextAreaField
        label="What was done"
        hint="Kept with the request. Never write a password, a code or a recovery code here."
        value={note}
        onChange={setNote}
        maxLength={4000}
      />
    </FormPopup>
  );
}
