import type { CatalogOverview, CatalogStatus, PriceBookRow } from "@/lib/api/adminConsole";

/**
 * The plan builder's vocabulary over the catalog's lifecycle (Phase 8
 * extension). The catalog has a status and a separate "listed publicly" flag;
 * the builder presents the combinations an administrator actually chooses:
 *
 * - Draft       = status draft (not on sale)
 * - Private     = available, not listed publicly (sold by link or by staff)
 * - Public      = available and listed
 * - Deactivated = grandfathered (existing customers keep it; no new sales)
 * - Retired     = retired (shown, never chosen here)
 */

export type BuilderKind = "bundle" | "package" | "addon";
export type UiStatus = "draft" | "private" | "public" | "deactivated";

export const NOUN: Record<BuilderKind, string> = {
  bundle: "plan",
  package: "product package",
  addon: "add-on",
};

export const STATUS_LABEL: Record<UiStatus | "retired", string> = {
  draft: "Draft",
  private: "Private",
  public: "Public",
  deactivated: "Deactivated",
  retired: "Retired",
};

export const STATUS_BADGE: Record<UiStatus | "retired", string> = {
  draft: "w360-badge w360-badge-neutral",
  private: "w360-badge w360-badge-neutral",
  public: "w360-badge w360-badge-success",
  deactivated: "w360-badge w360-badge-warning",
  retired: "w360-badge w360-badge-danger",
};

export function uiStatusOf(row: { status: CatalogStatus; is_public: boolean }): UiStatus | "retired" {
  switch (row.status) {
    case "draft":
      return "draft";
    case "available":
      return row.is_public ? "public" : "private";
    case "grandfathered":
      return "deactivated";
    default:
      return "retired";
  }
}

/** The catalog status a choice means. */
export function catalogStatusOf(choice: UiStatus): CatalogStatus {
  if (choice === "draft") return "draft";
  if (choice === "deactivated") return "grandfathered";
  return "available";
}

/** The status changes, in order, that take ``current`` to ``choice``. The
 * catalog only moves draft → available → grandfathered (and back to
 * available), so deactivating a draft passes through "available". */
export function statusPath(current: CatalogStatus, choice: UiStatus): CatalogStatus[] {
  const target = catalogStatusOf(choice);
  if (current === target) return [];
  if (current === "draft" && target === "grandfathered") return ["available", "grandfathered"];
  return [target];
}

export const DEFAULT_INTERVALS = ["monthly", "quarterly", "semi_annual", "annual"];

export function builderIntervals(overview: CatalogOverview): string[] {
  return overview.intervals?.length ? overview.intervals : DEFAULT_INTERVALS;
}

/** Books the builder publishes list prices into: the direct channel. */
export function directBooks(overview: CatalogOverview): PriceBookRow[] {
  return overview.price_books.filter((b) => b.channel === "direct");
}

export function defaultBook(overview: CatalogOverview): PriceBookRow | undefined {
  const books = directBooks(overview);
  return books.find((b) => b.is_active) ?? books[0];
}

export const CODE_PATTERN = /^[a-z0-9][a-z0-9_-]{1,62}$/;

/** A product's published package versions a plan (bundle) can include: a
 * standard (not add-on), not-retired plan's published versions that carry a
 * price for every chosen billing cycle. ``keep`` stays listed regardless, so
 * a current choice never silently disappears from its select. */
export function packageVersions(
  overview: CatalogOverview,
  productId: string,
  cycles: string[],
  keep?: string,
): { id: string; label: string }[] {
  const out: { id: string; label: string }[] = [];
  for (const plan of overview.plans) {
    if (plan.product.id !== productId || plan.kind !== "standard" || plan.status === "retired") continue;
    for (const version of plan.versions) {
      if (!version.published_at) continue;
      const covers = cycles.every((c) => version.prices.some((p) => p.interval === c));
      if (covers || version.id === keep) {
        out.push({
          id: version.id,
          label: `${plan.name} · v${version.version}${version.effective_to ? " (superseded)" : ""}`,
        });
      }
    }
  }
  return out;
}
