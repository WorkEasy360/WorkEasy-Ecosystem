"use client";

import { useEffect, useId, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Icon } from "@/components/sidebar/icons";
import { publicApi, type PublicStorePage } from "@/lib/api/public";
import { describeError } from "@/features/controlplane/ui";
import { ProductCard } from "./ProductCard";
import { storePaths } from "./links";
import styles from "./StorePages.module.css";
import shell from "./Store.module.css";

/**
 * Every product of the Store (`/store/products`): search, Department and Industry
 * filters, availability and sorting - each filtered on the server
 * (`GET public/store`), each in the address so a link opens the same view. A
 * filter group appears only when the catalog has products in it: no empty
 * departments, no industries nobody filled in.
 */

type Filters = { q: string; category: string; bestFor: string; status: string; sort: string };

function filtersFrom(params: URLSearchParams): Filters {
  return {
    q: params.get("q") ?? "",
    category: params.get("category") ?? "",
    bestFor: params.get("best_for") ?? "",
    status: params.get("status") ?? "",
    sort: params.get("sort") ?? "",
  };
}

function query(f: Filters): Record<string, string | undefined> {
  return {
    q: f.q.trim() || undefined,
    category: f.category || undefined,
    best_for: f.bestFor || undefined,
    status: f.status || undefined,
    sort: f.sort || undefined,
  };
}

function address(f: Filters): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query(f))) if (v) params.set(k, v);
  const s = params.toString();
  return s ? `${storePaths.products}?${s}` : storePaths.products;
}

export function StoreCatalogScreen() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const filters = filtersFrom(params);
  const [text, setText] = useState(filters.q);
  const [result, setResult] = useState<{ key: string; page?: PublicStorePage; error?: string } | null>(null);
  const key = JSON.stringify(query(filters));
  const searchId = useId();

  useEffect(() => {
    let live = true;
    publicApi.store(JSON.parse(key) as Record<string, string | undefined>).then(
      (page) => live && setResult({ key, page }),
      (err: unknown) => live && setResult({ key, error: describeError(err) }),
    );
    return () => {
      live = false;
    };
  }, [key]);

  const set = (patch: Partial<Filters>) => router.replace(address({ ...filters, ...patch }), { scroll: false });
  const current = result?.key === key ? result : null;
  const page = current?.page ?? null;
  const categories = (page?.facets.categories ?? []).filter((c) => c.count > 0 || c.value === filters.category);
  const industries = (page?.facets.best_for ?? []).filter((c) => c.count > 0 || c.value === filters.bestFor);
  const statuses = (page?.facets.statuses ?? []).filter((c) => c.count > 0 || c.value === filters.status);
  const filtered = Boolean(filters.q || filters.category || filters.bestFor || filters.status);

  return (
    <div className={shell.container}>
      <nav aria-label="Breadcrumb" className={shell.crumbs}>
        <ol>
          <li>
            <Link href={storePaths.home}>Store</Link>
          </li>
          <li aria-current="page">Products</li>
        </ol>
      </nav>
      <header className={shell.pageHead}>
        <h1 className={shell.pageTitle}>Products</h1>
        <p className={shell.pageLead}>Every WorkEasy360 product on sale, with its plans and prices. Prices are before GST, which the checkout adds where it applies.</p>
      </header>
      <div className={styles.catalog}>
        <aside className={`${shell.card} ${styles.filters}`} aria-label="Filters">
          <form
            role="search"
            className={styles.filterSearch}
            onSubmit={(e) => {
              e.preventDefault();
              set({ q: text });
            }}
          >
            <label htmlFor={searchId} className={styles.filterLabel}>
              Search
            </label>
            <div className={styles.filterSearchRow}>
              <input
                id={searchId}
                type="search"
                className="w360-input"
                value={text}
                maxLength={100}
                placeholder="Product, feature..."
                onChange={(e) => setText(e.target.value)}
              />
              <button type="submit" className={shell.primary} aria-label="Search">
                <Icon name="search" size={18} />
              </button>
            </div>
          </form>
          {categories.length > 0 && (
            <fieldset className={styles.filterGroup}>
              <legend className={styles.filterLabel}>Department</legend>
              <label className={styles.filterOption}>
                <input type="radio" name="category" checked={!filters.category} onChange={() => set({ category: "" })} />
                All departments
              </label>
              {categories.map((c) => (
                <label key={c.value} className={styles.filterOption}>
                  <input type="radio" name="category" checked={filters.category === c.value} onChange={() => set({ category: c.value })} />
                  {c.label} <span className={styles.count}>{c.count}</span>
                </label>
              ))}
            </fieldset>
          )}
          {industries.length > 0 && (
            <fieldset className={styles.filterGroup}>
              <legend className={styles.filterLabel}>Industry</legend>
              <label className={styles.filterOption}>
                <input type="radio" name="best_for" checked={!filters.bestFor} onChange={() => set({ bestFor: "" })} />
                All industries
              </label>
              {industries.map((c) => (
                <label key={c.value} className={styles.filterOption}>
                  <input type="radio" name="best_for" checked={filters.bestFor === c.value} onChange={() => set({ bestFor: c.value })} />
                  {c.label} <span className={styles.count}>{c.count}</span>
                </label>
              ))}
            </fieldset>
          )}
          {statuses.length > 1 && (
            <fieldset className={styles.filterGroup}>
              <legend className={styles.filterLabel}>Availability</legend>
              <label className={styles.filterOption}>
                <input type="radio" name="status" checked={!filters.status} onChange={() => set({ status: "" })} />
                All
              </label>
              {statuses.map((c) => (
                <label key={c.value} className={styles.filterOption}>
                  <input type="radio" name="status" checked={filters.status === c.value} onChange={() => set({ status: c.value })} />
                  {c.label} <span className={styles.count}>{c.count}</span>
                </label>
              ))}
            </fieldset>
          )}
          {filtered && (
            <button
              type="button"
              className={shell.secondary}
              onClick={() => {
                setText("");
                router.replace(pathname ?? storePaths.products, { scroll: false });
              }}
            >
              Clear filters
            </button>
          )}
        </aside>
        <section aria-labelledby="results-title" className={styles.results}>
          <div className={styles.resultsHead}>
            <h2 id="results-title" className={styles.resultsCount} aria-live="polite">
              {page ? `${page.count} product${page.count === 1 ? "" : "s"}` : "Loading products…"}
            </h2>
            {page && page.sorts.length > 0 && (
              <label className={styles.sort}>
                <span>Sort by</span>
                <select className="w360-input" value={filters.sort || page.sorts[0].value} onChange={(e) => set({ sort: e.target.value })}>
                  {page.sorts.map((s) => (
                    <option key={s.value} value={s.value}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
          {current?.error ? (
            <p role="alert" className={`${shell.card} ${shell.empty}`}>
              {current.error}
            </p>
          ) : !page ? (
            <ul className={styles.productGrid} aria-busy="true">
              {[0, 1, 2].map((i) => (
                <li key={i} className={`${shell.card} ${styles.skeleton}`} />
              ))}
            </ul>
          ) : page.products.length === 0 ? (
            <p className={`${shell.card} ${shell.empty}`}>
              {filtered ? "No product matches these filters." : "No products are on sale yet. Please check back soon."}
            </p>
          ) : (
            <ul className={`${styles.productGrid} ${styles.productGridThree}`}>
              {page.products.map((card) => (
                <ProductCard key={card.code} card={card} />
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
