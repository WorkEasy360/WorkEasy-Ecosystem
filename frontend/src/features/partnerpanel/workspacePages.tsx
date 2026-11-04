"use client";

import type { ReactElement } from "react";
import { PortalWhiteLabelScreen } from "@/features/controlplane/WhiteLabelWizard";
import {
  Account,
  Appearance,
  AuthConfiguration,
  CommunicationAlerts,
  CommunicationChannels,
  CommunicationLogs,
  CommunicationTemplates,
  Integrations,
  SharedResources,
} from "./Account";
import { Roles } from "./Access";
import { Campaigns, Leads, MarketingMaterials, ReferralCodes, ReferralLinks } from "./AffiliateScreens";
import { BrandSettings, UrlManager } from "./Brand";
import {
  AddOns,
  Balance,
  Currency,
  PartnerProducts,
  PaymentCommissions,
  Payments,
  Plans,
  PricingPackages,
  PricingRules,
  ProductBilling,
  ProductManager,
  Subscriptions,
} from "./Catalog";
import { Notifications } from "./Help";
import { CustomerDesk, Disputes, MyTickets, SavedReplies } from "./Helpdesk";
import { MarginCalculator } from "./MarginCalculator";
import { Affiliates, AffiliateConversions, AffiliatePayouts, AffiliateSettings, Agency, Team, Users } from "./Network";
import { Reports } from "./Reports";
import { StoreConsole } from "./StoreConsole";
import { PartnerStore } from "./PartnerStore";
import { UnifiedDashboard } from "./UnifiedDashboard";
import { UpgradePage } from "./UpgradeWizard";
import { ApiKeys, ChannelManager, Translations } from "./Tools";
import { Wallet } from "./Wallet";
import type { ExperienceKey } from "./experiences";

/**
 * The pages of the shared partner panel, by the address they are served at inside
 * a workspace (ADR-047). One table for all three workspaces: which of these a
 * workspace serves is `experiences.ts`'s list, and whether a person may open one is
 * their permissions - this file only says what a page *is*.
 *
 * The table holds elements, not component types: a component picked by a call
 * during render would be a new type on every render and lose its state.
 */
const PAGES: Record<string, ReactElement> = {
  users: <Users />,
  "users/subscriptions": <Subscriptions />,
  "partner-products": <PartnerProducts />,
  "product-manager": <ProductManager />,
  "product-billing": <ProductBilling />,
  plans: <Plans />,
  "plans/add-ons": <AddOns />,
  "pricing/currency": <Currency />,
  "pricing/packages": <PricingPackages />,
  "pricing/rules": <PricingRules />,
  payments: <Payments />,
  "payments/commissions": <PaymentCommissions />,
  "payments/balance": <Balance />,
  payouts: <AffiliatePayouts />,
  "affiliates/payouts": <AffiliatePayouts />,
  agency: <Agency />,
  affiliates: <Affiliates />,
  conversions: <AffiliateConversions />,
  "affiliates/conversions": <AffiliateConversions />,
  "affiliates/settings": <AffiliateSettings />,
  team: <Team />,
  roles: <Roles />,
  reports: <Reports />,
  // 2026-11-03: the helpdesk (tickets) replaces the one-shot support form.
  "support-manager": <MyTickets />,
  desk: <CustomerDesk />,
  "desk/replies": <SavedReplies />,
  disputes: <Disputes />,
  wallet: <Wallet />,
  "margin-calculator": <MarginCalculator />,
  notifications: <Notifications />,
  account: <Account />,
  appearance: <Appearance />,
  "communication/channel": <CommunicationChannels />,
  "communication/templates": <CommunicationTemplates />,
  "communication/alerts": <CommunicationAlerts />,
  "communication/logs": <CommunicationLogs />,
  integrations: <Integrations />,
  "auth-configuration": <AuthConfiguration />,
  "shared-resources": <SharedResources />,
  "channel-manager": <ChannelManager />,
  "api-keys": <ApiKeys />,
  translations: <Translations />,
  "brand-settings": <BrandSettings />,
  "brand-settings/url-manager": <UrlManager />,
  "brand-settings/white-label-setup": <PortalWhiteLabelScreen />,
  store: <PartnerStore kind="promote" mine={{ path: "store-console", label: "Approved / Promoting" }} />,
  "store-console": <StoreConsole tab="mine" kind="promote" />,
  "store-console/explore": <StoreConsole tab="explore" kind="promote" />,
  "store-console/requests": <StoreConsole tab="requests" kind="promote" />,
  // 2026-11-03: an upgraded affiliate keeps promoting from its higher workspace,
  // whose own `store` sells - so its promoting Store lives under `promote/`.
  "promote/store": <PartnerStore kind="promote" mine={{ path: "promote/store-console", label: "Approved / Promoting" }} />,
  "promote/store-console": <StoreConsole tab="mine" kind="promote" root="promote/store-console" />,
  "promote/store-console/explore": <StoreConsole tab="explore" kind="promote" root="promote/store-console" />,
  "promote/store-console/requests": <StoreConsole tab="requests" kind="promote" root="promote/store-console" />,
  "referral-links": <ReferralLinks />,
  "referral-codes": <ReferralCodes />,
  campaigns: <Campaigns />,
  "marketing-materials": <MarketingMaterials />,
  leads: <Leads />,
  // Growing the partnership: a request on the same account (ADR-049).
  upgrade: <UpgradePage />,
  "upgrade/reseller": <UpgradePage target="reseller" />,
  "upgrade/white-label": <UpgradePage target="white_label" />,
};

/** One dashboard for every level (2026-11-03, the unified partner dashboard):
 * the level decides its ribbon and its extra sections. */
const DASHBOARDS: Record<ExperienceKey, ReactElement> = {
  reseller: <UnifiedDashboard level="reseller" />,
  white_label: <UnifiedDashboard level="white_label" />,
  affiliate: <UnifiedDashboard level="affiliate" />,
};

/** The Store Console asks for the right kind of permission in each workspace
 * (ADR-055): an affiliate asks to promote, a reseller to sell. */
const SELLER_PAGES: Record<string, ReactElement> = {
  store: <PartnerStore kind="sell" mine={{ path: "store-console", label: "My Products" }} />,
  "store-console": <StoreConsole tab="mine" kind="sell" />,
  "store-console/explore": <StoreConsole tab="explore" kind="sell" />,
  "store-console/requests": <StoreConsole tab="requests" kind="sell" />,
};

/** The page of a workspace, or null when there is none. */
export function pageFor(experience: ExperienceKey, path: string): ReactElement | null {
  if (path === "dashboard") return DASHBOARDS[experience];
  // 2026-11-03: one panel - a white-label partner sells from the same Store
  // Console as a reseller (its Products & Plans page is a page of My Products).
  if (experience !== "affiliate" && SELLER_PAGES[path]) return SELLER_PAGES[path];
  return PAGES[path] ?? null;
}

/** Every address with a page behind it (the architecture test checks that each
 * page a workspace lists has one). */
export const PAGE_PATHS: readonly string[] = ["dashboard", ...Object.keys(PAGES)];
