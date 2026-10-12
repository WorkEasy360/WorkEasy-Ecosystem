"use client";

import { createContext, useContext } from "react";
import type { PortalOrigins } from "./portals";

/**
 * The portal origins, read on the server from runtime configuration by the
 * root layout and handed down here (ADR-043). `null` is single-host mode.
 *
 * Only for building links to another portal's pages and for the central
 * sign-in's hand-off: nothing here is a permission, and every page still asks
 * the server what the signed-in person may do.
 */
const PortalsContext = createContext<PortalOrigins | null>(null);

export function PortalsProvider({ origins, children }: { origins: PortalOrigins | null; children: React.ReactNode }) {
  return <PortalsContext.Provider value={origins}>{children}</PortalsContext.Provider>;
}

export function usePortals(): PortalOrigins | null {
  return useContext(PortalsContext);
}
