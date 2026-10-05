"use client";

import { createContext, useContext } from "react";
import type { ConsoleAccess } from "@/lib/api/console";

/**
 * Who the signed-in member is in this organization, and what they may do -
 * loaded once by the shell (`GET /orgs/<slug>/access`) and shared with every
 * page below it. **Navigation only**: a page hides what the member cannot use,
 * and the backend refuses it anyway.
 */
export const OrgAccessContext = createContext<{
  slug: string;
  access: ConsoleAccess | null;
  reload: () => void;
}>({ slug: "", access: null, reload: () => undefined });

export function useOrgAccess() {
  const value = useContext(OrgAccessContext);
  const permissions = value.access?.permissions ?? [];
  return {
    ...value,
    can: (permission: string) => permissions.includes(permission),
  };
}
