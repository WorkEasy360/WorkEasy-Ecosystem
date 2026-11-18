"use client";

import { useParams } from "next/navigation";
import { TicketScreen } from "@/features/controlplane/AdminSupport";

export default function Page() {
  const { ticketId } = useParams<{ ticketId: string }>();
  return <TicketScreen ticketId={ticketId} />;
}
