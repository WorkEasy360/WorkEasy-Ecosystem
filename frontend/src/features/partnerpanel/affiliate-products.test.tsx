import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { PartnerContext } from "@/features/controlplane/ControlShell";
import { expectNoViolations, mockApi, ok, type Reply } from "@/features/controlplane/testSupport";
import type { PortalPartner } from "@/lib/api/controlPlane";
import { MyProductsTab, ReferralTrailPanel } from "./AffiliateProducts";

const MyProducts = () => <MyProductsTab exploreHref="/partner/affiliate/store-console/explore" />;
import { panelApi } from "@/lib/api/partnerPanel";

/**
 * My Products and the attribution trail (ADR-054): the affiliate sees only the
 * products it was assigned, creates links by kind (the server builds the
 * address), renames and switches them, and reads a referral's trail without
 * the customer ever being named.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/partner/affiliate/store-console",
}));

const ID = "33333333-3333-3333-3333-333333333333";
const API = `/api/v1/partner/${ID}`;
const perm = (...keys: string[]) => keys.map((k) => `platform.partner_${k}`);
const AFFILIATE = perm("dashboard.view", "referral.view", "referral.create", "commission.view");

function partner(permissions: string[] = AFFILIATE): PortalPartner {
  return { partner_id: ID, code: "af", display_name: "Aff Co", status: "active", role: "owner", is_admin: true, capabilities: [], permissions };
}

const mount = (ui: React.ReactNode, permissions?: string[]) =>
  render(<PartnerContext.Provider value={{ partner: partner(permissions), base: "/partner/affiliate" }}>{ui}</PartnerContext.Provider>);

const EARN = [{ currency: "INR", sales_minor: 499900, commission_minor: 49990, sales: 1 }];
const ROLLUP = { links: 1, clicks: 9, visitors: 7, registrations: 3, trials: 2, conversions: 1, conversion_rate_bp: 1111, earnings: EARN };

const LINK = {
  id: "c1",
  code: "CRMAI",
  label: "AI launch",
  status: "active",
  created_at: "2026-10-20T10:00:00Z",
  campaign: "AI launch",
  campaign_id: null,
  campaign_name: null,
  product_code: "easy_crm",
  product_name: "Easy CRM",
  plan_code: "",
  link_kind: "update",
  update_id: "u1",
  update_title: "AI Lead Scoring",
  url: "https://app.example.com/r/CRMAI",
  clicks: 9,
  visitors: 7,
  registrations: 3,
  trials: 2,
  conversions: 1,
  earnings: EARN,
};

const UPDATE = {
  id: "u1",
  kind: "release",
  slug: "ai-lead-scoring",
  title: "AI Lead Scoring",
  summary: "Rank every lead.",
  body: "Easy CRM now scores your leads.",
  banner_url: null,
  landing_url: "",
  page_path: "/products/easy-crm/updates/ai-lead-scoring",
  published_at: "2026-10-18T10:00:00Z",
  ...ROLLUP,
};

const PRODUCTS = {
  range: { key: "30d", from: "2026-09-28T00:00:00Z", to: "2026-10-28T00:00:00Z" },
  can_create: true,
  products: [
    {
      code: "easy_crm",
      name: "Easy CRM",
      slug: "easy-crm",
      tagline: "Sell more",
      summary: "",
      description: "",
      category: "crm",
      icon: "",
      plans: [{ plan_code: "crm_basic", plan_name: "Basic" }],
      media: [],
      granted_at: "2026-10-01T00:00:00Z",
      features: [{ key: "crm.pipelines", name: "Pipelines", description: "Track every deal." }],
      updates: [UPDATE],
      links: [LINK],
      totals: ROLLUP,
    },
  ],
  withdrawn: [],
  campaigns: [],
  campaign_list: [{ id: "cmp1", name: "October", status: "active", links: 0, created_at: "2026-10-05T10:00:00Z" }],
  policy: { attribution_days: 30, revoked_links: "stop", attribution_rule: "first_touch" },
};

const routes = (extra: Record<string, Reply[]> = {}) => ({
  [`GET ${API}/affiliate/products`]: [ok(PRODUCTS), ok(PRODUCTS), ok(PRODUCTS)],
  ...extra,
});

describe("My Products", () => {
  it("shows only the assigned products with their updates, links and figures, and passes axe", async () => {
    mockApi(routes());
    const { container } = mount(<MyProducts />);
    expect(await screen.findByRole("heading", { level: 2, name: "Easy CRM" })).toBeInTheDocument();
    expect(screen.getByText(/credited to the first link they follow, for 30 days/)).toBeInTheDocument();
    const updates = screen.getByRole("table", { name: "Easy CRM: published features and updates" });
    expect(within(updates).getByText("AI Lead Scoring")).toBeInTheDocument();
    const links = screen.getByRole("table", { name: "Your links for Easy CRM" });
    expect(within(links).getByText("Product update: AI Lead Scoring")).toBeInTheDocument();
    expect(screen.getByText("Pipelines")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("creates an update link from the update's row - kind and ids only, never a URL", async () => {
    const calls = mockApi(
      routes({ [`POST ${API}/referrals`]: [{ status: 201, body: { id: "c9", code: "NEW1", label: "", url: "https://app.example.com/r/NEW1" } }] }),
    );
    mount(<MyProducts />);
    const updates = await screen.findByRole("table", { name: "Easy CRM: published features and updates" });
    fireEvent.click(within(updates).getByRole("button", { name: /Create link/ }));
    const form = screen.getByRole("form", { name: "Create a link for Easy CRM" });
    expect(within(form).getByLabelText("Link opens")).toHaveValue("update");
    expect(within(form).getByLabelText("Update")).toHaveValue("u1");
    fireEvent.click(within(form).getByRole("button", { name: "Generate link" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${API}/referrals`)).toBe(true));
    const body = calls.find((c) => c.key === `POST ${API}/referrals`)!.body as Record<string, unknown>;
    expect(body).toMatchObject({ link_kind: "update", product_code: "easy_crm", update_id: "u1" });
    expect(Object.values(body).some((v) => typeof v === "string" && v.startsWith("http"))).toBe(false);
  });

  it("renames and switches off a link through the server", async () => {
    const calls = mockApi(
      routes({
        [`PATCH ${API}/referral-codes/c1`]: [ok({ id: "c1", label: "Blog", status: "active" }), ok({ id: "c1", label: "Blog", status: "revoked" })],
      }),
    );
    mount(<MyProducts />);
    const links = await screen.findByRole("table", { name: "Your links for Easy CRM" });
    fireEvent.click(within(links).getByRole("button", { name: /Rename/ }));
    fireEvent.change(within(links).getByLabelText("New name for CRMAI"), { target: { value: "Blog" } });
    fireEvent.click(within(links).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.filter((c) => c.key === `PATCH ${API}/referral-codes/c1`)).toHaveLength(1));
    expect(calls.find((c) => c.key === `PATCH ${API}/referral-codes/c1`)!.body).toEqual({ label: "Blog" });
    fireEvent.click(await within(screen.getByRole("table", { name: "Your links for Easy CRM" })).findByRole("button", { name: /Switch off/ }));
    await waitFor(() => expect(calls.filter((c) => c.key === `PATCH ${API}/referral-codes/c1`)).toHaveLength(2));
    expect(calls.filter((c) => c.key === `PATCH ${API}/referral-codes/c1`)[1].body).toEqual({ active: false });
  });

  it("offers no link form to a role that may only look", async () => {
    mockApi(routes({ [`GET ${API}/affiliate/products`]: [ok({ ...PRODUCTS, can_create: false })] }));
    mount(<MyProducts />, perm("dashboard.view", "referral.view"));
    expect(await screen.findByRole("heading", { level: 2, name: "Easy CRM" })).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Create a link for Easy CRM" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Switch off/ })).not.toBeInTheDocument();
    expect(screen.getAllByRole("note")[0]).toHaveTextContent(/Your role cannot create links/);
  });

  it("tells a staff guest where the affiliate creates links, instead of an empty space", async () => {
    mockApi(routes({ [`GET ${API}/affiliate/products`]: [ok({ ...PRODUCTS, can_create: false })] }));
    const guest = { ...partner(perm("dashboard.view", "referral.view")), guest: { partner_id: ID, reason: "", expires_at: "2026-10-29T05:00:00Z" } };
    render(<PartnerContext.Provider value={{ partner: guest, base: "/partner/affiliate" }}><MyProducts /></PartnerContext.Provider>);
    expect(await screen.findByRole("heading", { level: 2, name: "Easy CRM" })).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Create a link for Easy CRM" })).not.toBeInTheDocument();
    const note = screen.getAllByRole("note")[0];
    expect(note).toHaveTextContent(/viewing this portal as a guest/);
    expect(note).toHaveTextContent(/“Create a link for Easy CRM” form appears here/);
  });
});

describe("the attribution trail", () => {
  it("tells each step and each sale's status without naming the customer", async () => {
    mockApi({
      [`GET ${API}/referrals/r1/trail`]: [
        ok({
          id: "r1",
          customer_ref: "C-1A2B3C4D",
          attribution_rule: "first_touch",
          status: "converted",
          expires_at: "2026-11-20T00:00:00Z",
          link: { id: "c1", code: "CRMAI", label: "AI launch", link_kind: "update", product_code: "easy_crm", plan_code: "", update_title: "AI Lead Scoring", campaign_name: null },
          steps: [
            { step: "clicked", at: "2026-10-20T10:00:00Z" },
            { step: "registered", at: "2026-10-20T10:05:00Z" },
            { step: "customer", at: "2026-10-20T10:06:00Z" },
            { step: "trial", at: null },
            { step: "purchased", at: "2026-10-21T09:00:00Z" },
          ],
          sales: [
            {
              id: "a1",
              product_code: "easy_crm",
              plan_code: "crm_basic",
              renewal: false,
              paid_at: "2026-10-21T09:00:00Z",
              payment_verified: true,
              sale_minor: 499900,
              commission_minor: 49990,
              currency: "INR",
              rate_bp: 1000,
              clears_at: "2026-11-20T09:00:00Z",
              status: "pending",
              reversed_minor: 0,
            },
          ],
          reversals: [],
        }),
      ],
    });
    const onClose = vi.fn();
    mount(<ReferralTrailPanel referralId="r1" fetchTrail={(r) => panelApi.referralTrail(ID, r)} onClose={onClose} />);
    expect(await screen.findByText(/C-1A2B3C4D/)).toBeInTheDocument();
    expect(screen.getByText(/Started a trial: not yet/)).toBeInTheDocument();
    const sales = screen.getByRole("table", { name: "Paid sales and their commission" });
    expect(within(sales).getByText("Verified")).toBeInTheDocument();
    expect(within(sales).getByText("pending")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Close/ }));
    expect(onClose).toHaveBeenCalled();
  });
});
