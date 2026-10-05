import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    // Most screens are exercised with an axe pass over a populated DOM, which
    // takes seconds when every test file runs in parallel on one machine. A
    // genuinely stuck test still fails - at twenty seconds instead of five.
    testTimeout: 20_000,
    include: ["src/**/*.test.{ts,tsx}", "scripts/**/*.test.mjs"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
