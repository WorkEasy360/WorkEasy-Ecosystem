"use client";

import { useParams } from "next/navigation";
import { Workspace } from "@/features/partnerpanel/WorkspaceShell";

/** The affiliate workspace (ADR-047): one catch-all route over the shared partner
 * panel. The server decides whether this account has the workspace and the page;
 * the address only names the page. */
export default function Page() {
  const { slug } = useParams<{ slug?: string[] }>();
  return <Workspace slug="affiliate" segments={slug} />;
}
