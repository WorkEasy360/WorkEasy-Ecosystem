"use client";

import Link from "next/link";
import { adminApi, productMediaUrl, type StoreCard, type StorePage } from "@/lib/api/controlPlane";
import { StoreBrowser, StoreCard as Card, storeAsideClass, type BadgeTone } from "@/features/storefront/StoreBrowser";
import { can, useAdmin } from "../ControlShell";
import { ButtonLink, Page } from "../kit";
import { StoreHeader } from "./StoreTabs";
import css from "./AdminStore.module.css";

/**
 * Super Admin → Product Store → Store: the central catalog as a marketplace
 * page - the shared Store layout (`StoreBrowser`) over `GET admin/products/store`.
 *
 * Every fact on a card is the server's: which products match, the counts
 * beside each filter, the order, the status badge and the price, which is the
 * direct list price a customer would see the product from (none when it is not
 * on sale). Read only - managing a product is the Product Manager's job, and
 * nothing shown here is anyone's access.
 */

const BADGE: Record<NonNullable<StoreCard["ribbon"]>["kind"], BadgeTone> = {
  new: "new",
  trial: "trial",
  coming_soon: "soon",
  draft: "quiet",
  hidden: "quiet",
  archived: "archived",
};

/** Short channel names for a card's tags. */
const CHANNEL: Record<string, string> = {
  customer_store: "Store",
  reseller: "Resellers",
  affiliate: "Affiliates",
  white_label: "White label",
};

export function ProductStore() {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_catalog.manage");
  const add = manage && (
    <ButtonLink variant="primary" icon="plus" href="/admin/products/new">
      New product
    </ButtonLink>
  );
  return (
    <Page>
    <StoreHeader current="browse" primary={add || undefined} actions={<ButtonLink href="/admin/products">Manage products</ButtonLink>} />
    <div className={css.preview}>
    <StoreBrowser<StoreCard, StorePage>
      title="Customer preview"
      titleLevel={2}
      lead="Every product as customers find it in the store, with the price they would see it from."
      load={adminApi.productStore}
      loadKey="admin"
      forbidden="You don't have access to the store."
      nothingYet={{ title: "There are no products yet.", action: add }}
      renderCard={(card, _page, onCategory) => (
        <Card
          key={card.id}
          code={card.code}
          title={card.title}
          href={`/admin/products/${card.id}`}
          icon={card.icon}
          logoSrc={card.logo_media_id ? productMediaUrl(card.id, card.logo_media_id) : null}
          bannerSrc={card.banner_media_id ? productMediaUrl(card.id, card.banner_media_id) : null}
          ribbon={card.ribbon && { label: card.ribbon.label, tone: BADGE[card.ribbon.kind] }}
          category={card.category}
          onCategory={onCategory}
          summary={card.summary}
          badges={card.badges}
          chipsLabel="Sold through"
          chips={[
            ...card.programs.map((p) => ({ label: CHANNEL[p] ?? p })),
            ...(card.trial_days != null && card.ribbon?.kind !== "trial" ? [{ label: `${card.trial_days}-day trial`, trial: true }] : []),
          ]}
          price={card.price}
          deal={card.yearly_deal}
          noPrice={card.store_status === "live" ? "No price yet" : "Not on sale"}
          aside={
            <Link className={`w360-btn w360-btn-secondary ${storeAsideClass}`} href={`/admin/products/${card.id}`} aria-label={`Manage ${card.title}`}>
              Manage
            </Link>
          }
        />
      )}
    />
    </div>
    </Page>
  );
}
