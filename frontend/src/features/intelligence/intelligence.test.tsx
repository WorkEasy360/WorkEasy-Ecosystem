import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import axe, { type Result } from "axe-core";
import { AiSettings } from "./AiSettings";
import { AnalyticsShell, windowFor } from "./AnalyticsShell";
import { AdminAi } from "@/features/controlplane/AdminAi";
import { AdminContext } from "@/features/controlplane/ControlShell";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useParams: () => ({ orgSlug: "acme" }),
  usePathname: () => "/o/acme/settings/ai",
}));

type Reply = { status: number; body?: unknown };

function mockApi(routes: Record<string, Reply[]>) {
  const calls: { key: string; url: string; body: unknown }[] = [];
  global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const key = `${(init?.method ?? "GET").toUpperCase()} ${url.split("?")[0]}`;
    calls.push({ key, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const queue = routes[key];
    if (!queue) throw new Error(`unexpected request ${key}`);
    const reply = queue.length > 1 ? queue.shift()! : queue[0];
    return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), {
      status: reply.status,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
  return calls;
}

async function expectNoViolations(container: HTMLElement) {
  const { violations } = await axe.run(container, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] } });
  const detail = violations.map((v: Result) => `${v.id}: ${v.help}`).join("\n");
  expect(violations, detail).toEqual([]);
}

const API = "/api/v1/orgs/acme";
const SPEND = { spent_micros: 1_250_000, reserved_micros: 0, requests: 3, input_tokens: 900, output_tokens: 300, embedding_tokens: 40 };
const USAGE = {
  status: 200,
  body: {
    currency: "USD",
    period_start: "2026-09-01",
    resets_at: "2026-10-01T00:00:00+00:00",
    status: "enabled",
    organization: { ...SPEND, limit_micros: 50_000_000 },
    products: [{ ...SPEND, product_code: "example", ai_entitled: true, limit_micros: 5_000_000 }],
    note: "Estimated AI infrastructure cost, for spend control. It is not an invoice and is not billed.",
  },
};
const POLICY = {
  ai_enabled: true,
  monthly_budget_micros: null,
  allowed_providers: [],
  allow_provider_fallback: false,
  platform_suspended: false,
  platform_cap_micros: 50_000_000,
  organization_cap_micros: 50_000_000,
  premium_models_allowed: false,
  revision: 1,
};
const SETTINGS = {
  status: 200,
  body: {
    policy: POLICY,
    providers: [
      { key: "anthropic", display_name: "Anthropic", status: "active", data_region: "", retains_inputs_days: null, trains_on_inputs: false, data_policy_note: "Not used for training." },
      { key: "openai", display_name: "OpenAI", status: "active", data_region: "us", retains_inputs_days: 30, trains_on_inputs: false, data_policy_note: "" },
    ],
  },
};
const REQUESTS = {
  status: 200,
  body: {
    results: [
      {
        request_id: "r1",
        product_code: "example",
        operation: "invoke",
        skill: "product.example.summary",
        skill_version: 1,
        status: "succeeded",
        outcome: "ok",
        model: "anthropic.claude-opus-5",
        provider: "anthropic",
        fallback_used: false,
        input_tokens: 300,
        output_tokens: 100,
        embedding_tokens: 10,
        cost_micros: 4_100,
        created_at: "2026-09-25T10:00:00+00:00",
      },
    ],
    next_cursor: null,
    has_more: false,
  },
};

