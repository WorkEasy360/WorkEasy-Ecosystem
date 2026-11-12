"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Icon } from "@/components/sidebar/icons";
import { storeHostApi, type HelpArticle } from "@/lib/api/storeHost";
import { useMyAccount, useStoreAccount } from "./account";
import { useSite } from "./StoreShell";
import { POLICIES, storePaths } from "./links";
import styles from "./StorePages.module.css";
import shell from "./Store.module.css";

/**
 * Help & Policies (ADR-065): purchase help by topic, the FAQs the Super Admin
 * publishes (Help content), how to reach support - a ticket in the shared support
 * desk from My Account, or the support email - and the footer's policies.
 *
 * The built-in answers describe how the Store and the platform work, and nothing
 * more: no refund window, price, rate or promise of their own. What is the Super
 * Admin's to set (a product's refund window, the policies, the rates) is pointed to
 * where it is published.
 */

type Faq = { q: string; a: string };

const TOPICS: { key: string; title: string; icon: string; faqs: Faq[] }[] = [
  {
    key: "purchase",
    title: "Buying a product",
    icon: "cart",
    faqs: [
      {
        q: "How do I buy a product?",
        a: "Open the product, choose a plan and billing period, and select Buy Now. Sign in or create an account, choose the organization you are buying for, check the order summary and pay. Each order is for one product and one plan.",
      },
      {
        q: "Who in my organization can buy?",
        a: "The organization's owner places orders. The owner and Billing Managers pay invoices and recharge wallets.",
      },
      {
        q: "What does Coming Soon mean?",
        a: "The product is announced but not on sale yet. Its page shows what it will do; you can buy it once it goes on sale.",
      },
    ],
  },
  {
    key: "subscription",
    title: "Subscriptions",
    icon: "layers",
    faqs: [
      {
        q: "When does my subscription start?",
        a: "As soon as the payment provider confirms your payment to us. A message on the screen alone never activates anything - the Store waits for that confirmation.",
      },
      {
        q: "Where do I see my subscriptions?",
        a: "In My Account (User Admin), under Billing & Subscriptions. Your products open from My Products.",
      },
      {
        q: "Do free plans and trials need a card?",
        a: "No. A free plan or a trial activates without a payment. Each product's page says whether it offers either.",
      },
    ],
  },
  {
    key: "payment",
    title: "Payments & GST",
    icon: "card",
    faqs: [
      {
        q: "How can I pay?",
        a: "Online on the payment provider's secure page, from your organization's Platform Wallet, or part from the wallet and the rest online. The checkout shows which online methods are available.",
      },
      {
        q: "How does paying part from the Platform Wallet work?",
        a: "The wallet amount you choose is reserved, not taken, while you pay the rest online. It is taken only when the online payment is confirmed; if that payment fails, expires or is cancelled, the reservation is released.",
      },
      {
        q: "Is GST added?",
        a: "Prices in Indian rupees are shown before GST. The checkout adds GST at the rate in force and shows it as its own line before you pay, and it appears on your invoice.",
      },
      {
        q: "What if I am charged twice?",
        a: "A retried payment continues the same payment rather than starting a new one. Money that arrives for an order that was already paid or withdrawn is refunded automatically.",
      },
    ],
  },
  {
    key: "refund",
    title: "Refunds",
    icon: "refresh",
    faqs: [
      {
        q: "Can I get a refund?",
        a: "Each product's page shows its refund window where one is offered. Ask for a refund from My Account, under the order, and our team decides under the Refund & Cancellation Policy.",
      },
    ],
  },
  {
    key: "setup",
    title: "After you buy",
    icon: "settings",
    faqs: [
      {
        q: "What happens after I pay?",
        a: "The confirmation page shows your order, payment and invoice references. Your product appears in My Products; if it needs setting up first, My Products shows what is waiting.",
      },
      {
        q: "Where is my invoice?",
        a: "In My Account, under Billing & Subscriptions and Orders & Payments.",
      },
    ],
  },
  {
    key: "renewal",
    title: "Renewals",
    icon: "calendar",
    faqs: [
      {
        q: "How does my plan renew?",
        a: "Each plan on a product's page says how it renews. Renewal invoices and their payment are in My Account.",
      },
    ],
  },
  {
    key: "recharge",
    title: "Recharge",
    icon: "wallet",
    faqs: [
      {
        q: "What are the three balances?",
        a: "The Platform Wallet pays eligible subscriptions. AI Credits pay for AI features. The Consumption Wallet pays for messaging, API calls and webhooks. They are always kept separate.",
      },
      {
        q: "When does a recharge arrive?",
        a: "As soon as its payment is confirmed. The amount goes into the balance you recharged, once, and the transaction is listed in My Account.",
      },
      {
        q: "Can I buy AI credits from my Platform Wallet?",
        a: "No. AI credit packs and wallet recharges are paid online.",
      },
    ],
  },
];

