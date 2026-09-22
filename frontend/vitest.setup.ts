import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Explicit, not relying on @testing-library/react's global-detection
// auto-cleanup: this project does not set vitest's `test.globals`, so
// `afterEach` is not implicitly global and the library's own auto-cleanup
// silently never registers - leaving one test's DOM in place for the next.
afterEach(() => {
  cleanup();
});
