import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { PartnerShell } from "@/features/controlplane/ControlShell";
import { SIGNED_IN, expectNoViolations, mockApi, ok, type Call, type Reply } from "@/features/controlplane/testSupport";
import { PartnerKycScreen } from "./KycApplication";

/**
 * A partner's own KYC application (2026-11-09). It works for a draft partner, whose only
 * way in is the status notice; it reads only once sent, and only the partner's
 * administrators may change it or open its documents.
 */

const countries = vi.hoisted(() => [
  { value: "", label: "Choose a country" },
  { value: "IN", label: "India" },
  { value: "US", label: "United States" },
]);
// Every role query walks the real list's 250 options; which countries exist is pinned elsewhere.
vi.mock("@/features/controlplane/countries", async (original) => ({
  ...(await original<typeof import("@/features/controlplane/countries")>()),
  countryOptions: () => countries,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/partner/p/kyc",
}));

beforeEach(() => {
  vi.restoreAllMocks();
});

const PARTNER = "11111111-1111-4111-8111-111111111111";
const KYC = `/api/v1/partner/${PARTNER}/kyc`;
const DOCUMENTS = `${KYC}/documents`;
const SUBMIT = `${KYC}/submit`;
const T = "2026-11-01T10:00:00Z";

const CERTIFICATE = { id: "r1", key: "certificate", label: "Certificate", help_text: "A clear scan.", is_required: true, is_active: true, display_order: 1 };
const TAX_CARD = { id: "r2", key: "tax_card", label: "Tax card", help_text: "", is_required: false, is_active: true, display_order: 2 };
const DOCUMENT = { id: "d1", requirement_key: "certificate", filename: "cert.pdf", content_type: "application/pdf", size_bytes: 2048, uploaded_at: T };

const EMPTY_DETAILS = {
  legal_name: "",
  business_type: "",
  registration_number: "",
  tax_number: "",
  address_line: "",
  city: "",
  region: "",
  postal_code: "",
  country: "",
  website: "",
  phone: "",
};

const APP = {
  status: "draft",
  version: 3,
  details: {},
  requested_experience: "reseller",
  submitted_at: null,
  decided_at: null,
  decision_reason: "",
  correction: null,
  requirements: [CERTIFICATE, TAX_CARD],
  documents: [],
  editable: true,
  may_edit: true,
};

const READY = { ...APP, details: { ...EMPTY_DETAILS, legal_name: "Acme Pvt Ltd", country: "IN" }, documents: [DOCUMENT] };

function routes(app: unknown = APP, extra: Record<string, Reply[]> = {}): Record<string, Reply[]> {
  return { [`GET ${KYC}`]: [ok(app)], ...extra };
}

async function open() {
  const view = render(
    <main>
      <PartnerKycScreen partnerId={PARTNER} />
    </main>,
  );
  await screen.findByRole("heading", { name: "Business details" });
  return view;
}

const bodyOf = (calls: Call[], key: string) => calls.find((c) => c.key === key)?.body;
const pdf = (name = "new.pdf", type = "application/pdf", bytes = 10) => new File([new Uint8Array(bytes)], name, { type });

