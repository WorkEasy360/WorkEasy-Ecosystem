"use client";

import { useId, useMemo, useState } from "react";
import Link from "next/link";
import { apiFetch } from "@/lib/api/client";
import { supportApi } from "@/lib/api/adminPanel";
import type { HelpArticle } from "@/lib/api/storeHost";
import { usePortals } from "@/lib/portalContext";
import { portalHref } from "@/lib/portals";
import { DialogHead, Modal } from "./Modal";
import { useLoad } from "./ui";
import { EmptyState, LoadingBlock, SearchInput } from "./kit";
import styles from "./HelpDrawer.module.css";

/**
 * Help, beside the page (2026-11-18, admin-ux phase 1): the published help articles,
 * read-only and searchable, in a drawer on the right. Staff who may read the help
 * library (platform.staff_support.view) get every published article from it; everyone
 * else gets what the platform publishes for all readers. Editing stays in Support ›
 * Help articles. Article text is plain text, shown with its line breaks kept.
 */

const SUPPORT_VIEW = "platform.staff_support.view";

const KIND: Record<string, string> = {
  faq: "FAQ",
  setup_guide: "Setup guide",
  sales_guide: "Sales guide",
  policy: "Policy",
};

interface Article {
  key: string;
  title: string;
  body: string;
  kind: string;
}

function useArticles(library: boolean) {
  return useLoad<Article[]>(
    () =>
      library
        ? supportApi
            .articles({ status: "published" })
            .then((r) => r.results.map((a) => ({ key: a.id, title: a.title, body: a.body, kind: a.kind })))
        : apiFetch<{ results: HelpArticle[] }>("/api/v1/public/help").then((r) =>
            r.results.map((a) => ({ key: a.slug, title: a.title, body: a.body, kind: a.kind })),
          ),
    [library],
  );
}

export function HelpDrawer({ permissions, onClose }: { permissions: string[]; onClose: () => void }) {
  const titleId = useId();
  const library = permissions.includes(SUPPORT_VIEW);
  const portals = usePortals();
  const [load] = useArticles(library);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const all = useMemo(() => (load.status === "ready" ? load.data : []), [load]);
  const needle = query.trim().toLowerCase();
  const shown = needle ? all.filter((a) => `${a.title}\n${a.body}`.toLowerCase().includes(needle)) : all;
  const contact = portalHref(portals, "app", "/contact");

  return (
    <Modal labelledBy={titleId} onClose={onClose} size="drawer">
      <DialogHead titleId={titleId} title="Help" onClose={onClose} />
      <div className={styles.body}>
        <SearchInput label="Search help articles" value={query} onSearch={setQuery} />
        {load.status === "loading" ? (
          <LoadingBlock what="help articles" />
        ) : load.status === "ready" ? (
          shown.length === 0 ? (
            <EmptyState
              icon="book"
              compact
              title={needle ? `No help articles match “${query.trim()}”.` : "No help articles are published yet."}
              hint={needle ? "Try other words." : undefined}
            />
          ) : (
            <ul className={styles.list} aria-label="Help articles">
              {shown.map((a) => {
                const expanded = open === a.key;
                const bodyId = `${titleId}-${a.key}`;
                return (
                  <li key={a.key} className={styles.item}>
                    <button
                      type="button"
                      className={styles.title}
                      aria-expanded={expanded}
                      aria-controls={bodyId}
                      onClick={() => setOpen(expanded ? null : a.key)}
                    >
                      <span>{a.title}</span>
                      <span className={styles.kind}>{KIND[a.kind] ?? a.kind}</span>
                    </button>
                    {expanded && (
                      <p id={bodyId} className={styles.text}>
                        {a.body}
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>
          )
        ) : (
          <p role="alert" className={styles.note}>
            Help articles could not be loaded right now.
          </p>
        )}
      </div>
      <div className={styles.foot}>
        {library && (
          <Link href="/admin/help-content" className={styles.footLink} onClick={onClose}>
            Manage help articles
          </Link>
        )}
        {/^https?:/.test(contact) ? (
          <a href={contact} className={styles.footLink}>
            Contact support
          </a>
        ) : (
          <Link href={contact} className={styles.footLink} onClick={onClose}>
            Contact support
          </Link>
        )}
      </div>
    </Modal>
  );
}
