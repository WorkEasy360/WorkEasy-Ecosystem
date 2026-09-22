"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { getMembers } from "@/lib/api/organizations";
import { ApiError } from "@/lib/api/client";
import type { MembershipSummary } from "@/lib/api/types";

type Load =
  | { status: "loading" }
  | { status: "forbidden" }
  | { status: "error" }
  | { status: "ready"; members: MembershipSummary[] };

/** Task 14 (member half): "which users belong to organization". Seats and
 * product access per member are on Settings → Products, so an admin
 * answers "who + what" by reading the two pages together rather than one
 * page trying to be both. */
export default function MembersPage() {
  const params = useParams<{ orgSlug: string }>();
  const [load, setLoad] = useState<Load>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    getMembers(params.orgSlug)
      .then(({ results }) => !cancelled && setLoad({ status: "ready", members: results }))
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 403) setLoad({ status: "forbidden" });
        else setLoad({ status: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, [params.orgSlug]);

  return (
    <div>
      <h1 style={{ fontSize: 22, marginBottom: "var(--w360-space-5)" }}>Members</h1>

      {load.status === "loading" && <p aria-live="polite">Loading members…</p>}
      {load.status === "forbidden" && (
        <p role="alert">You don&apos;t have permission to view members.</p>
      )}
      {load.status === "error" && <p role="alert">Couldn&apos;t load members right now.</p>}

      {load.status === "ready" && (
        <div className="w360-card" style={{ overflow: "hidden" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <caption className="w360-visually-hidden">Organization members</caption>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--w360-border)" }}>
                <th scope="col" style={{ padding: "var(--w360-space-3) var(--w360-space-4)" }}>
                  Email
                </th>
                <th scope="col" style={{ padding: "var(--w360-space-3) var(--w360-space-4)" }}>
                  Role
                </th>
                <th scope="col" style={{ padding: "var(--w360-space-3) var(--w360-space-4)" }}>
                  Status
                </th>
              </tr>
            </thead>
            <tbody>
              {load.members.map((member) => (
                <tr key={member.membership_id} style={{ borderTop: "1px solid var(--w360-border)" }}>
                  <td style={{ padding: "var(--w360-space-3) var(--w360-space-4)" }}>
                    {member.email}
                    {member.is_owner && (
                      <span className="w360-badge w360-badge-neutral" style={{ marginLeft: 8 }}>
                        Owner
                      </span>
                    )}
                  </td>
                  <td style={{ padding: "var(--w360-space-3) var(--w360-space-4)" }}>
                    {member.member_type}
                  </td>
                  <td style={{ padding: "var(--w360-space-3) var(--w360-space-4)" }}>
                    <span
                      className={`w360-badge ${
                        member.status === "active" ? "w360-badge-success" : "w360-badge-neutral"
                      }`}
                    >
                      {member.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {load.members.length === 0 && (
            <p style={{ padding: "var(--w360-space-4)" }}>No members found.</p>
          )}
        </div>
      )}
    </div>
  );
}
