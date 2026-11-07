"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Users } from "@/features/controlplane/AdminUsers";
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
  const q = (useSearchParams().get("q") ?? "").trim().slice(0, 80);
  // A new search from the box replaces the page's own state.
  return <Users key={q} initialQuery={q} />;
}
