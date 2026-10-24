import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import type { PartnerUpgrades, UpgradeRequestRow } from "@/lib/api/partnerConsole";
import type { Load } from "./ui";
import { UpgradeNotice, UpgradePanel } from "./UpgradePanel";
import { expectNoViolations, mockApi, ok, type Call, type Reply } from "./testSupport";

/**
 * ADR-049, the staff side: a partner's upgrade standing and its review. What the
 * backend cannot enforce for the browser: the panel shows the level, the status
 * and what the partner asked for as plain text; a decision sends a choice, a
 * note, ticked product codes and a reference - never a capability or an amount;
 * a refusal is shown in the server's words with what is missing and changes
 * nothing; and a staff member who may not manage partners cannot decide.
 */

const ID = "p1";
const DECISION = `POST /api/v1/admin/partners/${ID}/upgrades/r1/decision`;

const PRODUCTS = [
  { code: "crm", name: "CRM" },
  { code: "books", name: "Books" },
];

function request(overrides: Partial<UpgradeRequestRow> = {}): UpgradeRequestRow {
  return {
    id: "r1",
    partner_id: ID,
    from_experience: "affiliate",
    to_experience: "reseller",
    status: "pending_approval",
    proposal: {
      products: ["crm"],
      pricing: { preference: "negotiated", notes: "Volume discount please" },
      commercial: { expected_customers: 40, team_size: 3, customer_management: "CRM sync", notes: "" },
    },
    requested_at: "2026-10-01T10:00:00Z",
    submitted_at: "2026-10-02T10:00:00Z",
    decided_at: null,
    info_request_note: "",
    decision_note: "",
    capabilities_added: [],
    capabilities_removed: [],
    products_granted: [],
    version: 4,
    editable: false,
    requested_by: "pat@acme.example",
    decided_by: null,
    commercial_reference: "",
    correlation_id: null,
    ...overrides,
  };
}

const CHECKLIST = [
  { key: "business", label: "Business details", required: true, done: true, detail: "" },
  { key: "products", label: "Products to sell", required: true, done: true, detail: "" },
  { key: "pricing", label: "Pricing preference", required: false, done: true, detail: "" },
  { key: "commercial", label: "Commercial notes", required: false, done: false, detail: "" },
];

function upgrades(overrides: Partial<PartnerUpgrades> = {}): PartnerUpgrades {
  return {
    current: "affiliate",
    eligible: [],
    status: "pending_approval",
    requested: "reseller",
    open: request(),
    checklist: CHECKLIST,
    history: [],
    products: PRODUCTS,
    ...overrides,
  };
}

const ready = (data: PartnerUpgrades): Load<PartnerUpgrades> => ({ status: "ready", data });

const onChanged = vi.fn();
const onOpenTab = vi.fn();

function mount(data: PartnerUpgrades, manage = true, available = ["profile", "products", "commission"]) {
  return render(<UpgradePanel partnerId={ID} load={ready(data)} manage={manage} onChanged={onChanged} onOpenTab={onOpenTab} available={available} />);
}

const writes = (calls: Call[]) => calls.filter((c) => !c.key.startsWith("GET "));
const status = () => screen.getByText("Upgrade status").nextElementSibling as HTMLElement;

beforeEach(() => {
  vi.restoreAllMocks();
  onChanged.mockReset();
  onOpenTab.mockReset();
});