describe("AI settings", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows status, limits and usage as metadata, accessibly", async () => {
    mockApi({ [`GET ${API}/ai/usage`]: [USAGE], [`GET ${API}/ai/settings`]: [SETTINGS], [`GET ${API}/ai/requests`]: [REQUESTS] });
    const { container } = render(<AiSettings orgSlug="acme" />);
    expect(await screen.findByText("AI is on")).toBeInTheDocument();
    expect(screen.getByText(/not an invoice/i)).toBeInTheDocument();
    expect(screen.getByText("product.example.summary")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /usage by product/i })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("describes each provider's data handling and what deletion cannot reach", async () => {
    mockApi({ [`GET ${API}/ai/usage`]: [USAGE], [`GET ${API}/ai/settings`]: [SETTINGS], [`GET ${API}/ai/requests`]: [REQUESTS] });
    render(<AiSettings orgSlug="acme" />);
    expect(await screen.findByLabelText(/OpenAI.*region us; keeps inputs 30 days; does not train on inputs/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Anthropic.*input retention not stated.*Not used for training\./)).toBeInTheDocument();
    expect(screen.getByText(/a provider may keep what it already received/i)).toBeInTheDocument();
  });

  it("saves the settings with the revision it read", async () => {
    const calls = mockApi({
      [`GET ${API}/ai/usage`]: [USAGE],
      [`GET ${API}/ai/settings`]: [SETTINGS],
      [`GET ${API}/ai/requests`]: [REQUESTS],
      [`PATCH ${API}/ai/settings`]: [{ status: 200, body: { policy: { ...POLICY, ai_enabled: false, revision: 2 } } }],
    });
    render(<AiSettings orgSlug="acme" />);
    fireEvent.click(await screen.findByLabelText(/allow products to use ai/i));
    fireEvent.change(screen.getByLabelText(/monthly ai budget/i), { target: { value: "12.5" } });
    fireEvent.click(screen.getByLabelText(/^OpenAI/));
    fireEvent.click(screen.getByRole("button", { name: /save ai settings/i }));
    await screen.findByText("AI settings saved.");
    const patch = calls.find((c) => c.key === `PATCH ${API}/ai/settings`);
    expect(patch?.body).toEqual({
      revision: 1,
      ai_enabled: false,
      monthly_budget_micros: 12_500_000,
      allowed_providers: ["openai"],
      allow_provider_fallback: false,
    });
  });

  it("explains a stale edit instead of overwriting", async () => {
    mockApi({
      [`GET ${API}/ai/usage`]: [USAGE],
      [`GET ${API}/ai/settings`]: [SETTINGS],
      [`GET ${API}/ai/requests`]: [REQUESTS],
      [`PATCH ${API}/ai/settings`]: [{ status: 409, body: { code: "ai_request_conflict" } }],
    });
    render(<AiSettings orgSlug="acme" />);
    fireEvent.click(await screen.findByRole("button", { name: /save ai settings/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/changed since you opened them/i);
  });

  it("says so when the member may not see AI settings", async () => {
    mockApi({
      [`GET ${API}/ai/usage`]: [{ status: 403, body: { code: "permission_denied" } }],
      [`GET ${API}/ai/settings`]: [SETTINGS],
      [`GET ${API}/ai/requests`]: [REQUESTS],
    });
    render(<AiSettings orgSlug="acme" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/permission/i);
  });
});

const METRICS = {
  status: 200,
  body: {
    results: [
      {
        key: "product.example.record_value",
        version: 1,
        owner: "example",
        status: "active",
        description: "Value of records created",
        unit: "currency",
        aggregation: "sum",
        value_type: "money",
        event: { type: "product.example.record.created", version: 1 },
        dimensions: [{ name: "status", values: ["open", "won"] }],
        shared_with: [],
      },
    ],
  },
};

