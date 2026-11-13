"use client";

import { useEffect, useState } from "react";
import { assignSeat, getSeatOverview, releaseSeat } from "@/lib/api/launcher";
import type { SeatOverview } from "@/lib/api/types";
import { ApiError } from "@/lib/api/client";
import { Badge, Button, EmptyState, LoadingBlock, formatNumber } from "@/features/controlplane/kit";
import { Table, describeError } from "@/features/controlplane/ui";
import local from "@/features/console/ConsoleScreens.module.css";

type Load =
  | { status: "loading" }
  | { status: "forbidden" }
  | { status: "error" }
  | { status: "ready"; overview: SeatOverview };

/**
 * Who holds a seat for one per-seat product, and assigning or releasing one. The
 * server decides: a full product refuses another seat whatever this table shows, and
 * a refusal is said in its own words (2026-11-11; it used to be dropped silently).
 */
export function SeatTable({ orgSlug, productCode }: { orgSlug: string; productCode: string }) {
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

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
    setProblem(null);
    try {
      if (held) {
        await releaseSeat(orgSlug, productCode, membershipId);
      } else {
        await assignSeat(orgSlug, productCode, membershipId);
      }
      refresh();
    } catch (err) {
      // The table keeps its last known-good state and says why.
      setProblem(describeError(err));
    } finally {
      setPendingId(null);
    }
  }

  if (load.status === "loading") return <LoadingBlock what="seats" />;
  if (load.status === "forbidden") {
    return (
      <div role="alert">
        <EmptyState icon="lock" title="You don't have permission to manage seats for this product." hint="Contact Organization Admin." compact />
      </div>
    );
  }
  if (load.status === "error") {
    return (
      <div role="alert">
        <EmptyState icon="alert" title="Couldn't load seats right now." compact />
      </div>
    );
  }

  const { overview } = load;
  const full = overview.seats_assigned >= overview.seats_total;
  return (
    <div className={local.section}>
      <p className={local.contactLine}>
        <span className={local.muted}>Seats in use</span>
        <strong>
          {formatNumber(overview.seats_assigned)} of {formatNumber(overview.seats_total)}
        </strong>
        {full && <Badge tone="warning">All seats assigned</Badge>}
      </p>
      {problem && (
        <p role="alert" className="w360-error-text">
          {problem}
        </p>
      )}
      <Table
        caption={`Seat assignment for ${overview.display_name}`}
        head={["Member", "Seat"]}
        isEmpty={overview.members.length === 0}
        empty="No members to assign yet."
      >
        {overview.members.map((member) => (
          <tr key={member.membership_id}>
            <td>{member.email}</td>
            <td>
              <Button
                size="sm"
                variant={member.held ? "secondary" : "primary"}
                icon={member.held ? "check" : "plus"}
                onClick={() => void toggle(member.membership_id, member.held)}
                disabled={pendingId === member.membership_id || (!member.held && full)}
                aria-pressed={member.held}
                aria-label={member.held ? `Assigned to ${member.email} - choose to release the seat` : `Assign a seat to ${member.email}`}
              >
                {member.held ? "Assigned" : "Assign"}
              </Button>
            </td>
          </tr>
        ))}
      </Table>
    </div>
  );
}