describe("what the panel shows", () => {
  it("says nothing is requested, and what the partner could ask for", async () => {
    const { container } = mount(upgrades({ status: "none", requested: null, open: null, checklist: [], eligible: ["reseller", "white_label"] }));
    expect(screen.getByRole("heading", { level: 2, name: "Upgrade" })).toBeInTheDocument();
    expect(screen.getByText("Current partner level").nextElementSibling).toHaveTextContent("Affiliate");
    expect(status()).toHaveTextContent("none");
    expect(screen.getByText("Requested upgrade").nextElementSibling).toHaveTextContent("—");
    expect(screen.getByText(/The partner can ask for Reseller or White-label from its own dashboard\./)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("says there is nothing above the highest level", () => {
    mount(upgrades({ current: "white_label", status: "none", requested: null, open: null, checklist: [] }));
    expect(screen.getByText("No upgrade requested, and none is available from this level.")).toBeInTheDocument();
  });

  it("shows a pending request: level, status, target, checklist and exactly what the partner asked for", async () => {
    const { container } = mount(upgrades());
    expect(screen.getByText("Current partner level").nextElementSibling).toHaveTextContent("Affiliate");
    expect(status()).toHaveTextContent("pending approval");
    expect(screen.getByText("Requested upgrade").nextElementSibling).toHaveTextContent("Reseller");

    const list = screen.getByRole("region", { name: "What the request still needs" });
    expect(within(list).getByText("Business details").parentElement).toHaveTextContent("done");
    expect(within(list).getByText("Commercial notes").parentElement).toHaveTextContent("optional, not provided");

    const asked = screen.getByRole("region", { name: "What the partner asked for" });
    expect(within(asked).getByText("Products").nextElementSibling).toHaveTextContent("CRM");
    expect(within(asked).getByText("Pricing preference").nextElementSibling).toHaveTextContent("Negotiated");
    expect(within(asked).getByText("Volume discount please")).toBeInTheDocument();
    expect(within(asked).getByText("Expected customers").nextElementSibling).toHaveTextContent("40");
    expect(screen.getByText(/Requested .* by pat@acme\.example; submitted/)).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("shows the brand and proposed domain of a white-label request", () => {
    mount(
      upgrades({
        open: request({
          to_experience: "white_label",
          proposal: {
            products: ["crm"],
            brand: { brand_name: "Acme Cloud", primary_color: "#1a73e8", secondary_color: "#34a853", support_email: "help@acme.example" },
            domain: { hostname: "app.acme.example" },
          },
        }),
      }),
    );
    expect(screen.getByText("Requested upgrade").nextElementSibling).toHaveTextContent("White-label");
    const asked = screen.getByRole("region", { name: "What the partner asked for" });
    expect(within(asked).getByText("Brand name").nextElementSibling).toHaveTextContent("Acme Cloud");
    expect(within(asked).getByText("Primary colour").nextElementSibling).toHaveTextContent("#1a73e8");
    expect(within(asked).getByText("Proposed domain").nextElementSibling).toHaveTextContent("app.acme.example");
  });

  it("shows a request the partner has started as not yet decidable", () => {
    mount(upgrades({ status: "requested", open: request({ status: "requested", submitted_at: null, proposal: {} }) }));
    expect(status()).toHaveTextContent("requested");
    expect(screen.getByText(/has not submitted it\. There is nothing to decide yet\./)).toBeInTheDocument();
    expect(screen.getByText("The partner has not filled anything in yet.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve|Reject|Send back/ })).not.toBeInTheDocument();
  });

  it("shows a request sent back with the note, where only rejecting is left", () => {
    mount(upgrades({ status: "profile_required", open: request({ status: "profile_required", info_request_note: "Please confirm VAT" }) }));
    expect(status()).toHaveTextContent("profile required");
    expect(screen.getByText(/Sent back to the partner: Please confirm VAT/)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Approve" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "Ask for more information" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "Reject" })).toBeChecked();
    expect(screen.getByRole("button", { name: "Reject upgrade" })).toBeInTheDocument();
  });

  it("shows how the last decided request ended when none is open", () => {
    mount(
      upgrades({
        status: "none",
        requested: null,
        open: null,
        checklist: [],
        current: "reseller",
        history: [
          request({
            status: "approved",
            decided_at: "2026-10-03T10:00:00Z",
            decided_by: "staff@w360.example",
            decision_note: "Agreed on the call",
            commercial_reference: "CONTRACT-17",
            capabilities_added: ["customer.provision", "subscription.sell"],
            products_granted: ["crm"],
          }),
        ],
      }),
    );
    expect(status()).toHaveTextContent("approved");
    expect(screen.getByText("Requested upgrade").nextElementSibling).toHaveTextContent("Reseller");
    const history = screen.getByRole("region", { name: "Upgrade history" });
    expect(within(history).getByText("Affiliate → Reseller")).toBeInTheDocument();
    expect(within(history).getByText("Note: Agreed on the call")).toBeInTheDocument();
    expect(within(history).getByText("Commercial reference: CONTRACT-17")).toBeInTheDocument();
    expect(within(history).getByText(/Added 2 capabilities \(customer\.provision, subscription\.sell\); products: CRM\. Nothing was removed\./)).toBeInTheDocument();
  });

  it("shows a rejected request, with the note the partner was given", () => {
    mount(
      upgrades({
        status: "none",
        requested: null,
        open: null,
        checklist: [],
        history: [request({ status: "rejected", decided_at: "2026-10-03T10:00:00Z", decision_note: "Not yet, sorry" })],
      }),
    );
    expect(status()).toHaveTextContent("rejected");
    expect(screen.getByText("Note: Not yet, sorry")).toBeInTheDocument();
  });

  it("does not let a withdrawn request stand for the partner's state", () => {
    mount(upgrades({ status: "none", requested: null, open: null, checklist: [], eligible: ["reseller"], history: [request({ status: "cancelled" })] }));
    expect(status()).toHaveTextContent("none");
  });

  it("shows what the partner typed as text and never as markup", () => {
    const hostile = '<img src=x onerror="alert(1)"><script>alert(2)</script>';
    const { container } = mount(
      upgrades({
        open: request({
          proposal: { pricing: { preference: "standard", notes: hostile }, brand: { brand_name: hostile, primary_color: "", secondary_color: "", support_email: "" } },
        }),
      }),
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getAllByText(hostile).length).toBeGreaterThan(0);
  });
});

describe("the header line", () => {
  it("appears only while a request is open, and opens the panel", () => {
    const open = vi.fn();
    const { rerender } = render(<UpgradeNotice load={ready(upgrades())} onOpen={open} />);
    const notice = screen.getByRole("region", { name: "Upgrade request" });
    expect(notice).toHaveTextContent("Upgrade to Reseller requested");
    expect(notice).toHaveTextContent("pending approval");
    fireEvent.click(within(notice).getByRole("button", { name: "Review the upgrade request" }));
    expect(open).toHaveBeenCalledTimes(1);

    rerender(<UpgradeNotice load={ready(upgrades({ status: "none", open: null }))} onOpen={open} />);
    expect(screen.queryByRole("region", { name: "Upgrade request" })).not.toBeInTheDocument();
    rerender(<UpgradeNotice load={{ status: "loading" }} onOpen={open} />);
    expect(screen.queryByRole("region", { name: "Upgrade request" })).not.toBeInTheDocument();
  });
});

describe("deciding", () => {
  it("approves with the ticked products, a reference and a note - and sends no capability or amount", async () => {
    const calls = mockApi({ [DECISION]: [ok(request({ status: "approved" }))] });
    mount(upgrades());
    // What the partner asked for is ticked; the other offered product is not.
    expect(screen.getByRole("checkbox", { name: /CRM/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Books/ })).not.toBeChecked();
    fireEvent.click(screen.getByRole("checkbox", { name: /Books/ }));
    fireEvent.change(screen.getByLabelText(/Commercial reference/), { target: { value: "CONTRACT-2026-17" } });
    fireEvent.change(screen.getByLabelText(/Note \(optional\)/), { target: { value: "Agreed on the call" } });
    fireEvent.click(screen.getByRole("button", { name: "Approve upgrade" }));

    await screen.findByText(/Upgrade approved\. The partner keeps its login and everything it earned/);
    expect(writes(calls)).toHaveLength(1);
    expect(writes(calls)[0].body).toEqual({
      action: "approve",
      note: "Agreed on the call",
      products: ["crm", "books"],
      commercial_reference: "CONTRACT-2026-17",
    });
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("can approve with a product the partner did not ask for left unticked, and with none", async () => {
    const calls = mockApi({ [DECISION]: [ok(request({ status: "approved" }))] });
    mount(upgrades());
    fireEvent.click(screen.getByRole("checkbox", { name: /CRM/ }));
    fireEvent.click(screen.getByRole("button", { name: "Approve upgrade" }));
    await screen.findByText(/Upgrade approved/);
    expect(writes(calls)[0].body).toMatchObject({ action: "approve", products: [] });
  });

  it("lists a product the partner asked for even when it is no longer on the offered list", () => {
    mount(upgrades({ products: [{ code: "books", name: "Books" }], open: request({ proposal: { products: ["retired-thing"] } }) }));
    expect(screen.getByRole("checkbox", { name: /retired-thing/ })).toBeChecked();
  });

  it("asks for more information with a required note", async () => {
    const calls = mockApi({ [DECISION]: [ok(request({ status: "profile_required" }))] });
    mount(upgrades());
    fireEvent.click(screen.getByRole("radio", { name: "Ask for more information" }));
    const send = screen.getByRole("button", { name: "Send back to the partner" });
    expect(send).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Note to the partner \(required\)/), { target: { value: "Please confirm VAT" } });
    expect(send).toBeEnabled();
    fireEvent.click(send);
    await screen.findByText("Sent back to the partner with your note.");
    expect(writes(calls)[0].body).toEqual({ action: "request_info", note: "Please confirm VAT" });
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("rejects with a required note and sends no products", async () => {
    const calls = mockApi({ [DECISION]: [ok(request({ status: "rejected" }))] });
    mount(upgrades());
    fireEvent.click(screen.getByRole("radio", { name: "Reject" }));
    const reject = screen.getByRole("button", { name: "Reject upgrade" });
    expect(reject).toBeDisabled();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Note to the partner \(required\)/), { target: { value: "Not yet, sorry" } });
    fireEvent.click(reject);
    await screen.findByText("Upgrade rejected. Nothing the partner holds has changed.");
    expect(writes(calls)[0].body).toEqual({ action: "reject", note: "Not yet, sorry" });
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("shows the server's refusal and what is missing, keeps what was typed, and does not reload", async () => {
    mockApi({
      [DECISION]: [
        {
          status: 400,
          body: {
            code: "partner_validation_error",
            detail: "Not ready to approve - still needed: business details (Add phone); products (Allow at least one product this partner may sell).",
            field: "business",
            missing: ["business", "products", "something-new"],
          },
        } satisfies Reply,
      ],
    });
    const { container } = mount(upgrades());
    fireEvent.change(screen.getByLabelText(/Note \(optional\)/), { target: { value: "Looks fine to me" } });
    fireEvent.click(screen.getByRole("button", { name: "Approve upgrade" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/Not ready to approve - still needed: business details \(Add phone\); products/);
    const still = await screen.findByText("Still needed before this can be approved:");
    const items = within(still.parentElement!).getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual(["Business details", "Products", "something-new"]);
    expect(onChanged).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/Note \(optional\)/)).toHaveValue("Looks fine to me");
    await expectNoViolations(container);
  });

  it("shows a conflict (already decided) in the server's words", async () => {
    mockApi({ [DECISION]: [{ status: 409, body: { code: "partner_upgrade_conflict", detail: "This request is not waiting for that decision." } }] });
    mount(upgrades());
    fireEvent.click(screen.getByRole("button", { name: "Approve upgrade" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This request is not waiting for that decision.");
    expect(onChanged).not.toHaveBeenCalled();
  });

});

describe("who may decide", () => {
  it("shows a viewer the request and offers no decision", async () => {
    const { container } = mount(upgrades(), false);
    expect(screen.getByRole("region", { name: "What the partner asked for" })).toBeInTheDocument();
    expect(screen.getByText("You can read this request but not decide it.")).toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Decision" })).not.toBeInTheDocument();
    await expectNoViolations(container);
  });
});

describe("checking what a decision depends on", () => {
  it("points to the partner's own tabs that exist, and opens them", () => {
    mount(upgrades(), true, ["profile", "products"]);
    const form = screen.getByRole("heading", { name: "Decision" }).closest("form")!;
    expect(within(form).getByRole("button", { name: "Profile" })).toBeInTheDocument();
    expect(within(form).queryByRole("button", { name: "Commission" })).not.toBeInTheDocument();
    fireEvent.click(within(form).getByRole("button", { name: "Products" }));
    expect(onOpenTab).toHaveBeenCalledWith("products");
  });
});

describe("loading and refusal", () => {
  it("says it is loading, and says when the status cannot be read", () => {
    const { rerender } = render(<UpgradePanel partnerId={ID} load={{ status: "loading" }} manage onChanged={onChanged} />);
    expect(screen.getByText("Loading the upgrade status…")).toBeInTheDocument();
    rerender(<UpgradePanel partnerId={ID} load={{ status: "error", message: "Something went wrong. Please try again." }} manage onChanged={onChanged} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong");
    rerender(<UpgradePanel partnerId={ID} load={{ status: "forbidden" }} manage onChanged={onChanged} />);
    expect(screen.getByRole("alert")).toHaveTextContent("You don't have access to the upgrade status.");
  });
});
