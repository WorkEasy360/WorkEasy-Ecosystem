import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * A form's default method is GET. If its script has not loaded (a slow network, a blocked
 * chunk, a deploy in progress), pressing the button sends every field as part of the address,
 * where a password lands in the browser history, proxies and server logs. So a form that
 * carries a password says POST, whatever its submit handler does.
 */

const ROOT = join(process.cwd(), "src");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.tsx$/.test(name) && !/\.test\.tsx$/.test(name) ? [path] : [];
  });
}

const CARRIES_A_PASSWORD = /type=\{?["'`]?password|PasswordField|autoComplete="(?:current|new)-password"/;

describe("a form that carries a password", () => {
  const files = sources(ROOT).filter((path) => CARRIES_A_PASSWORD.test(readFileSync(path, "utf8")));

  it("finds the forms it is about", () => {
    expect(files.some((path) => path.endsWith("AdminLogin.tsx"))).toBe(true);
    expect(files.some((path) => path.endsWith("LoginForm.tsx"))).toBe(true);
  });

  it.each(files.map((path) => [path.slice(ROOT.length + 1), path]))("%s posts it", (_name, path) => {
    const source = readFileSync(path, "utf8");
    for (const match of source.matchAll(/<form\b[^>]*>/g)) {
      expect(match[0]).toContain('method="post"');
    }
  });
});
