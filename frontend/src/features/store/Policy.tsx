"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { storeHostApi, type HelpArticle } from "@/lib/api/storeHost";
import { useSite } from "./StoreShell";
import { POLICIES, policyLabel, storePaths } from "./links";
import styles from "./StorePages.module.css";
import shell from "./Store.module.css";

/**
 * One of the Store's policies (ADR-065): the text the Super Admin wrote and
 * published (Help content, kind "policy"), shown exactly as written - plain text.
 * Until it is published the page says so and how to reach us; the Store never
 * shows a policy of its own making.
 */
export function PolicyScreen({ policyKey }: { policyKey: string }) {
  const site = useSite();
  const label = policyLabel(policyKey);
  const [article, setArticle] = useState<HelpArticle | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    storeHostApi.policies().then(
      (r) => live && setArticle(r.results.find((a) => a.slug === policyKey) ?? null),
      () => live && setArticle(null),
    );
    return () => {
      live = false;
    };
  }, [policyKey]);
  useEffect(() => {
    if (label) document.title = `${label} - WorkEasy360 Store`;
  }, [label]);

  if (!label) {
    return (
      <div className={shell.container}>
        <p role="alert" className={`${shell.card} ${shell.empty}`}>
          There is no such policy. <Link href={`${storePaths.help}#policies`}>See all policies</Link>.
        </p>
      </div>
    );
  }
  return (
    <div className={shell.container}>
      <nav aria-label="Breadcrumb" className={shell.crumbs}>
        <ol>
          <li>
            <Link href={storePaths.home}>Store</Link>
          </li>
          <li>
            <Link href={`${storePaths.help}#policies`}>Policies</Link>
          </li>
          <li aria-current="page">{label}</li>
        </ol>
      </nav>
      <div className={styles.policyLayout}>
        <article className={`${shell.card} ${styles.policy}`} aria-labelledby="policy-title">
          <h1 id="policy-title" className={shell.pageTitle}>
            {article?.title || label}
          </h1>
          {article === undefined ? (
            <p aria-live="polite">Loading…</p>
          ) : article ? (
            <div className={shell.prose}>{article.body}</div>
          ) : (
            <div className={shell.notice}>
              <p>
                The {label} has not been published yet.
                {site?.support_email ? (
                  <>
                    {" "}
                    For a copy, write to <a href={`mailto:${site.support_email}`}>{site.support_email}</a>.
                  </>
                ) : null}
              </p>
            </div>
          )}
        </article>
        <nav aria-label="Policies" className={`${shell.card} ${styles.policyNav}`}>
          <h2 className={styles.boxLabel}>All policies</h2>
          <ul className={styles.policyList}>
            {POLICIES.map((p) => (
              <li key={p.key}>
                <Link href={storePaths.policy(p.key)} aria-current={p.key === policyKey ? "page" : undefined}>
                  {p.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </div>
  );
}
