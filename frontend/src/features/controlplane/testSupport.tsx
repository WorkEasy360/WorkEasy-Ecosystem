import { expect, vi } from "vitest";
import axe, { type Result } from "axe-core";
import { AdminContext } from "./ControlShell";

/**
 * Helpers shared by the Phase 10 console tests (not imported by any screen).
 * The Phase 7/8 suites keep their own copies; these are the same shape.
 */

const RULESET = { runOnly: { type: "tag" as const, values: ["wcag2a", "wcag2aa"] } };

/** The same non-visual axe pass as every earlier phase. Contrast, focus
 * visibility and reflow are checked in a real browser - jsdom paints nothing. */
export async function expectNoViolations(container: HTMLElement) {
  const { violations } = await axe.run(container, RULESET);
  const detail = violations.map((v: Result) => `${v.id}: ${v.help}\n    ${v.nodes[0]?.html ?? ""}`).join("\n  ");
  expect(violations, detail).toEqual([]);
}

export type Reply = { status: number; body?: unknown };

export interface Call {
  key: string;
  url: string;
  body: unknown;
  headers: Record<string, string>;
}

/** Route `METHOD /path` to a queue of replies (the last one repeats). An
 * unrouted request throws, so a screen cannot silently call something the test
 * did not expect. */
export function mockApi(routes: Record<string, Reply[]>): Call[] {
  const calls: Call[] = [];
  global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const key = `${(init?.method ?? "GET").toUpperCase()} ${url.split("?")[0]}`;
    const raw = init?.body;
    calls.push({
      key,
      url,
      body: typeof raw === "string" ? JSON.parse(raw) : undefined,
      headers: Object.fromEntries(new Headers(init?.headers).entries()),
    });
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

export const SIGNED_IN: Reply = { status: 200, body: { authenticated: true, user_id: "u1", email: "s@example.com" } };

export function AdminContextForTest({ permissions, children }: { permissions: string[]; children: React.ReactNode }) {
  return <AdminContext.Provider value={{ permissions }}>{children}</AdminContext.Provider>;
}

export const ok = (body: unknown): Reply => ({ status: 200, body });