export function HelpScreen() {
  const site = useSite();
  const account = useStoreAccount();
  const myAccount = useMyAccount();
  const [published, setPublished] = useState<HelpArticle[]>([]);
  const [policies, setPolicies] = useState<Set<string> | null>(null);
  useEffect(() => {
    let live = true;
    storeHostApi.faqs().then(
      (r) => live && setPublished(r.results.filter((a) => a.kind === "faq")),
      () => undefined,
    );
    storeHostApi.policies().then(
      (r) => live && setPolicies(new Set(r.results.map((a) => a.slug))),
      () => live && setPolicies(new Set()),
    );
    return () => {
      live = false;
    };
  }, []);
  const slug = account.organization?.slug ?? null;
  return (
    <div className={shell.container}>
      <nav aria-label="Breadcrumb" className={shell.crumbs}>
        <ol>
          <li>
            <Link href={storePaths.home}>Store</Link>
          </li>
          <li aria-current="page">Help</li>
        </ol>
      </nav>
      <header className={shell.pageHead}>
        <h1 className={shell.pageTitle}>Help &amp; Policies</h1>
        <p className={shell.pageLead}>How buying, payment, renewals and recharges work in the WorkEasy360 Store - and how to reach us.</p>
      </header>
      <nav className={styles.topicNav} aria-label="Help topics">
        {TOPICS.map((t) => (
          <a key={t.key} href={`#${t.key}`}>
            <Icon name={t.icon} size={18} /> {t.title}
          </a>
        ))}
        <a href="#contact">
          <Icon name="headset" size={18} /> Contact support
        </a>
      </nav>
      <div className={styles.helpLayout}>
        <div>
          {TOPICS.map((t) => (
            <section key={t.key} id={t.key} className={`${shell.card} ${styles.faqGroup}`} aria-labelledby={`${t.key}-title`}>
              <h2 id={`${t.key}-title`} className={styles.rechargeTitle}>
                <span className={styles.rechargeIcon}>
                  <Icon name={t.icon} size={22} />
                </span>
                {t.title}
              </h2>
              {t.faqs.map((f) => (
                <details key={f.q} className={styles.faq}>
                  <summary>{f.q}</summary>
                  <p>{f.a}</p>
                </details>
              ))}
            </section>
          ))}
          {published.length > 0 && (
            <section className={`${shell.card} ${styles.faqGroup}`} aria-labelledby="more-title">
              <h2 id="more-title" className={styles.rechargeTitle}>
                <span className={styles.rechargeIcon}>
                  <Icon name="help" size={22} />
                </span>
                More answers
              </h2>
              {published.map((a) => (
                <details key={a.slug} className={styles.faq}>
                  <summary>{a.title}</summary>
                  <p className={shell.prose}>{a.body}</p>
                </details>
              ))}
            </section>
          )}
        </div>
        <aside className={styles.helpAside}>
          <section id="contact" className={`${shell.card} ${styles.faqGroup}`} aria-labelledby="contact-title">
            <h2 id="contact-title" className={styles.detailHeading}>
              Contact support
            </h2>
            {account.status === "signed_in" && slug ? (
              <>
                <p className={styles.boxHint}>Raise a ticket in the support desk; replies and history stay with your organization.</p>
                <button type="button" className={`${shell.primary} ${styles.wide}`} onClick={() => void myAccount.open(`/o/${encodeURIComponent(slug)}/help?new=1`)}>
                  <Icon name="ticket" size={18} /> Raise a support ticket
                </button>
              </>
            ) : (
              <p className={styles.boxHint}>
                <Link href={`/login?next=${encodeURIComponent(`${storePaths.help}#contact`)}`}>Sign in</Link> to raise a support ticket for your organization.
              </p>
            )}
            {site?.support_email && (
              <p className={styles.contactLine}>
                <Icon name="mail" size={18} /> <a href={`mailto:${site.support_email}`}>{site.support_email}</a>
              </p>
            )}
            {site?.sales_email && site.sales_email !== site.support_email && (
              <p className={styles.contactLine}>
                <Icon name="chat" size={18} /> Sales: <a href={`mailto:${site.sales_email}`}>{site.sales_email}</a>
              </p>
            )}
          </section>
          <section id="policies" className={`${shell.card} ${styles.faqGroup}`} aria-labelledby="policies-title">
            <h2 id="policies-title" className={styles.detailHeading}>
              Policies
            </h2>
            <ul className={styles.policyList}>
              {POLICIES.map((p) => (
                <li key={p.key}>
                  <Link href={storePaths.policy(p.key)}>{p.label}</Link>
                  {policies && !policies.has(p.key) && <span className={styles.optional}> (not published yet)</span>}
                </li>
              ))}
            </ul>
          </section>
        </aside>
      </div>
    </div>
  );
}
