"use client";

import { useEffect, useState } from "react";
import { assignSeat, getSeatOverview, releaseSeat } from "@/lib/api/launcher";
import type { SeatOverview } from "@/lib/api/types";
import { ApiError } from "@/lib/api/client";

type Load =
  | { status: "loading" }
  | { status: "forbidden" }
  | { status: "error" }
  | { status: "ready"; overview: SeatOverview };

export function SeatTable({ orgSlug, productCode }: { orgSlug: string; productCode: string }) {
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [pendingId, setPendingId] = useState<string | null>(null);

  function refresh() {
    getSeatOverview(orgSlug, productCode)
      .then((overview) => setLoad({ status: "ready", overview }))
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 403) setLoad({ status: "forbidden" });
        else setLoad({ status: "error" });
      });
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgSlug, productCode]);

  async function toggle(membershipId: string, held: boolean) {
    setPendingId(membershipId);
    try {
      if (held) {
        await releaseSeat(orgSlug, productCode, membershipId);
      } else {
        await assignSeat(orgSlug, productCode, membershipId);
      }
      refresh();
    } catch {
      // The table simply keeps its last known-good state; a toast/error
      // surface is a reasonable next iteration, out of Phase 4's scope.
    } finally {
      setPendingId(null);
    }
  }

  if (load.status === "loading") return <p aria-live="polite">Loading seats…</p>;
  if (load.status === "forbidden") {
    return <p role="alert">You don&apos;t have permission to manage seats for this product.</p>;
  }
  if (load.status === "error") return <p role="alert">Couldn&apos;t load seats right now.</p>;

  const { overview } = load;
  return (
    <div>
      <p style={{ color: "var(--w360-text-muted)", marginBottom: "var(--w360-space-4)" }}>
        Seats: <strong style={{ color: "var(--w360-text)" }}>{overview.seats_assigned}</strong> /{" "}
        {overview.seats_total}
      </p>
      <div className="w360-card" style={{ overflow: "hidden" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <caption className="w360-visually-hidden">
            Seat assignment for {overview.display_name}
          </caption>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid var(--w360-border)" }}>
              <th scope="col" style={{ padding: "var(--w360-space-3) var(--w360-space-4)" }}>
                Member
              </th>
              <th scope="col" style={{ padding: "var(--w360-space-3) var(--w360-space-4)" }}>
                Seat
              </th>
            </tr>
          </thead>
          <tbody>
            {overview.members.map((member) => (
              <tr key={member.membership_id} style={{ borderTop: "1px solid var(--w360-border)" }}>
                <td style={{ padding: "var(--w360-space-3) var(--w360-space-4)" }}>{member.email}</td>
                <td style={{ padding: "var(--w360-space-3) var(--w360-space-4)" }}>
                  <button
                    className="w360-btn w360-btn-secondary"
                    onClick={() => toggle(member.membership_id, member.held)}
                    disabled={
                      pendingId === member.membership_id ||
                      (!member.held && overview.seats_assigned >= overview.seats_total)
                    }
                    aria-pressed={member.held}
                  >
                    {member.held ? "✓ Assigned" : "— Assign"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
