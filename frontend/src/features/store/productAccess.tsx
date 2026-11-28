"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { ApiError } from "@/lib/api/client";
import { launchProduct } from "@/lib/api/launcher";
import { productAccessApi, type ProductAccess } from "@/lib/api/productAccess";
import { describeError } from "@/features/controlplane/ui";
import { launchDenialMessage } from "@/features/launcher/launchCopy";
import { useStoreAccount } from "./account";
import { trialHref } from "./checkoutLinks";
import { storePaths } from "./links";
import shell from "./Store.module.css";

/**
 * What the signed-in customer can do with each product (2026-11-28, automatic
 * free trials): one primary action per product, decided by the server -
 *
 *   Start Free Trial · Open Product · Buy Plan · Upgrade Plan · Setting Up ·
 *   Retry Setup
 *
 * Start Free Trial does everything in one click: it creates the customer's
 * workspace when they have none (they become its owner), starts the trial, and
 * the server provisions the product and seats the starter before it answers.
 * "Open Product" appears only when the server's launch decision says the
 * product really opens; Open asks the launch endpoint again before leaving.
 */

interface AccessState {
  /** `idle` signed out or no organization yet; `loading`; `ready`; `error`. */
  status: "idle" | "loading" | "ready" | "error";
  slug: string | null;
  products: Record<string, ProductAccess>;
  refresh: () => void;
  put: (code: string, access: ProductAccess) => void;
}

const AccessContext = createContext<AccessState | null>(null);

/** How often, and how many times, "Setting Up" asks the server again. */
const SETTING_UP_POLL_MS = 4000;
const SETTING_UP_POLLS = 8;

export function ProductAccessProvider({ children }: { children: ReactNode }) {
  const account = useStoreAccount();
  const slug = account.status === "signed_in" ? (account.organization?.slug ?? null) : null;
  const [tick, setTick] = useState(0);
  const [state, setState] = useState<{ slug: string | null; status: AccessState["status"]; products: Record<string, ProductAccess> }>({
    slug: null,
    status: "idle",
    products: {},
  });

  useEffect(() => {
    if (!slug) return;
    let live = true;
    productAccessApi.list(slug).then(
      (data) => live && setState({ slug, status: "ready", products: data.products }),
      () => live && setState((s) => ({ ...s, slug, status: "error" })),
    );
    return () => {
      live = false;
    };
  }, [slug, tick]);

  const refresh = useCallback(() => setTick((n) => n + 1), []);
  const put = useCallback(
    (code: string, access: ProductAccess) => setState((s) => ({ ...s, products: { ...s.products, [code]: access } })),
    [],
  );
  const value = useMemo<AccessState>(
    () => ({
      // A different organization than the one read last: still loading.
      status: !slug ? "idle" : state.slug === slug ? state.status : "loading",
      slug,
      products: slug && state.slug === slug ? state.products : {},
      refresh,
      put,
    }),
    [slug, state, refresh, put],
  );
  return <AccessContext.Provider value={value}>{children}</AccessContext.Provider>;
}

/** Outside a provider (a test rendering one piece): nothing known. */
export function useProductAccess(): AccessState {
  return (
    useContext(AccessContext) ?? {
      status: "idle",
      slug: null,
      products: {},
      refresh: () => undefined,
      put: () => undefined,
    }
  );
}

function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** The sentence under a button that cannot be pressed (or just failed). */
export function accessNote(access: ProductAccess | undefined): string | null {
  if (!access) return null;
  switch (access.action) {
    case "setting_up":
      return "We are setting up your access. This takes a few seconds.";
    case "retry_setup":
      return access.reason === "seat_missing"
        ? "One last step: your user seat. Choose Retry Setup to finish."
        : "Setup did not finish. Choose Retry Setup - your trial is safe.";
    case "ask_owner":
      if (access.reason === "owner_starts_trials") return "Only your organization's owner can start a trial. Ask them to start it.";
      if (access.reason === "seats_full") return "All user seats are in use. Your organization's owner can free one or add seats.";
      return "Ask your organization's owner to give you a seat for this product.";
    case "unavailable":
      if (access.reason === "temporarily_unavailable") return "This product is temporarily unavailable. Please try again shortly.";
      if (access.reason === "coming_soon") return null;
      return "This product cannot be opened yet.";
    default:
      return null;
  }
}

