"use client";

import { useEffect, useState } from "react";
import { getSession } from "@/lib/api/auth";
import type { SessionState } from "@/lib/api/types";

export type SessionQuery =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; session: SessionState };

/**
 * The one place the shell/login/root route ask "is anyone signed in". Also
 * the call that guarantees the CSRF cookie exists (auth_views module
 * docstring) - every mounted page eventually calls this, directly or
 * through the shell layout that wraps it.
 */
export function useSession(): SessionQuery {
  const [state, setState] = useState<SessionQuery>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    getSession()
      .then((session) => {
        if (!cancelled) setState({ status: "ready", session });
      })
      .catch(() => {
        if (!cancelled) setState({ status: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}
