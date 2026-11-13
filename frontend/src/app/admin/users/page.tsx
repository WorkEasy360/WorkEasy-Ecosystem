"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Users } from "@/features/controlplane/AdminUsers";
import { USER_SEGMENTS } from "@/lib/api/adminConsole";
import { LoadingBlock } from "@/features/controlplane/kit";

/** Suspense: the page reads a search from the address (the console's search box). */
export default function Page() {
  return (
    <Suspense fallback={<LoadingBlock what="users" />}>
      <UsersFromAddress />
    </Suspense>
  );
}

function UsersFromAddress() {
  const params = useSearchParams();
  const q = (params.get("q") ?? "").trim().slice(0, 80);
  // A segment named in the address (a link from Subscriptions to "Trial"); an
  // unknown one is ignored.
  const raw = params.get("segment") ?? "";
  const segment = USER_SEGMENTS.some((s) => s.key === raw) ? raw : "";
  // A new search from the box replaces the page's own state.
  return <Users key={`${q}|${segment}`} initialQuery={q} initialSegment={segment} />;
}