export interface AccessActionsProps {
  code: string;
  title: string;
  /** The product's Store page slug (not the organization's). */
  productSlug: string;
  /** The product's trial plan and length, from the public catalog - used for a
   * visitor who is signed out (the server's answer wins once signed in). */
  trialPlan?: string | null;
  trialDays?: number | null;
  /** Where Buy Plan / Upgrade Plan go (the product page's plans, or the page's own
   * buy box when it is already showing). */
  buyHref?: string;
  /** Whether to show Buy/Upgrade here at all - the product page has its own buy
   * box, so it shows only the access actions. */
  showBuy?: boolean;
  compact?: boolean;
}

/**
 * The primary action for one product. Signed out: Start Free Trial (to the
 * checkout, which signs in or creates the account first) or nothing (the
 * caller's own Buy Now stays). Signed in: the server's action.
 */
export function AccessActions({ code, title, productSlug, trialPlan, trialDays, buyHref, showBuy = true, compact = false }: AccessActionsProps) {
  const account = useStoreAccount();
  const access = useProductAccess();
  const [busy, setBusy] = useState<"" | "start" | "open" | "repair">("");
  const [problem, setProblem] = useState<string | null>(null);
  const polls = useRef(0);
  // The server's answer to this button's own command, until the shared state
  // has caught up (a new workspace is still being read back, for instance).
  const [own, setOwn] = useState<ProductAccess | null>(null);
  const current = access.products[code] ?? own ?? undefined;
  const days = current?.trial_days ?? trialDays;
  const trialLabel = `Start ${days ? `${days}-day ` : ""}Free Trial`;
  const buyTo = buyHref ?? `${storePaths.product(productSlug)}#plans`;

  // "Setting Up": ask again a few times, then the server's answer stands.
  useEffect(() => {
    if (current?.action !== "setting_up" || polls.current >= SETTING_UP_POLLS) return;
    const timer = setTimeout(() => {
      polls.current += 1;
      access.refresh();
    }, SETTING_UP_POLL_MS);
    return () => clearTimeout(timer);
  }, [current, access]);

  if (account.status === "loading" || (account.status === "signed_in" && account.organizations.length > 0 && access.status === "loading")) {
    return (
      <button type="button" className={shell.primary} disabled aria-busy="true">
        Checking access…
      </button>
    );
  }

  // Signed out: the checkout's trial route signs in or creates the account, then
  // activates. A visitor never sees Open Product - nothing is known about them.
  if (account.status === "signed_out") {
    if (!trialPlan) return null;
    return (
      <Link href={trialHref(code, trialPlan)} className={shell.primary} aria-label={`${trialLabel}: ${title}`}>
        {trialLabel}
      </Link>
    );
  }

  async function start() {
    setBusy("start");
    setProblem(null);
    try {
      let orgSlug = access.slug;
      if (!orgSlug) {
        // No organization yet: one is created for this person, as its owner.
        const made = await productAccessApi.ensureWorkspace(newKey());
        orgSlug = made.slug;
        account.chooseOrganization(made.slug);
        account.refresh();
      }
      const result = await productAccessApi.startTrial(orgSlug, code, newKey(), current?.trial_plan_code ?? trialPlan ?? undefined);
      polls.current = 0;
      setOwn(result.access);
      access.put(code, result.access);
    } catch (err) {
      setProblem(trialProblem(err));
      access.refresh();
    } finally {
      setBusy("");
    }
  }

  async function open() {
    if (!access.slug) return;
    setBusy("open");
    setProblem(null);
    try {
      const { redirect_url: redirectUrl } = await launchProduct(access.slug, code);
      window.location.assign(redirectUrl);
    } catch (err) {
      const body = err instanceof ApiError && typeof err.body === "object" && err.body !== null ? (err.body as { state?: string; code?: string }) : undefined;
      setProblem(launchDenialMessage(body?.state ?? body?.code));
      setBusy("");
      access.refresh();
    }
  }

  async function repair() {
    if (!access.slug) return;
    setBusy("repair");
    setProblem(null);
    try {
      const result = await productAccessApi.repair(access.slug, code);
      polls.current = 0;
      setOwn(result.access);
      access.put(code, result.access);
    } catch (err) {
      setProblem(err instanceof ApiError ? describeError(err) : "Setup could not be retried right now. Try again in a minute.");
    } finally {
      setBusy("");
    }
  }

  // Signed in without an organization: the trial creates one; buying shows its
  // own organization step in the checkout.
  // The access read failed: no claim either way - the plans stay one click away.
  const action = current?.action ?? (access.slug ? (access.status === "error" ? "buy" : null) : trialPlan ? "start_trial" : null);
  const note = problem ?? accessNote(current);
  const button = (() => {
    switch (action) {
      case "open":
        return (
          <button type="button" className={shell.primary} onClick={open} disabled={busy !== ""} aria-label={`Open Product: ${title}`}>
            {busy === "open" ? "Opening…" : "Open Product"}
          </button>
        );
      case "start_trial":
        return (
          <button type="button" className={shell.primary} onClick={start} disabled={busy !== ""} aria-busy={busy === "start"} aria-label={`${trialLabel}: ${title}`}>
            {busy === "start" ? "Setting Up…" : trialLabel}
          </button>
        );
      case "setting_up":
        return (
          <button type="button" className={shell.primary} disabled aria-busy="true">
            Setting Up…
          </button>
        );
      case "retry_setup":
        return (
          <button type="button" className={shell.primary} onClick={repair} disabled={busy !== ""} aria-busy={busy === "repair"} aria-label={`Retry Setup: ${title}`}>
            {busy === "repair" ? "Setting Up…" : "Retry Setup"}
          </button>
        );
      case "upgrade":
        return showBuy ? (
          <Link href={buyTo} className={shell.primary} aria-label={`Upgrade Plan: ${title}`}>
            Upgrade Plan
          </Link>
        ) : null;
      case "buy":
        return showBuy ? (
          <Link href={buyTo} className={shell.primary} aria-label={`Buy Plan: ${title}`}>
            Buy Plan
          </Link>
        ) : null;
      case "contact_sales":
        return showBuy ? (
          <Link href={`${storePaths.help}#contact`} className={shell.secondary}>
            Contact sales
          </Link>
        ) : null;
      case "unavailable":
        return current?.reason === "coming_soon" ? null : (
          <button type="button" className={shell.primary} disabled>
            {current?.reason === "temporarily_unavailable" ? "Temporarily Unavailable" : "Not Available Yet"}
          </button>
        );
      default:
        return null;
    }
  })();
  const upgrade =
    action === "open" && current?.upgrade_available && showBuy ? (
      <Link href={buyTo} className={shell.secondary} aria-label={`Upgrade Plan: ${title}`}>
        Upgrade Plan
      </Link>
    ) : null;

  if (!button && !upgrade && !note) return null;
  // Several organizations: say which one this answer is for (the Store's chosen one).
  const forOrganization = !compact && account.organizations.length > 1 ? account.organization?.display_name : null;
  return (
    <>
      {forOrganization && <p className={shell.accessNote}>For {forOrganization}</p>}
      {button}
      {upgrade}
      {action === "open" && current?.holding === "trial" && current.trial_days_left !== null && !compact && (
        <p className={shell.accessNote}>
          Free trial · {current.trial_days_left} {current.trial_days_left === 1 ? "day" : "days"} left
        </p>
      )}
      {note && (
        <p role={problem ? "alert" : "status"} className={shell.accessNote}>
          {note}
        </p>
      )}
    </>
  );
}

