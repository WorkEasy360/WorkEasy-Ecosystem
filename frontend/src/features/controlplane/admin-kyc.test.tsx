import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { KycApplicationScreen, KycApplicationsScreen, KycRequirementsScreen } from "./AdminKyc";
import { ToastProvider } from "./kit";
import { AdminContextForTest, expectNoViolations, mockApi, ok, type Call } from "./testSupport";

/**
 * Admin → Partners → Applications and KYC requirements (2026-11-09). KYC status and
 * partner account status are two systems, shown side by side; the actions follow what
 * the server says this person may do.
 */

const nav = vi.hoisted(() => ({ router: { replace: vi.fn(), push: vi.fn(), refresh: vi.fn() } }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/admin/partners/applications",
}));

beforeEach(() => {
  vi.restoreAllMocks();
  nav.router.push.mockReset();
});

const QUEUE = "/api/v1/admin/kyc/applications";
const REQUIREMENTS = "/api/v1/admin/kyc/requirements";
const PARTNER = "11111111-1111-4111-8111-111111111111";
const DETAIL = `${QUEUE}/${PARTNER}`;
const T = "2026-11-01T10:00:00Z";

const VIEW = ["platform.staff_kyc.view"];
const REVIEW = [...VIEW, "platform.staff_kyc.review"];

const ROW = {
  partner_id: PARTNER,
  partner_name: "Acme Partners",
  partner_code: "acme",
  partner_status: "draft",
  contact_email: "owner@acme.example",
  status: "under_review",
  requested_experience: "white_label",
  source: "application",
  submitted_at: T,
  decided_at: null,
  reviewer_name: "Asha Rao",
  decided_by_name: "",
  created_at: T,
};

const COUNTS = { draft: 4, submitted: 2, under_review: 1, correction_required: 3, approved: 5, rejected: 6 };

const page = (results: unknown[], extra: Record<string, unknown> = {}) => ok({ results, total: results.length, limit: 25, offset: 0, counts: COUNTS, ...extra });

const screenAs = (permissions: string[], ui: React.ReactNode) =>
  render(
    <AdminContextForTest permissions={permissions}>
      <ToastProvider>
        <main>{ui}</main>
      </ToastProvider>
    </AdminContextForTest>,
  );

const lastUrl = (calls: Call[], key: string) => new URL(calls.filter((c) => c.key === key).at(-1)!.url, "https://x.invalid");

