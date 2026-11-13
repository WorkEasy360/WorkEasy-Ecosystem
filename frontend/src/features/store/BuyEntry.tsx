"use client";

import { useEffect, useMemo } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { publicApi } from "@/lib/api/public";
import { REFERRAL_TOKEN } from "@/features/auth/SelfService";
import { INTERVALS, checkoutHref, trialHref } from "./checkoutLinks";
import { useShellCatalog } from "./StoreShell";
import { storePaths } from "./links";
import shell from "./Store.module.css";

/**
 * Where a product's own site sends a buyer (ADR-065):
 *
 *   https://store.workeasy360.com/store/buy?product=easy_crm&plan=crm_professional
 *     &interval=annual[&quantity=5][&trial=1][&ref=CODE][&return_to=https://crm...]
 *
 * Codes, never prices or ids: the product and plan are looked up in the public
 * catalog (so a plan that is not on sale opens nothing), a referral code is
 * recorded on this host where sign-up and checkout will read it, and the return
 * address goes to the checkout, which shows it only once the server approves it
 * for that product. Mobile apps send buyers here too - purchases are made on the
 * web.
 */

const CODE = /^[a-z][a-z0-9_]{0,63}$/;
const REF = /^[A-Za-z0-9]{4,16}$/;

type Decision = { go?: string; problem?: string } | null;

/** Where the link leads: the checkout, the product's page, or why neither. */
function decide(catalog: ReturnType<typeof useShellCatalog>, params: URLSearchParams): Decision {
  if (!catalog) return null;
  const productCode = params.get("product") ?? "";
  const planCode = params.get("plan") ?? "";
  if (!CODE.test(productCode)) return { problem: "This link does not name a product." };
  const card = catalog.products.find((p) => p.code === productCode);
  if (!card) return { problem: "That product is not in the Store." };
  if (card.status !== "available") return { go: storePaths.product(card.slug) };
  const returnUrl = params.get("return_to") ?? "";
  const returnTo = /^https:\/\//.test(returnUrl) ? { product: card.code, url: returnUrl } : undefined;
  if (params.get("trial") === "1" && card.trial?.available) {
    const plan =
      card.plans.find((p) => p.plan_code === planCode && card.trial.plan_codes.includes(p.plan_code)) ??
      card.plans.find((p) => card.trial.plan_codes.includes(p.plan_code));
    if (plan) return { go: trialHref(card.code, plan.plan_code) };
  }
  const plan = CODE.test(planCode) ? card.plans.find((p) => p.plan_code === planCode) : undefined;
  if (!plan) return { go: `${storePaths.product(card.slug)}#plans` };
  const asked = params.get("interval") ?? "";
  const offered = plan.prices.filter((x) => x.amount_minor !== null).map((x) => x.interval);
  const interval =
    (INTERVALS as readonly string[]).includes(asked) && offered.includes(asked) ? asked : offered.find((i) => (INTERVALS as readonly string[]).includes(i));
  if (!interval) return { go: `${storePaths.product(card.slug)}#plans` };
  const quantity = Number(params.get("quantity") ?? "1");
  return {
    go: checkoutHref({ plan: plan.plan_version_id, interval, quantity: Number.isInteger(quantity) && quantity > 1 ? quantity : undefined, returnTo }),
  };
}

export function BuyEntryScreen() {
  const params = useSearchParams();
  const router = useRouter();
  const catalog = useShellCatalog();
  const decision = useMemo(() => decide(catalog, params), [catalog, params]);

  useEffect(() => {
    const ref = params.get("ref") ?? "";
    if (!REF.test(ref)) return;
    publicApi.captureReferral(ref).then(
      ({ token }) => {
        try {
          sessionStorage.setItem(REFERRAL_TOKEN, token);
        } catch {
          // The referral cookie this host just set still carries it.
        }
      },
      () => undefined,
    );
  }, [params]);

  useEffect(() => {
    if (decision?.go) router.replace(decision.go);
  }, [decision, router]);

  const problem = decision?.problem ?? null;
  return (
    <div className={shell.container}>
      {problem ? (
        <p role="alert" className={`${shell.card} ${shell.empty}`}>
          {problem} <Link href={storePaths.products}>Browse the Store</Link>.
        </p>
      ) : (
        <p aria-live="polite" className={`${shell.card} ${shell.empty}`}>
          Opening your checkout…
        </p>
      )}
    </div>
  );
}