/** Why a trial did not start, in words. */
export function trialProblem(err: unknown): string {
  if (err instanceof ApiError) {
    const body = (err.body ?? {}) as { reason?: string; code?: string; available_from?: string };
    switch (body.reason) {
      case "already_used":
        return "Your organization has already used its free trial of this product. Choose a plan to continue.";
      case "already_used_elsewhere":
        return "You have already had a free trial of this product in another of your organizations. Choose a plan to continue.";
      case "repeat_too_soon": {
        const when = body.available_from ? new Date(body.available_from) : null;
        return when && !Number.isNaN(when.getTime())
          ? `Another free trial of this product opens on ${when.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}. You can choose a plan now.`
          : "Another free trial of this product is not open yet. You can choose a plan now.";
      }
      case "not_eligible":
        return "This product's free trial is for new customers. Choose a plan to continue.";
      case "product_not_ready":
      case "product_unavailable":
        return "This product cannot be opened yet, so its trial cannot start. Please try again later.";
      case "not_offered":
      case "plan_not_eligible":
        return "This product does not offer a free trial right now.";
      case "organization_required":
        return "Choose the organization to use first.";
    }
    if (err.status === 403) return "Only your organization's owner can start a trial. Ask them to start it.";
    return describeError(err);
  }
  return "The trial could not be started. Please try again.";
}