describe("Analytics shell", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("aligns every window the way the API requires", () => {
    for (const g of ["hour", "day", "month"] as const) {
      const { start, end } = windowFor(g);
      expect(start.endsWith("T00:00:00.000Z")).toBe(true);
      expect(end.endsWith("T00:00:00.000Z")).toBe(true);
      expect(new Date(end).getTime()).toBeGreaterThan(new Date(start).getTime());
    }
    expect(new Date(windowFor("month").start).getUTCDate()).toBe(1);
  });

  it("runs one bounded query and says how fresh it is", async () => {
    const calls = mockApi({
      [`GET ${API}/analytics/metrics`]: [METRICS],
      [`GET ${API}/analytics/query`]: [
        {
          status: 200,
          body: {
            metric: { key: "product.example.record_value", version: 1, unit: "currency", aggregation: "sum", value_type: "money" },
            granularity: "day",
            start: "2026-08-27T00:00:00+00:00",
            end: "2026-09-26T00:00:00+00:00",
            group_by: ["status"],
            results: [{ bucket: "2026-09-25T00:00:00+00:00", dimensions: { status: "won" }, count: 2, amount_minor: 1500, currency: "GBP" }],
            freshness: { pending_facts: 3, oldest_pending_since: "2026-09-25T10:00:00+00:00", last_projected_at: null, up_to_date: false },
          },
        },
      ],
    });
    const { container } = render(<AnalyticsShell orgSlug="acme" />);
    fireEvent.change(await screen.findByLabelText("Group by"), { target: { value: "status" } });
    fireEvent.click(screen.getByRole("button", { name: /run query/i }));
    expect(await screen.findByText(/3 recent event\(s\) not yet included/)).toBeInTheDocument();
    expect(screen.getByText("won")).toBeInTheDocument();
    expect(screen.getByText(/15\.00/)).toBeInTheDocument();
    const query = calls.find((c) => c.key === `GET ${API}/analytics/query`)!;
    const params = new URL(query.url, "http://x").searchParams;
    expect(params.get("metric")).toBe("product.example.record_value");
    expect(params.get("group_by")).toBe("status");
    expect(params.get("granularity")).toBe("day");
    await expectNoViolations(container);
  });

  it("is honest when there is nothing to show", async () => {
    mockApi({ [`GET ${API}/analytics/metrics`]: [{ status: 200, body: { results: [] } }] });
    render(<AnalyticsShell orgSlug="acme" />);
    expect(await screen.findByText(/publish metrics yet/)).toBeInTheDocument();
  });
});

const REGISTRY = {
  status: 200,
  body: {
    providers: [
      { key: "anthropic", display_name: "Anthropic", adapter: "anthropic_messages", status: "active", credential_configured: true, data_region: "", retains_inputs_days: null, trains_on_inputs: false, data_policy_note: "" },
      { key: "openai", display_name: "OpenAI", adapter: "openai", status: "active", credential_configured: false, data_region: "", retains_inputs_days: null, trains_on_inputs: false, data_policy_note: "" },
    ],
    models: [
      {
        key: "anthropic.claude-opus-5",
        provider: "anthropic",
        model_id: "claude-opus-5",
        status: "active",
        tier: "standard",
        capabilities: ["text", "structured_output"],
        context_window_tokens: 1_000_000,
        max_output_tokens: 16_000,
        embedding_dimensions: null,
        priority: 10,
        circuit_open_until: null,
        price: { version: 1, currency: "USD", input_micros_per_mtok: 5_000_000, output_micros_per_mtok: 25_000_000 },
      },
    ],
  },
};

function AsStaff({ permissions, children }: { permissions: string[]; children: React.ReactNode }) {
  return <AdminContext.Provider value={{ permissions }}>{children}</AdminContext.Provider>;
}

describe("Super Admin AI", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows whether a credential is configured, never a value, and no actions without manage", async () => {
    mockApi({ ["GET /api/v1/admin/ai/registry"]: [REGISTRY] });
    const { container } = render(
      <AsStaff permissions={["platform.staff_ai.view"]}>
        <AdminAi />
      </AsStaff>,
    );
    expect(await screen.findByText("Configured")).toBeInTheDocument();
    expect(screen.getByText("Not configured")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /disable/i })).not.toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("asks for an audited reason before switching a provider off", async () => {
    const calls = mockApi({
      ["GET /api/v1/admin/ai/registry"]: [REGISTRY],
      ["POST /api/v1/admin/ai/providers/anthropic/status"]: [REGISTRY],
    });
    render(
      <AsStaff permissions={["platform.staff_ai.view", "platform.staff_ai.manage"]}>
        <AdminAi />
      </AsStaff>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Disable Anthropic" }));
    fireEvent.change(screen.getByLabelText(/reason for/i), { target: { value: "Vendor incident INC-42" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(calls.find((c) => c.key === "POST /api/v1/admin/ai/providers/anthropic/status")?.body).toEqual({
        status: "disabled",
        reason: "Vendor incident INC-42",
      }),
    );
  });
});
