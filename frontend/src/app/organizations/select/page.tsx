"use client";

import { Suspense, useCallback, useEffect, useId, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { getOrganizations } from "@/lib/api/auth";
import { useSession } from "@/lib/auth/useSession";
import { StatusScreen } from "@/components/StatusScreen";
import type { OrganizationSummary } from "@/lib/api/types";
import { Invitations } from "@/features/controlplane/Invitations";

type Load =
  | { status: "loading" }
  | { status: "error" }
  | {
      status: "ready";
      organizations: OrganizationSummary[];
      nextCursor: string | null;
      hasMore: boolean;
      loadingMore: boolean;
    };

function Chooser() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const resume = searchParams.get("resume");
  const session = useSession();
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [search, setSearch] = useState("");
  const searchId = useId();

  // Fetches and applies a result - no synchronous setState of its own, so
  // it is safe to call from inside an effect body (react-hooks/set-state-
  // in-effect: an effect may set state from an *async* callback, never
  // synchronously in the effect's own call stack).
  const applyPage = useCallback((query: string) => {
    getOrganizations({ search: query || undefined })
      .then(({ organizations, next_cursor, has_more }) =>
        setLoad({
          status: "ready",
          organizations,
          nextCursor: next_cursor,
          hasMore: has_more,
          loadingMore: false,
        }),
      )
      .catch(() => setLoad({ status: "error" }));
  }, []);

  useEffect(() => {
    if (session.status !== "ready") return;
    if (!session.session.authenticated) {
      router.replace(resume ? `/login?resume=${encodeURIComponent(resume)}` : "/login");
      return;
    }
    applyPage(search);
    // Search is deliberately not a dependency: it is applied only when the
    // user submits (see handleSearchSubmit), not on every keystroke - a
    // bounded, explicit query rather than a request per character (Task 57).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, router, resume, applyPage]);

  function handleSearchSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoad({ status: "loading" });
    applyPage(search);
  }

  async function loadMore() {
    if (load.status !== "ready" || !load.nextCursor) return;
    setLoad({ ...load, loadingMore: true });
    try {
      const page = await getOrganizations({ search: search || undefined, cursor: load.nextCursor });
      setLoad((prev) =>
        prev.status === "ready"
          ? {
              status: "ready",
              organizations: [...prev.organizations, ...page.organizations],
              nextCursor: page.next_cursor,
              hasMore: page.has_more,
              loadingMore: false,
            }
          : prev,
      );
    } catch {
      setLoad((prev) => (prev.status === "ready" ? { ...prev, loadingMore: false } : prev));
    }
  }

  function choose(org: OrganizationSummary) {
    if (resume) {
      // A genuine top-level navigation, not client-side routing (Task 31/
      // 32): this must reach the Django resume endpoint as a real browser
      // request and be free to follow whatever it redirects to next -
      // Next.js's router cannot express that.
      // eslint-disable-next-line react-hooks/immutability, @next/next/no-location-assign-relative-destination
      window.location.href = `/api/v1/integrations/oauth/resume?resume=${encodeURIComponent(
        resume,
      )}&organization_id=${encodeURIComponent(org.organization_id)}`;
    } else {
      router.push(`/o/${org.slug}`);
    }
  }

  if (load.status === "loading" || session.status === "loading") {
    return <StatusScreen>Loading your organizations…</StatusScreen>;
  }
  if (load.status === "error") {
    return (
      <StatusScreen role="alert">
        <div>
          <p>Couldn&apos;t load your organizations.</p>
          <button
            className="w360-btn w360-btn-secondary"
            onClick={() => {
              setLoad({ status: "loading" });
              applyPage(search);
            }}
          >
            Retry
          </button>
        </div>
      </StatusScreen>
    );
  }

  if (load.organizations.length === 0 && !search) {
    return (
      <StatusScreen>
        <div>
          <h1 style={{ fontSize: 18 }}>No organizations yet</h1>
          <p style={{ color: "var(--w360-text-muted)", marginBottom: "var(--w360-space-4)" }}>
            You aren&apos;t a member of any Workeasy360 organization yet.
          </p>
          <Invitations />
          <Link href="/onboarding" className="w360-btn w360-btn-primary">
            Create an organization
          </Link>
        </div>
      </StatusScreen>
    );
  }

  return (
    <main id="w360-main" className="w360-container" style={{ paddingTop: "var(--w360-space-7)" }}>
      <h1 style={{ fontSize: 22 }}>Choose an organization</h1>
      <p style={{ color: "var(--w360-text-muted)", marginBottom: "var(--w360-space-5)" }}>
        {resume
          ? "Pick the organization to continue signing in to the product you opened."
          : "Pick which organization you'd like to work in."}
      </p>
      <Invitations />
      <form
        onSubmit={handleSearchSubmit}
        role="search"
        style={{ marginBottom: "var(--w360-space-5)", display: "flex", gap: "var(--w360-space-2)" }}
      >
        <label className="w360-label" htmlFor={searchId} style={{ position: "absolute", left: -9999 }}>
          Search organizations
        </label>
        <input
          id={searchId}
          className="w360-input"
          type="search"
          placeholder="Search organizations…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ flex: 1 }}
        />
        <button type="submit" className="w360-btn w360-btn-secondary">
          Search
        </button>
      </form>

      {load.organizations.length === 0 ? (
        <p style={{ color: "var(--w360-text-muted)" }}>No organizations match &quot;{search}&quot;.</p>
      ) : (
        <ul
          style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: "var(--w360-space-3)" }}
        >
          {load.organizations.map((org) => (
            <li key={org.organization_id}>
              <button
                className="w360-card"
                onClick={() => choose(org)}
                style={{
                  width: "100%",
                  textAlign: "left",
                  padding: "var(--w360-space-5)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: "var(--w360-space-4)",
                }}
              >
                <span>
                  <span style={{ fontWeight: 600, fontSize: 16 }}>{org.display_name}</span>
                  {org.is_owner && (
                    <span className="w360-badge w360-badge-neutral" style={{ marginLeft: 8 }}>
                      Owner
                    </span>
                  )}
                </span>
                <span aria-hidden="true" style={{ color: "var(--w360-text-faint)" }}>
                  →
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {load.hasMore && (
        <button
          className="w360-btn w360-btn-secondary"
          style={{ marginTop: "var(--w360-space-4)" }}
          onClick={loadMore}
          disabled={load.loadingMore}
        >
          {load.loadingMore ? "Loading…" : "Load more"}
        </button>
      )}

      <p style={{ marginTop: "var(--w360-space-6)" }}>
        <Link href="/onboarding">Create another organization</Link>
      </p>
    </main>
  );
}

export default function OrganizationSelectPage() {
  return (
    <Suspense fallback={<StatusScreen>Loading…</StatusScreen>}>
      <Chooser />
    </Suspense>
  );
}
