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
import { AffiliateDashboard, Campaigns, Leads, MarketingMaterials, ReferralCodes, ReferralLinks } from "./AffiliateScreens";
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
import { PartnerDashboard } from "./Dashboard";
import { Notifications, Support } from "./Help";
import { Affiliates, AffiliateConversions, AffiliatePayouts, AffiliateSettings, Agency, Team, Users } from "./Network";
import { Reports } from "./Reports";
import { ApiKeys, ChannelManager, Translations } from "./Tools";
import { WhiteLabelDashboard } from "./WhiteLabelDashboard";
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
  "support-manager": <Support />,
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
  "referral-links": <ReferralLinks />,
  "referral-codes": <ReferralCodes />,
  campaigns: <Campaigns />,
  "marketing-materials": <MarketingMaterials />,
  leads: <Leads />,
};

const DASHBOARDS: Record<ExperienceKey, ReactElement> = {
  reseller: <PartnerDashboard />,
  white_label: <WhiteLabelDashboard />,
  affiliate: <AffiliateDashboard />,
};

/** The page of a workspace, or null when there is none. */
export function pageFor(experience: ExperienceKey, path: string): ReactElement | null {
  if (path === "dashboard") return DASHBOARDS[experience];
  return PAGES[path] ?? null;
}

/** Every address with a page behind it (the architecture test checks that each
 * page a workspace lists has one). */
export const PAGE_PATHS: readonly string[] = ["dashboard", ...Object.keys(PAGES)];
