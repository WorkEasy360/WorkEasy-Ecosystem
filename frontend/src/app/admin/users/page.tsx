"use client";

import { Suspense } from "react";
import { Users } from "@/features/controlplane/AdminUsers";
import { LoadingBlock } from "@/features/controlplane/kit";

/** Suspense: the list reads its filters from the address (a search from the
 * console's search box, a segment from a link) and writes them back there. */
export default function Page() {
  return (
    <Suspense fallback={<LoadingBlock what="users" />}>
      <Users />
    </Suspense>
  );
}
