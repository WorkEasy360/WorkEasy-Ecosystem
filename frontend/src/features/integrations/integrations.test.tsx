import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import axe, { type Result } from "axe-core";
import { ConnectionsSettings } from "./ConnectionsSettings";
import { DeliveryLog } from "./DeliveryLog";
import { AutomationsSettings, parseValue } from "./AutomationsSettings";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useParams: () => ({ orgSlug: "acme" }),
  usePathname: () => "/o/acme/settings/integrations",
}));

type Reply = { status: number; body?: unknown };

/** Routes `fetch` by "METHOD path" to a queue of replies (last one repeats). */
function mockApi(routes: Record<string, Reply[]>) {
  const calls: { key: string; body: unknown }[] = [];
  global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const key = `${(init?.method ?? "GET").toUpperCase()} ${url.split("?")[0]}`;
    calls.push({ key, body: init?.body ? JSON.parse(String(init.body)) : undefined });
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
  const { violations } = await axe.run(container, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] },
  });
  const detail = violations.map((v: Result) => `${v.id}: ${v.help}`).join("\n");
  expect(violations, detail).toEqual([]);
}

const API = "/api/v1/orgs/acme";
const EMPTY_PAGE = { status: 200, body: { results: [], next_cursor: null, has_more: false } };
const CATALOG = {
  status: 200,
  body: {
    results: [
      {
        ref: "product.example.record.created.v1",
        event_type: "product.example.record.created",
        version: 1,
        product_code: "example",
        description: "A record was created.",
      },
    ],
  },
};
const CONNECTION = {
  id: "c1",
  connector_key: "webhook",
  display_name: "Orders",
  status: "draft",
  status_reason: "",
  revision: 1,
  destination: "https://hooks.example.com/…",
  health: { consecutive_failures: 0, circuit_open_until: null, last_success_at: null, last_failure_at: null },
  activated_at: null,
  created_at: "2026-09-23T10:00:00Z",
  updated_at: "2026-09-23T10:00:00Z",
};
const SECRET = "whsec_only-shown-once";

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});
afterEach(() => vi.restoreAllMocks());