describe("the applications queue", () => {
  it("lists real rows with both status systems labelled, and passes axe", async () => {
    const calls = mockApi({ [`GET ${QUEUE}`]: [page([ROW])] });
    const { container } = screenAs(VIEW, <KycApplicationsScreen />);
    const table = await screen.findByRole("table", { name: "Partner applications" });
    for (const heading of ["Partner", "Contact email", "KYC status", "Partner account status", "Requested experience", "Submitted", "Reviewer"]) {
      expect(within(table).getByRole("columnheader", { name: heading })).toBeInTheDocument();
    }
    const row = within(table).getByRole("row", { name: /Acme Partners/ });
    expect(row).toHaveTextContent("owner@acme.example");
    expect(row).toHaveTextContent("Under review");
    expect(row).toHaveTextContent("Draft");
    expect(row).toHaveTextContent("White-label");
    expect(row).toHaveTextContent("1 Nov 2026");
    expect(row).toHaveTextContent("Asha Rao");
    expect(within(row).getByRole("link", { name: "Acme Partners" })).toHaveAttribute("href", `/admin/partners/applications/${PARTNER}`);
    // The default is what waits for a reviewer: no status is sent at all.
    expect(lastUrl(calls, `GET ${QUEUE}`).searchParams.has("status")).toBe(false);
    await expectNoViolations(container);
  });

  it("opens the application when a row is clicked", async () => {
    mockApi({ [`GET ${QUEUE}`]: [page([ROW])] });
    screenAs(VIEW, <KycApplicationsScreen />);
    fireEvent.click(await screen.findByText("owner@acme.example"));
    expect(nav.router.push).toHaveBeenCalledWith(`/admin/partners/applications/${PARTNER}`);
  });

  it("shows the counts on the chips and asks the server for the chosen status", async () => {
    const calls = mockApi({ [`GET ${QUEUE}`]: [page([ROW])] });
    screenAs(VIEW, <KycApplicationsScreen />);
    await screen.findByRole("table", { name: "Partner applications" });
    const chips = screen.getByRole("group", { name: "KYC status" });
    const chip = (name: RegExp) => within(chips).getByRole("button", { name });
    expect(chip(/^Waiting for review/)).toHaveAttribute("aria-pressed", "true");
    expect(chip(/^Waiting for review/)).toHaveTextContent("3");
    expect(chip(/^All/)).toHaveTextContent("21");
    expect(chip(/^Correction required/)).toHaveTextContent("3");
    expect(chip(/^Rejected/)).toHaveTextContent("6");

    fireEvent.click(chip(/^Rejected/));
    await waitFor(() => expect(lastUrl(calls, `GET ${QUEUE}`).searchParams.get("status")).toBe("rejected"));
    fireEvent.click(chip(/^All/));
    await waitFor(() => expect(lastUrl(calls, `GET ${QUEUE}`).searchParams.get("status")).toBe("all"));
  });

  it("searches on the server, and hides counts that would not match a search", async () => {
    const calls = mockApi({ [`GET ${QUEUE}`]: [page([ROW])] });
    screenAs(VIEW, <KycApplicationsScreen />);
    await screen.findByRole("table", { name: "Partner applications" });
    const box = screen.getByLabelText("Search by partner, code or email");
    fireEvent.change(box, { target: { value: "acme" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(lastUrl(calls, `GET ${QUEUE}`).searchParams.get("q")).toBe("acme"));
    expect(within(screen.getByRole("group", { name: "KYC status" })).getByRole("button", { name: /^Rejected/ })).not.toHaveTextContent("6");
  });

  it("pages by offset", async () => {
    const calls = mockApi({ [`GET ${QUEUE}`]: [page([ROW], { total: 30 })] });
    screenAs(VIEW, <KycApplicationsScreen />);
    await screen.findByText("Showing 1–1 of 30");
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(lastUrl(calls, `GET ${QUEUE}`).searchParams.get("offset")).toBe("25"));
  });

  it("says when nothing is waiting", async () => {
    mockApi({ [`GET ${QUEUE}`]: [page([])] });
    screenAs(VIEW, <KycApplicationsScreen />);
    expect(await screen.findByText("Nothing is waiting for review")).toBeInTheDocument();
  });

  it("shows an error state and a refusal", async () => {
    mockApi({ [`GET ${QUEUE}`]: [{ status: 500, body: { detail: "The queue is unavailable." } }] });
    const { unmount } = screenAs(VIEW, <KycApplicationsScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("The queue is unavailable.");
    unmount();

    mockApi({ [`GET ${QUEUE}`]: [{ status: 403, body: { detail: "No." } }] });
    screenAs([], <KycApplicationsScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("You don't have access to partner applications.");
  });
});

const REQUIREMENT_A = { id: "r1", key: "certificate", label: "Certificate of incorporation", help_text: "A clear scan.", is_required: true, is_active: true, display_order: 1 };
const REQUIREMENT_B = { id: "r2", key: "tax_card", label: "Tax card", help_text: "", is_required: false, is_active: true, display_order: 2 };

const APP = {
  ...ROW,
  status: "submitted",
  reviewer_name: "",
  version: 7,
  details: { legal_name: "Acme Partners Pvt Ltd", country: "IN", city: "Pune", tax_number: "" },
  decision_reason: "",
  requirements: [REQUIREMENT_A, REQUIREMENT_B],
  documents: [
    { id: "d3", requirement_key: "old_key", filename: "old.pdf", content_type: "application/pdf", size_bytes: 500, uploaded_at: T, removed_at: null },
    { id: "d2", requirement_key: "certificate", filename: "cert-new.pdf", content_type: "application/pdf", size_bytes: 2048, uploaded_at: T, removed_at: null },
    { id: "d1", requirement_key: "certificate", filename: "cert-old.pdf", content_type: "application/pdf", size_bytes: 3 * 1024 * 1024, uploaded_at: T, removed_at: "2026-11-02T10:00:00Z" },
  ],
  missing: ["Bank letter"],
  decisions: [
    {
      id: "h1",
      decision: "request_correction",
      from_status: "under_review",
      to_status: "correction_required",
      reason: "The certificate is cut off.",
      flagged_requirements: ["certificate"],
      decided_by: "u1",
      decided_by_name: "Asha Rao",
      decided_at: T,
    },
  ],
  document_access: [{ document_id: "d2", accessed_by_name: "Sam Lee", accessed_at: T }],
  may_review: true,
  may_decide: true,
};

const reviewer = (app: unknown = APP) => ({ [`GET ${DETAIL}`]: [ok(app)] });

async function openDetail(permissions = REVIEW) {
  const view = screenAs(permissions, <KycApplicationScreen partnerId={PARTNER} />);
  await screen.findByRole("heading", { level: 1, name: "Acme Partners" });
  return view;
}

const bodyOf = (calls: Call[], key: string) => calls.find((c) => c.key === key)?.body;

describe("one application", () => {
  it("shows details, documents, what is missing, the history and the access log; passes axe", async () => {
    mockApi(reviewer());
    const { container } = await openDetail();

    expect(screen.getByText("KYC: Submitted")).toBeInTheDocument();
    expect(screen.getByText("Partner account: Draft")).toBeInTheDocument();
    expect(screen.getByText("Acme Partners Pvt Ltd")).toBeInTheDocument();
    expect(screen.getByText("India")).toBeInTheDocument();

    const documents = screen.getByRole("list", { name: "Documents by requirement" });
    const certificate = within(documents).getByText("Certificate of incorporation").closest("li")!;
    expect(within(certificate).getByRole("link", { name: "Open cert-new.pdf" })).toHaveAttribute("href", `${DETAIL}/documents/d2/content`);
    expect(within(certificate).getByRole("link", { name: "Open cert-new.pdf" })).toHaveAttribute("target", "_blank");
    expect(within(certificate).getByRole("link", { name: "Open cert-new.pdf" })).toHaveAttribute("rel", "noopener");
    expect(within(certificate).getByText("cert-old.pdf").closest("li")).toHaveTextContent("Replaced 2 Nov 2026");
    expect(within(certificate).getByText("cert-new.pdf").closest("li")).not.toHaveTextContent("Replaced");
    expect(within(documents).getByText("Tax card").closest("li")).toHaveTextContent("Nothing uploaded.");
    // A requirement since switched off: its document is still on the application.
    expect(within(documents).getByText("old.pdf")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Bank letter");

    const history = screen.getByRole("table", { name: "Decision history" });
    const step = within(history).getByRole("row", { name: /Asha Rao/ });
    expect(step).toHaveTextContent("Under review");
    expect(step).toHaveTextContent("Correction required");
    expect(step).toHaveTextContent("The certificate is cut off.");
    expect(step).toHaveTextContent("Certificate of incorporation");

    const log = screen.getByRole("table", { name: "Document access log" });
    expect(within(log).getByRole("row", { name: /Sam Lee/ })).toHaveTextContent("cert-new.pdf");

    await expectNoViolations(container);
  });

  it("gives someone who may only view no document links and no actions", async () => {
    mockApi(reviewer({ ...APP, may_review: false, may_decide: false }));
    await openDetail(VIEW);
    expect(within(screen.getByRole("list", { name: "Documents by requirement" })).getByText("cert-new.pdf")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^Open cert/ })).not.toBeInTheDocument();
    for (const name of ["Start review", "Approve", "Request corrections", "Reject"]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });

  it("offers Start review only on a submitted application", async () => {
    mockApi(reviewer({ ...APP, status: "under_review" }));
    await openDetail();
    expect(screen.queryByRole("button", { name: "Start review" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("starts a review, says so and refreshes", async () => {
    const calls = mockApi({ ...reviewer(), [`POST ${DETAIL}/review`]: [ok(APP)] });
    await openDetail();
    fireEvent.click(screen.getByRole("button", { name: "Start review" }));
    expect(await screen.findByText("Review started. You are now its reviewer.")).toBeInTheDocument();
    expect(bodyOf(calls, `POST ${DETAIL}/review`)).toEqual({});
    await waitFor(() => expect(calls.filter((c) => c.key === `GET ${DETAIL}`).length).toBe(2));
  });

  it("shows the server's refusal of Start review", async () => {
    mockApi({ ...reviewer(), [`POST ${DETAIL}/review`]: [{ status: 409, body: { detail: "It has already been picked up." } }] });
    await openDetail();
    fireEvent.click(screen.getByRole("button", { name: "Start review" }));
    expect(await screen.findByText("It has already been picked up.")).toBeInTheDocument();
  });

  it("approves with the requested experience by default, and sends the version read", async () => {
    const calls = mockApi({ ...reviewer(), [`POST ${DETAIL}/decision`]: [ok(APP)] });
    await openDetail();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    const popup = screen.getByRole("dialog", { name: "Approve Acme Partners" });
    expect(within(popup).getByRole("combobox", { name: /Experience to grant/ })).toHaveTextContent("White-label");
    fireEvent.click(within(popup).getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("Application approved.")).toBeInTheDocument();
    expect(bodyOf(calls, `POST ${DETAIL}/decision`)).toEqual({ decision: "approve", experience: "white_label", expected_version: 7 });
    await waitFor(() => expect(calls.filter((c) => c.key === `GET ${DETAIL}`).length).toBe(2));
  });

  it("lets the reviewer grant another experience", async () => {
    const calls = mockApi({ ...reviewer(), [`POST ${DETAIL}/decision`]: [ok(APP)] });
    await openDetail();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    const popup = screen.getByRole("dialog", { name: "Approve Acme Partners" });
    fireEvent.click(within(popup).getByRole("combobox", { name: /Experience to grant/ }));
    fireEvent.click(screen.getByRole("option", { name: "Affiliate" }));
    fireEvent.click(within(popup).getByRole("button", { name: "Approve" }));
    await screen.findByText("Application approved.");
    expect(bodyOf(calls, `POST ${DETAIL}/decision`)).toEqual({ decision: "approve", experience: "affiliate", expected_version: 7 });
  });

  it("allows no experience only when none was requested: the server would otherwise grant the requested one", async () => {
    const calls = mockApi({ ...reviewer({ ...APP, requested_experience: "" }), [`POST ${DETAIL}/decision`]: [ok(APP)] });
    await openDetail();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    const popup = screen.getByRole("dialog", { name: "Approve Acme Partners" });
    expect(within(popup).getByRole("combobox", { name: /Experience to grant/ })).toHaveTextContent("No new capabilities");
    fireEvent.click(within(popup).getByRole("button", { name: "Approve" }));
    await screen.findByText("Application approved.");
    expect(bodyOf(calls, `POST ${DETAIL}/decision`)).toEqual({ decision: "approve", expected_version: 7 });
  });

  it("does not offer no experience when one was requested", async () => {
    mockApi(reviewer());
    await openDetail();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    fireEvent.click(screen.getByRole("combobox", { name: /Experience to grant/ }));
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["Affiliate", "Reseller", "White-label"]);
  });

  it("keeps a refused decision in the dialog, in the server's words", async () => {
    mockApi({ ...reviewer(), [`POST ${DETAIL}/decision`]: [{ status: 400, body: { detail: "This application changed in the meantime. Reload and try again." } }] });
    await openDetail();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    const popup = screen.getByRole("dialog", { name: "Approve Acme Partners" });
    fireEvent.click(within(popup).getByRole("button", { name: "Approve" }));
    expect(await within(popup).findByRole("alert")).toHaveTextContent("This application changed in the meantime.");
  });

  it("requests corrections with a reason of 10 characters and the flagged documents", async () => {
    const calls = mockApi({ ...reviewer(), [`POST ${DETAIL}/decision`]: [ok(APP)] });
    await openDetail();
    fireEvent.click(screen.getByRole("button", { name: "Request corrections" }));
    const popup = screen.getByRole("dialog", { name: "Request corrections" });
    const send = within(popup).getByRole("button", { name: "Request corrections" });
    fireEvent.change(within(popup).getByLabelText("What needs correcting"), { target: { value: "too short" } });
    expect(send).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText("What needs correcting"), { target: { value: "  The tax card is unreadable.  " } });
    fireEvent.click(within(popup).getByRole("checkbox", { name: "Tax card" }));
    fireEvent.click(within(popup).getByRole("checkbox", { name: "Certificate of incorporation" }));
    fireEvent.click(within(popup).getByRole("checkbox", { name: "Certificate of incorporation" }));
    fireEvent.click(send);
    expect(await screen.findByText("Corrections requested. The partner has been told.")).toBeInTheDocument();
    expect(bodyOf(calls, `POST ${DETAIL}/decision`)).toEqual({
      decision: "request_correction",
      reason: "The tax card is unreadable.",
      flagged_requirements: ["tax_card"],
      expected_version: 7,
    });
  });

  it("rejects only after a reason and a confirmation", async () => {
    const calls = mockApi({ ...reviewer(), [`POST ${DETAIL}/decision`]: [ok(APP)] });
    await openDetail();
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    const popup = screen.getByRole("dialog", { name: "Reject this application" });
    expect(within(popup).getByRole("button", { name: "Continue" })).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText("Why it is rejected"), { target: { value: "The business could not be verified." } });
    fireEvent.click(within(popup).getByRole("button", { name: "Continue" }));
    const confirm = await screen.findByRole("dialog", { name: "Reject Acme Partners?" });
    expect(calls.some((c) => c.key === `POST ${DETAIL}/decision`)).toBe(false);
    fireEvent.click(within(confirm).getByRole("button", { name: "Reject application" }));
    expect(await screen.findByText("Application rejected. The partner has been told.")).toBeInTheDocument();
    expect(bodyOf(calls, `POST ${DETAIL}/decision`)).toEqual({ decision: "reject", reason: "The business could not be verified.", expected_version: 7 });
  });

  it("shows a refused rejection in its confirmation", async () => {
    mockApi({ ...reviewer(), [`POST ${DETAIL}/decision`]: [{ status: 403, body: { detail: "Your review grant has ended." } }] });
    await openDetail();
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    fireEvent.change(screen.getByLabelText("Why it is rejected"), { target: { value: "The business could not be verified." } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    const confirm = await screen.findByRole("dialog", { name: "Reject Acme Partners?" });
    fireEvent.click(within(confirm).getByRole("button", { name: "Reject application" }));
    expect(await within(confirm).findByRole("alert")).toHaveTextContent("Your review grant has ended.");
  });

  it("links to the partner's page only for someone who may read it", async () => {
    mockApi(reviewer());
    const { unmount } = await openDetail(REVIEW);
    expect(screen.queryByRole("link", { name: "Open this partner's page" })).not.toBeInTheDocument();
    unmount();
    mockApi(reviewer());
    await openDetail([...REVIEW, "platform.staff_partner.view"]);
    expect(screen.getByRole("link", { name: "Open this partner's page" })).toHaveAttribute("href", `/admin/partners/${PARTNER}`);
  });

  it("shows an error state and has an empty state for no details and no documents", async () => {
    mockApi({ [`GET ${DETAIL}`]: [{ status: 404, body: {} }] });
    const { unmount } = screenAs(VIEW, <KycApplicationScreen partnerId={PARTNER} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("You don't have access to this application.");
    unmount();

    mockApi(reviewer({ ...APP, details: {}, requirements: [], documents: [], missing: [], decisions: [], document_access: [] }));
    await openDetail();
    expect(screen.getByText("No business details yet")).toBeInTheDocument();
    expect(screen.getByText("No documents asked for")).toBeInTheDocument();
    expect(screen.getByText("No decisions yet")).toBeInTheDocument();
    expect(screen.getByText("Nobody has opened a document")).toBeInTheDocument();
  });
});

describe("KYC requirements", () => {
  it("lists what an application asks for, and passes axe", async () => {
    mockApi({ [`GET ${REQUIREMENTS}`]: [ok({ results: [REQUIREMENT_A, { ...REQUIREMENT_B, is_active: false }] })] });
    const { container } = screenAs(REVIEW, <KycRequirementsScreen />);
    const table = await screen.findByRole("table", { name: "KYC requirements" });
    const row = within(table).getByRole("row", { name: /Certificate of incorporation/ });
    expect(row).toHaveTextContent("A clear scan.");
    expect(row).toHaveTextContent("Required");
    expect(within(table).getByRole("row", { name: /Tax card/ })).toHaveTextContent("Inactive");
    await expectNoViolations(container);
  });

  it("explains what having none means", async () => {
    mockApi({ [`GET ${REQUIREMENTS}`]: [ok({ results: [] })] });
    screenAs(REVIEW, <KycRequirementsScreen />);
    expect(await screen.findByText(/an application is business details alone/)).toBeInTheDocument();
  });

  it("shows an error state", async () => {
    mockApi({ [`GET ${REQUIREMENTS}`]: [{ status: 500, body: { detail: "Requirements are unavailable." } }] });
    screenAs(REVIEW, <KycRequirementsScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Requirements are unavailable.");
  });

  it("adds one, with the exact body, and refreshes", async () => {
    const calls = mockApi({
      [`GET ${REQUIREMENTS}`]: [ok({ results: [] }), ok({ results: [REQUIREMENT_A] })],
      [`POST ${REQUIREMENTS}`]: [{ status: 201, body: REQUIREMENT_A }],
    });
    screenAs(REVIEW, <KycRequirementsScreen />);
    await screen.findByText("No KYC requirements");
    fireEvent.click(screen.getByRole("button", { name: "Add requirement" }));
    const popup = screen.getByRole("dialog", { name: "Add a requirement" });
    const add = within(popup).getByRole("button", { name: "Add requirement" });
    expect(add).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText("Label"), { target: { value: " Certificate " } });
    fireEvent.change(within(popup).getByLabelText(/^Help text/), { target: { value: "A clear scan." } });
    fireEvent.change(within(popup).getByLabelText("Order"), { target: { value: "abc" } });
    expect(add).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText("Order"), { target: { value: "5" } });
    fireEvent.click(within(popup).getByRole("checkbox", { name: "Required" }));
    fireEvent.click(add);
    expect(await screen.findByText("Requirement added.")).toBeInTheDocument();
    expect(bodyOf(calls, `POST ${REQUIREMENTS}`)).toEqual({ label: "Certificate", help_text: "A clear scan.", is_required: false, is_active: true, display_order: 5 });
    expect(await screen.findByRole("row", { name: /Certificate of incorporation/ })).toBeInTheDocument();
  });

  it("edits one, with the exact body", async () => {
    const calls = mockApi({
      [`GET ${REQUIREMENTS}`]: [ok({ results: [REQUIREMENT_A] })],
      [`PUT ${REQUIREMENTS}/r1`]: [ok(REQUIREMENT_A)],
    });
    screenAs(REVIEW, <KycRequirementsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit Certificate of incorporation" }));
    const popup = screen.getByRole("dialog", { name: "Edit Certificate of incorporation" });
    expect(within(popup).getByLabelText("Order")).toHaveValue("1");
    fireEvent.click(within(popup).getByRole("checkbox", { name: "Active" }));
    fireEvent.click(within(popup).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Requirement saved.")).toBeInTheDocument();
    expect(bodyOf(calls, `PUT ${REQUIREMENTS}/r1`)).toEqual({
      label: "Certificate of incorporation",
      help_text: "A clear scan.",
      is_required: true,
      is_active: false,
      display_order: 1,
    });
  });

  it("keeps a refusal in the form", async () => {
    mockApi({
      [`GET ${REQUIREMENTS}`]: [ok({ results: [] })],
      [`POST ${REQUIREMENTS}`]: [{ status: 400, body: { detail: "Order must be 0 to 999." } }],
    });
    screenAs(REVIEW, <KycRequirementsScreen />);
    await screen.findByText("No KYC requirements");
    fireEvent.click(screen.getByRole("button", { name: "Add requirement" }));
    const popup = screen.getByRole("dialog", { name: "Add a requirement" });
    fireEvent.change(within(popup).getByLabelText("Label"), { target: { value: "Certificate" } });
    fireEvent.click(within(popup).getByRole("button", { name: "Add requirement" }));
    expect(await within(popup).findByRole("alert")).toHaveTextContent("Order must be 0 to 999.");
    expect(within(popup).getByLabelText("Label")).toHaveValue("Certificate");
  });

  it("hides the write actions without the review grant", async () => {
    mockApi({ [`GET ${REQUIREMENTS}`]: [ok({ results: [REQUIREMENT_A] })] });
    screenAs(VIEW, <KycRequirementsScreen />);
    await screen.findByRole("table", { name: "KYC requirements" });
    expect(screen.queryByRole("button", { name: "Add requirement" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Edit/ })).not.toBeInTheDocument();
  });
});

describe("two-step activation and the malware scan (2026-11-10)", () => {
  const MANAGER = [...REVIEW, "platform.staff_partner.manage"];
  const approved = { ...APP, status: "approved", may_decide: false, partner_status: "draft", partner_version: 4 };

  it("approved but not active: an Admin who may manage partners activates it as a second step", async () => {
    const calls = mockApi({
      [`GET ${DETAIL}`]: [ok(approved), ok({ ...approved, partner_status: "active" })],
      [`POST /api/v1/admin/partners/${PARTNER}/status`]: [ok({ id: PARTNER, status: "active" })],
    });
    screenAs(MANAGER, <KycApplicationScreen partnerId={PARTNER} />);
    await screen.findByRole("heading", { level: 1, name: "Acme Partners" });
    expect(screen.getByText("Approved, not yet active")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Activate partner" }));
    const confirm = await screen.findByRole("dialog");
    fireEvent.click(within(confirm).getByRole("button", { name: "Activate partner" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST /api/v1/admin/partners/${PARTNER}/status`)).toBe(true));
    expect(bodyOf(calls, `POST /api/v1/admin/partners/${PARTNER}/status`)).toEqual({
      status: "active",
      reason: "Activated after KYC approval.",
      expected_version: 4,
    });
  });

  it("a reviewer without partner management sees who must activate it, and no button", async () => {
    mockApi({ [`GET ${DETAIL}`]: [ok(approved)] });
    screenAs(REVIEW, <KycApplicationScreen partnerId={PARTNER} />);
    await screen.findByRole("heading", { level: 1, name: "Acme Partners" });
    expect(screen.queryByRole("button", { name: "Activate partner" })).not.toBeInTheDocument();
    expect(screen.getByText(/Someone who may manage partners has to activate it/)).toBeInTheDocument();
  });

  it("names each document's scan, and offers no Open link until it is clean", async () => {
    mockApi(
      reviewer({
        ...APP,
        documents: [
          { id: "p1", requirement_key: "certificate", filename: "waiting.pdf", content_type: "application/pdf", size_bytes: 10, uploaded_at: T, removed_at: null, scan: "pending" },
          { id: "p2", requirement_key: "tax_card", filename: "clean.pdf", content_type: "application/pdf", size_bytes: 10, uploaded_at: T, removed_at: null, scan: "clean" },
          { id: "p3", requirement_key: "bank", filename: "bad.pdf", content_type: "application/pdf", size_bytes: 10, uploaded_at: T, removed_at: null, scan: "infected" },
        ],
      }),
    );
    await openDetail();
    expect(screen.getByText("Waiting for malware scan: cannot be opened yet")).toBeInTheDocument();
    expect(screen.getByText("Scanned: clean")).toBeInTheDocument();
    expect(screen.getByText("Threat found: content deleted")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open waiting.pdf" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open bad.pdf" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open clean.pdf" })).toBeInTheDocument();
  });

  it("the queue says how many approved partners wait for activation, and names the legacy ones", async () => {
    mockApi({ [`GET ${QUEUE}`]: [page([ROW], { awaiting_activation: 2, legacy_active: 3 })] });
    screenAs(VIEW, <KycApplicationsScreen />);
    expect(await screen.findByText("2 approved partners are waiting to be activated")).toBeInTheDocument();
    expect(screen.getByText("3 partners were active before KYC was required")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Approved Partners" })).toHaveAttribute("href", "/admin/partners/all?kyc=legacy");
  });
});
