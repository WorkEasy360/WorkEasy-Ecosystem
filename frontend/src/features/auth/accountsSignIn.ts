import { issueHandoff } from "@/lib/api/auth";
import { isState } from "@/lib/portals";

/**
 * ADR-071: where a sign-in on WorkEasy360 Accounts goes next.
 *
 * - The customer portal sent the person here to sign in (`mode=user` with its
 *   binding value `state`): hand the sign-in to it - a one-time code in the
 *   URL fragment of its `/auth/continue` (ADR-043), redeemed there for its own
 *   host-only session. The page it opens is decided by the server from `next`.
 * - Otherwise this was a sign-in to Accounts itself: its own account page, or
 *   the Accounts page `next` names.
 */
export function accountsPage(raw: string | null | undefined): string {
  // Only Accounts' own account pages: a plain path, never a URL, a query or
  // anything that could leave this host.
  return raw && /^\/account(\/[a-z0-9-]+)*\/?$/.test(raw) ? raw : "/account";
}

export function handsOffToApp(params: URLSearchParams): boolean {
  const mode = params.get("mode");
  return isState(params.get("state")) && (mode === null || mode === "user");
}

export async function continueFromAccounts(params: URLSearchParams, go: (url: string) => void = (u) => window.location.replace(u)): Promise<void> {
  if (handsOffToApp(params)) {
    try {
      const { redirect_url } = await issueHandoff("user", params.get("next"), params.get("state") as string);
      go(redirect_url);
      return;
    } catch {
      // The binding value was refused (expired, malformed): the account page;
      // the customer portal starts a fresh sign-in the next time it is opened.
    }
  }
  go(accountsPage(params.get("next")));
}
