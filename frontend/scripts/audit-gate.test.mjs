// @vitest-environment node
import { describe, expect, it } from "vitest";
import { ACCEPTED, judge } from "./audit-gate.mjs";

function report(...advisories) {
  return {
    vulnerabilities: Object.fromEntries(
      advisories.map(([id, name, severity]) => [
        name,
        {
          via: [
            {
              url: `https://github.com/advisories/${id}`,
              name,
              range: "<=9.9.9",
              severity,
              title: "test advisory",
            },
            "another-package",
          ],
        },
      ]),
    ),
  };
}

describe("the dependency gate", () => {
  it("passes a clean audit", () => {
    expect(judge({ vulnerabilities: {} })).toEqual({
      failed: false,
      lines: ["No unaccepted advisories at moderate or above."],
    });
  });

  it("fails any advisory that is not accepted, at moderate and above", () => {
    expect(judge(report(["GHSA-xxxx-xxxx-xxxx", "leftpad", "moderate"])).failed).toBe(true);
    expect(judge(report(["GHSA-xxxx-xxxx-xxxx", "leftpad", "low"])).failed).toBe(false);
  });

  it("accepts the picomatch advisory only until its review date", () => {
    const picomatch = report(["GHSA-fc5q-39xc-vjpf", "picomatch", "high"]);
    expect(ACCEPTED.get("GHSA-fc5q-39xc-vjpf").reviewBy).toBe("2026-12-18");
    expect(judge(picomatch, new Date("2026-12-18T12:00:00Z")).failed).toBe(false);
    const after = judge(picomatch, new Date("2026-12-19T00:00:01Z"));
    expect(after.failed).toBe(true);
    expect(after.lines[0]).toMatch(/^acceptance expired on 2026-12-18/);
  });

  it("does not let an accepted advisory cover a different one", () => {
    const both = report(
      ["GHSA-fc5q-39xc-vjpf", "picomatch", "high"],
      ["GHSA-zzzz-zzzz-zzzz", "picomatch", "high"],
    );
    expect(judge(both, new Date("2026-11-18T00:00:00Z")).failed).toBe(true);
  });

  it("fails an audit that did not run", () => {
    expect(judge({ error: { code: "ENOLOCK" } }).failed).toBe(true);
  });
});
