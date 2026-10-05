import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup, configure } from "@testing-library/react";

// Explicit, not relying on @testing-library/react's global-detection
// auto-cleanup: this project does not set vitest's `test.globals`, so
// `afterEach` is not implicitly global and the library's own auto-cleanup
// silently never registers - leaving one test's DOM in place for the next.
afterEach(() => {
  cleanup();
});

// The default is one second per query. With every test file running in
// parallel on one machine, an async screen (a fetch, a state update, an axe
// pass) sometimes takes longer than that, and the test fails on a query that
// would have succeeded a moment later - a different test each run. A screen
// that really never renders still fails, at five seconds.
configure({ asyncUtilTimeout: 5000 });
