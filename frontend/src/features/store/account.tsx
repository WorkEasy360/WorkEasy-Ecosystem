"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { getOrganizations, getSession, logout } from "@/lib/api/auth";
import { forgetAccountState } from "@/lib/accountState";
import type { OrganizationSummary, SessionState } from "@/lib/api/types";
import { usePortals } from "@/lib/portalContext";
import { usePortalRouter } from "@/lib/portalRouter";
import { storeHostApi } from "@/lib/api/storeHost";

/**
 * Who is signed in to the Store, and for which organization (ADR-065).
 *
 * The Store's own host keeps its own session for the same accounts. The
 * organization the buyer works for is chosen here: one membership is chosen by
 * itself; with several, the buyer picks (remembered for this tab only). Nothing
 * here is a permission - every quote, order and payment is authorized again by the
 * server from the buyer's live membership of that organization.
 */

const ORG_KEY = "w360.store-org";

export interface StoreAccount {
  status: "loading" | "signed_out" | "signed_in";
  session: SessionState | null;
  organizations: OrganizationSummary[];
  organization: OrganizationSummary | null;
  chooseOrganization: (slug: string) => void;
  /** Read the session and memberships again (after signing in on the page). */
  refresh: () => void;
  signOut: () => Promise<void>;
}

const AccountContext = createContext<StoreAccount | null>(null);

function remembered(): string | null {
  try {
    return window.sessionStorage.getItem(ORG_KEY);
  } catch {
    return null;
  }
}

function remember(slug: string): void {
  try {
    window.sessionStorage.setItem(ORG_KEY, slug);
  } catch {
    // Storage refused: the choice lasts for this page only.
  }
}

export function StoreAccountProvider({ children }: { children: React.ReactNode }) {
  const [tick, setTick] = useState(0);
  const [state, setState] = useState<{ session: SessionState | null; organizations: OrganizationSummary[]; done: boolean }>({
    session: null,
    organizations: [],
    done: false,
  });
  const [chosen, setChosen] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      let session: SessionState | null = null;
      try {
        session = await getSession();
      } catch {
        session = null;
      }
      let organizations: OrganizationSummary[] = [];
      if (session?.authenticated) {
        try {
          organizations = (await getOrganizations({ limit: 50 })).organizations;
        } catch {
          organizations = [];
        }
      }
      if (live) setState({ session, organizations, done: true });
    })();
    return () => {
      live = false;
    };
  }, [tick]);

  const refresh = useCallback(() => setTick((n) => n + 1), []);
  const value = useMemo<StoreAccount>(() => {
    const { session, organizations, done } = state;
    const wanted = chosen ?? (typeof window === "undefined" ? null : remembered());
    const organization =
      organizations.find((o) => o.slug === wanted) ?? (organizations.length === 1 ? organizations[0] : null) ?? organizations[0] ?? null;
    return {
      status: !done ? "loading" : session?.authenticated ? "signed_in" : "signed_out",
      session,
      organizations,
      organization,
      chooseOrganization: (slug: string) => {
        remember(slug);
        setChosen(slug);
      },
      refresh,
      signOut: async () => {
        try {
          await logout();
          forgetAccountState();
        } finally {
          setChosen(null);
          refresh();
        }
      },
    };
  }, [state, chosen, refresh]);

  return <AccountContext.Provider value={value}>{children}</AccountContext.Provider>;
}

/** The Store's account state. Outside a provider (a test rendering one piece),
 * a signed-out visitor. */
export function useStoreAccount(): StoreAccount {
  return (
    useContext(AccountContext) ?? {
      status: "signed_out",
      session: null,
      organizations: [],
      organization: null,
      chooseOrganization: () => undefined,
      refresh: () => undefined,
      signOut: async () => undefined,
    }
  );
}

/** Where a My Account link leads, and how: on the Store's own host a single-use
 * pass signs the same person in to User Admin (posted in a form body, never a
 * URL); on the customer portal itself (no Store host) a plain link. */
export function useMyAccount() {
  const origins = usePortals();
  const router = usePortalRouter();
  const storeHost = Boolean(origins?.store) && typeof window !== "undefined" && origins?.store === window.location.origin;

  const href = (path: string) => (origins && storeHost ? `${origins.app}${path}` : path);

  async function open(path: string): Promise<void> {
    if (!origins || !storeHost) {
      router.push(path);
      return;
    }
    try {
      const pass = await storeHostApi.accountPass(path);
      // Only ever to User Admin's own address, whatever an answer says.
      if (new URL(pass.action).origin !== origins.app) throw new Error("unexpected pass target");
      const form = document.createElement("form");
      form.method = "post";
      form.action = pass.action;
      form.style.display = "none";
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "code";
      input.value = pass.code;
      form.appendChild(input);
      document.body.appendChild(form);
      form.submit();
    } catch {
      // No pass (signed out here, or refused): User Admin asks for the password itself.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- another origin, absolute
      window.location.assign(`${origins.app}${path}`);
    }
  }

  return { href, open };
}