describe("ConnectionsSettings", () => {
  it("shows a new signing secret once, stores it nowhere, and forgets it", async () => {
    mockApi({
      [`GET ${API}/connections`]: [EMPTY_PAGE, { status: 200, body: { results: [CONNECTION], next_cursor: null, has_more: false } }],
      [`GET ${API}/event-catalog`]: [CATALOG],
      [`POST ${API}/connections`]: [{ status: 201, body: { connection: CONNECTION, secret: SECRET, notice: "" } }],
    });
    render(<ConnectionsSettings orgSlug="acme" />);
    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Orders" } });
    fireEvent.change(screen.getByLabelText("Endpoint URL (https)"), {
      target: { value: "https://hooks.example.com/in" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create connection" }));

    expect(await screen.findByDisplayValue(SECRET)).toBeInTheDocument();
    expect(JSON.stringify({ ...localStorage })).not.toContain(SECRET);
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(SECRET);
    expect(window.location.href).not.toContain(SECRET);

    fireEvent.click(screen.getByRole("button", { name: "I've stored it" }));
    await waitFor(() => expect(screen.queryByDisplayValue(SECRET)).not.toBeInTheDocument());
    expect(document.body.innerHTML).not.toContain(SECRET);
  });

  it("runs the change at once and says so", async () => {
    const calls = mockApi({
      [`GET ${API}/connections`]: [{ status: 200, body: { results: [CONNECTION], next_cursor: null, has_more: false } }],
      [`GET ${API}/event-catalog`]: [CATALOG],
      [`POST ${API}/connections/c1/status`]: [{ status: 200, body: { ...CONNECTION, status: "active" } }],
    });
    render(<ConnectionsSettings orgSlug="acme" />);
    fireEvent.click(await screen.findByRole("button", { name: "Activate" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Connection activated.");
    expect(calls.filter((c) => c.key === `POST ${API}/connections/c1/status`)).toHaveLength(1);
  });

  it("is accessible, including the one-time secret panel", async () => {
    mockApi({
      [`GET ${API}/connections`]: [{ status: 200, body: { results: [CONNECTION], next_cursor: null, has_more: false } }],
      [`GET ${API}/event-catalog`]: [CATALOG],
      [`POST ${API}/connections/c1/secret`]: [
        { status: 201, body: { version: 2, secret: SECRET, previous_valid_until: "2026-09-24T10:00:00Z" } },
      ],
    });
    const { container } = render(
      <main>
        <h1>Integrations</h1>
        <ConnectionsSettings orgSlug="acme" />
      </main>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Rotate secret" }));
    await screen.findByDisplayValue(SECRET);
    await expectNoViolations(container);
  });

  it("sends one rotation for a double click", async () => {
    // A second rotation would end the first one's overlap at once and revoke
    // the secret the receiver is still using.
    const calls = mockApi({
      [`GET ${API}/connections`]: [{ status: 200, body: { results: [CONNECTION], next_cursor: null, has_more: false } }],
      [`GET ${API}/event-catalog`]: [CATALOG],
      [`POST ${API}/connections/c1/secret`]: [
        { status: 201, body: { version: 2, secret: SECRET, previous_valid_until: "2026-09-24T10:00:00Z" } },
      ],
    });
    render(<ConnectionsSettings orgSlug="acme" />);
    const rotate = await screen.findByRole("button", { name: "Rotate secret" });
    fireEvent.click(rotate);
    fireEvent.click(rotate);
    await screen.findByDisplayValue(SECRET);
    expect(calls.filter((c) => c.key === `POST ${API}/connections/c1/secret`)).toHaveLength(1);
  });
});

describe("DeliveryLog", () => {
  const row = (id: string, status: string, replayable: boolean) => ({
    id,
    status,
    origin: "subscription",
    connection_id: "c1",
    connection_name: "Orders",
    event: { event_id: "e1", event_type: "product.example.record.created", event_version: 1, source: "product.example" },
    attempts: status === "dead" ? 10 : 1,
    max_attempts: 10,
    next_attempt_at: null,
    last_attempt_at: null,
    last_http_status: status === "dead" ? 500 : 200,
    last_category: status === "dead" ? "http_5xx" : "http_2xx",
    dead_reason: status === "dead" ? "retries_exhausted" : "",
    replay_of: null,
    replayable,
    created_at: "2026-09-23T10:00:00Z",
  });

  it("offers replay only for dead deliveries and never shows a body or header", async () => {
    const calls = mockApi({
      [`GET ${API}/deliveries`]: [
        { status: 200, body: { results: [row("d1", "dead", true), row("d2", "succeeded", false)], next_cursor: null, has_more: false } },
      ],
      [`POST ${API}/deliveries/d1/replay`]: [{ status: 202, body: { delivery_id: "d3" } }],
    });
    const { container } = render(
      <main>
        <h1>Webhook deliveries</h1>
        <DeliveryLog orgSlug="acme" />
      </main>,
    );
    const buttons = await screen.findAllByRole("button", { name: /Replay delivery/ });
    expect(buttons).toHaveLength(1);
    await expectNoViolations(container);
    fireEvent.click(buttons[0]);
    expect(await screen.findByRole("status")).toHaveTextContent("Replay queued");
    expect(calls.some((c) => c.key === `POST ${API}/deliveries/d1/replay`)).toBe(true);
    expect(container.innerHTML.toLowerCase()).not.toContain("x-w360-signature");
  });
});

describe("AutomationsSettings", () => {
  it("builds a generic rule definition from the form", async () => {
    const calls = mockApi({
      [`GET ${API}/automations`]: [EMPTY_PAGE],
      [`GET ${API}/event-catalog`]: [CATALOG],
      [`GET ${API}/connections`]: [
        { status: 200, body: { results: [{ ...CONNECTION, status: "active" }], next_cursor: null, has_more: false } },
      ],
      [`POST ${API}/automations`]: [{ status: 201, body: {} }],
    });
    const { container } = render(
      <main>
        <h1>Automations</h1>
        <AutomationsSettings orgSlug="acme" />
      </main>,
    );
    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Big orders" } });
    fireEvent.click(screen.getByRole("button", { name: "Add condition" }));
    fireEvent.change(screen.getByLabelText("Field 1 (in the event data)"), { target: { value: "amount" } });
    fireEvent.change(screen.getByLabelText("Operator 1"), { target: { value: "greater_than" } });
    fireEvent.change(screen.getByLabelText("Value 1"), { target: { value: "100" } });
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Save automation" }));

    await waitFor(() => expect(calls.some((c) => c.key === `POST ${API}/automations`)).toBe(true));
    const sent = calls.find((c) => c.key === `POST ${API}/automations`)!.body as Record<string, unknown>;
    expect(sent).toEqual({
      name: "Big orders",
      definition: {
        trigger: { kind: "event", event: "product.example.record.created.v1" },
        conditions: { match: "all", rules: [{ path: "payload.amount", op: "greater_than", value: 100 }] },
        actions: [{ type: "connector.invoke", config: { connection_id: "c1" } }],
      },
    });
  });

  it("keeps condition values typed the way the server compares them", () => {
    expect(parseValue("100", "equals")).toBe(100);
    expect(parseValue("true", "equals")).toBe(true);
    expect(parseValue("open", "equals")).toBe("open");
    expect(parseValue("false", "exists")).toBe(false);
  });
});