describe("a draft application", () => {
  it("shows the status, the form and one upload per requirement; passes axe", async () => {
    mockApi(routes());
    const { container } = await open();
    expect(screen.getByRole("heading", { level: 1, name: "Your partner application" })).toBeInTheDocument();
    expect(screen.getByText("Your application has not been sent yet")).toBeInTheDocument();
    expect(screen.getByText("KYC status: Draft")).toBeInTheDocument();
    expect(screen.getByText(/You asked to partner as: Reseller/)).toBeInTheDocument();
    for (const label of ["Business type", "Registration number", "Tax number", "Address", "City", "State or region", "Postal code", "Website", "Phone"]) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
    expect(screen.getByLabelText(/^Legal name/)).toBeRequired();
    expect(screen.getByLabelText(/^Country/)).toBeRequired();
    expect(screen.getByLabelText("Upload Certificate")).toHaveAttribute("accept", "application/pdf,image/png,image/jpeg");
    expect(screen.getByLabelText("Upload Tax card")).toBeInTheDocument();
    expect(screen.getByText("A clear scan.")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("saves the details with the exact body", async () => {
    const calls = mockApi(routes(APP, { [`PUT ${KYC}`]: [ok(READY)] }));
    await open();
    expect(screen.getByRole("button", { name: "Save details" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/^Legal name/), { target: { value: " Acme Pvt Ltd " } });
    fireEvent.change(screen.getByLabelText(/^Country/), { target: { value: "IN" } });
    fireEvent.click(screen.getByRole("button", { name: "Save details" }));
    expect(await screen.findByText("Your business details are saved.")).toBeInTheDocument();
    expect(bodyOf(calls, `PUT ${KYC}`)).toEqual({ details: { ...EMPTY_DETAILS, legal_name: "Acme Pvt Ltd", country: "IN" }, expected_version: 3 });
    await waitFor(() => expect(calls.filter((c) => c.key === `GET ${KYC}`).length).toBe(2));
  });

  it("shows the server's refusal of a save", async () => {
    mockApi(routes(APP, { [`PUT ${KYC}`]: [{ status: 400, body: { detail: "This application changed in the meantime. Reload and try again." } }] }));
    await open();
    fireEvent.change(screen.getByLabelText("City"), { target: { value: "Pune" } });
    fireEvent.click(screen.getByRole("button", { name: "Save details" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This application changed in the meantime.");
    expect(screen.getByLabelText("City")).toHaveValue("Pune");
  });

  it("uploads a document for its requirement, keeping what is being typed", async () => {
    const calls = mockApi(routes(APP, { [`POST ${DOCUMENTS}`]: [{ status: 201, body: DOCUMENT }] }));
    await open();
    fireEvent.change(screen.getByLabelText("City"), { target: { value: "Pune" } });
    fireEvent.change(screen.getByLabelText("Upload Certificate"), { target: { files: [pdf("my cert.pdf")] } });
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${DOCUMENTS}`)).toBe(true));
    const call = calls.find((c) => c.key === `POST ${DOCUMENTS}`)!;
    const url = new URL(call.url, "https://x.invalid");
    expect(url.searchParams.get("requirement")).toBe("certificate");
    expect(url.searchParams.get("filename")).toBe("my cert.pdf");
    expect(call.headers["content-type"]).toBe("application/pdf");
    await waitFor(() => expect(calls.filter((c) => c.key === `GET ${KYC}`).length).toBe(2));
    expect(screen.getByLabelText("City")).toHaveValue("Pune");
  });

  it("refuses a wrong type or an oversized file before sending, and shows the server's refusal", async () => {
    const calls = mockApi(routes(APP, { [`POST ${DOCUMENTS}`]: [{ status: 400, body: { detail: "That file could not be read." } }] }));
    await open();
    const input = screen.getByLabelText("Upload Certificate");
    fireEvent.change(input, { target: { files: [pdf("virus.exe", "application/x-msdownload")] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("A document must be a PDF, a PNG or a JPEG.");
    fireEvent.change(input, { target: { files: [pdf("big.pdf", "application/pdf", 10 * 1024 * 1024 + 1)] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("A document must be at most 10 MB.");
    expect(calls.some((c) => c.key === `POST ${DOCUMENTS}`)).toBe(false);
    fireEvent.change(input, { target: { files: [pdf()] } });
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("That file could not be read."));
  });

  it("shows the current file, opens it through its own address, and can replace it", async () => {
    mockApi(routes(READY));
    await open();
    const row = screen.getByText("cert.pdf").closest("li")!;
    const openLink = within(row).getByRole("link", { name: "Open cert.pdf" });
    expect(openLink).toHaveAttribute("href", `${DOCUMENTS}/d1/content`);
    expect(openLink).toHaveAttribute("target", "_blank");
    expect(openLink).toHaveAttribute("rel", "noopener");
    expect(within(row).getByLabelText("Replace Certificate")).toBeInTheDocument();
  });

  it("removes a document after confirming", async () => {
    const calls = mockApi(routes(READY, { [`DELETE ${DOCUMENTS}/d1`]: [ok({ ok: true })] }));
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Remove cert.pdf" }));
    const confirm = screen.getByRole("dialog", { name: "Remove cert.pdf?" });
    expect(calls.some((c) => c.key === `DELETE ${DOCUMENTS}/d1`)).toBe(false);
    fireEvent.click(within(confirm).getByRole("button", { name: "Remove document" }));
    await waitFor(() => expect(calls.filter((c) => c.key === `GET ${KYC}`).length).toBe(2));
    expect(calls.some((c) => c.key === `DELETE ${DOCUMENTS}/d1`)).toBe(true);
  });

  it("lists what is missing and holds the submit button until nothing is", async () => {
    mockApi(routes());
    await open();
    const needed = screen.getByRole("list", { name: "Still needed" });
    expect(within(needed).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Legal name", "Country", "Certificate"]);
    expect(screen.getByRole("button", { name: "Submit application" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/^Legal name/), { target: { value: "Acme" } });
    expect(within(needed).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Country", "Certificate"]);
  });

  it("submits what is saved", async () => {
    const calls = mockApi(routes(READY, { [`POST ${SUBMIT}`]: [ok({ ...READY, status: "submitted", version: 4, editable: false })] }));
    await open();
    expect(screen.queryByRole("list", { name: "Still needed" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Submit application" }));
    expect(await screen.findByText("Your application has been sent.")).toBeInTheDocument();
    expect(bodyOf(calls, `POST ${SUBMIT}`)).toEqual({});
    expect(calls.some((c) => c.key === `PUT ${KYC}`)).toBe(false);
  });

  it("saves unsaved details first, so what is typed goes with the application", async () => {
    const calls = mockApi(routes(READY, { [`PUT ${KYC}`]: [ok(READY)], [`POST ${SUBMIT}`]: [ok(READY)] }));
    await open();
    fireEvent.change(screen.getByLabelText("City"), { target: { value: "Pune" } });
    expect(screen.getByText("Your unsaved details are saved first.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Submit application" }));
    await screen.findByText("Your application has been sent.");
    const order = calls.map((c) => c.key).filter((k) => k === `PUT ${KYC}` || k === `POST ${SUBMIT}`);
    expect(order).toEqual([`PUT ${KYC}`, `POST ${SUBMIT}`]);
    expect(bodyOf(calls, `PUT ${KYC}`)).toMatchObject({ details: { city: "Pune" }, expected_version: 3 });
  });

  it("shows the server's refusal of a submit, and reads again after a save", async () => {
    const calls = mockApi(routes(READY, { [`PUT ${KYC}`]: [ok(READY)], [`POST ${SUBMIT}`]: [{ status: 400, body: { detail: "Still needed: Bank letter." } }] }));
    await open();
    fireEvent.change(screen.getByLabelText("City"), { target: { value: "Pune" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit application" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Still needed: Bank letter.");
    await waitFor(() => expect(calls.filter((c) => c.key === `GET ${KYC}`).length).toBe(2));
  });

  it("says when no documents are asked for", async () => {
    mockApi(routes({ ...APP, requirements: [] }));
    await open();
    expect(screen.getByText(/your business details are the whole application/)).toBeInTheDocument();
  });
});

describe("a sent-back application", () => {
  const SENT_BACK = {
    ...READY,
    status: "correction_required",
    decision_reason: "The certificate is cut off.",
    correction: { reason: "The certificate is cut off.", flagged_requirements: ["certificate"], decided_at: T },
  };

  it("shows the reason, highlights the flagged document and can be edited; passes axe", async () => {
    mockApi(routes(SENT_BACK));
    const { container } = await open();
    expect(screen.getByText("Corrections are needed")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("The certificate is cut off.");
    const list = screen.getByRole("list", { name: "Documents to provide" });
    expect(within(list).getByText("Certificate").closest("li")).toHaveTextContent("Needs correction");
    expect(within(list).getByText("Tax card").closest("li")).not.toHaveTextContent("Needs correction");
    expect(screen.getByLabelText("Replace Certificate")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit application" })).toBeEnabled();
    await expectNoViolations(container);
  });
});

describe("once sent", () => {
  const SENT = { ...READY, status: "submitted", submitted_at: T, editable: false };

  it("reads only", async () => {
    mockApi(routes(SENT));
    const { container } = await open();
    expect(screen.getByText("Your application has been sent")).toBeInTheDocument();
    expect(screen.getByText("Acme Pvt Ltd")).toBeInTheDocument();
    expect(screen.getByText("India")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save details" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Submit application" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Remove/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/^Replace/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open cert.pdf" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("says why it was not approved", async () => {
    mockApi(routes({ ...SENT, status: "rejected", decided_at: T, decision_reason: "We could not verify the business." }));
    await open();
    expect(screen.getByText("Your application was not approved")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("We could not verify the business.");
  });

  it("says when it was approved", async () => {
    mockApi(routes({ ...SENT, status: "approved", decided_at: T }));
    await open();
    expect(screen.getByText("Your application is approved")).toBeInTheDocument();
    expect(screen.getByText(/Approved on 1 Nov 2026/)).toBeInTheDocument();
  });
});

describe("someone who is not an administrator", () => {
  it("reads only, and can't open documents", async () => {
    mockApi(routes({ ...READY, may_edit: false }));
    await open();
    expect(screen.getByText(/Only this partner's administrators can change this application/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save details" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Submit application" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open cert.pdf" })).not.toBeInTheDocument();
    expect(screen.getByText("cert.pdf")).toBeInTheDocument();
    expect(screen.queryByLabelText(/^Replace/)).not.toBeInTheDocument();
  });
});

describe("when it can't be read", () => {
  it("shows an error state", async () => {
    mockApi({ [`GET ${KYC}`]: [{ status: 500, body: { detail: "Applications are unavailable." } }] });
    render(<PartnerKycScreen partnerId={PARTNER} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Applications are unavailable.");
  });

  it("says when there is no access", async () => {
    mockApi({ [`GET ${KYC}`]: [{ status: 404, body: {} }] });
    render(<PartnerKycScreen partnerId={PARTNER} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("You don't have access to your application.");
  });
});

describe("the way in", () => {
  const portal = (status: string) =>
    ok({
      partners: [
        {
          partner_id: PARTNER,
          code: "acme",
          display_name: "Acme Partners",
          status,
          role: "owner",
          is_admin: true,
          capabilities: [],
          permissions: [],
        },
      ],
    });

  const shell = () =>
    render(
      <PartnerShell partnerId={PARTNER}>
        <h1>Page</h1>
      </PartnerShell>,
    );

  it("is a link in the draft partner's status notice", async () => {
    mockApi({ "GET /api/v1/auth/session": [SIGNED_IN], "GET /api/v1/partner/portal": [portal("draft")] });
    const { container } = shell();
    await screen.findByRole("heading", { name: "Page" });
    expect(screen.getByText(/This partner account isn't active yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Complete your application" })).toHaveAttribute("href", `/partner/${PARTNER}/kyc`);
    await expectNoViolations(container);
  });

  it("is not offered to any other status", async () => {
    mockApi({ "GET /api/v1/auth/session": [SIGNED_IN], "GET /api/v1/partner/portal": [portal("suspended")] });
    const { unmount } = shell();
    await screen.findByRole("heading", { name: "Page" });
    expect(screen.getByText(/This partner account is suspended/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Complete your application" })).not.toBeInTheDocument();
    unmount();

    mockApi({ "GET /api/v1/auth/session": [SIGNED_IN], "GET /api/v1/partner/portal": [portal("active")] });
    shell();
    await screen.findByRole("heading", { name: "Page" });
    expect(screen.queryByRole("link", { name: "Complete your application" })).not.toBeInTheDocument();
  });
});
