import { defineConfig, devices } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

/* End-to-end tests against the real stack: the FastAPI service (in-process EVM running the real
   EvidenceGate bytecode, fresh SQLite database) and the production Next.js server that proxies it. */
const API_PORT = Number(process.env.E2E_API_PORT ?? 8100);
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 3100);
const apiDir = resolve(__dirname, "../api");
const db = join(mkdtempSync(join(tmpdir(), "sufficia-e2e-")), "e2e.db");
const python = process.env.PYTHON ?? "python3";

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
  projects: [{ name: "chromium", use: { viewport: { width: 1440, height: 900 } } }],
  webServer: [
    {
      command: `${python} -m uvicorn sufficia.api.app:app --host 127.0.0.1 --port ${API_PORT} --log-level warning`,
      cwd: apiDir,
      url: `http://127.0.0.1:${API_PORT}/health`,
      env: {
        PYTHONPATH: apiDir,
        DATABASE_URL: `sqlite:///${db}`,
        CHAIN_MODE: "LOCAL_EVM",
        DEMO_MODE: "true",
        EVIDENCE_PROFILE: "demo",
        INTENT_COMPILER: "deterministic",
        ANTHROPIC_API_KEY: "",
        API_KEY: "",
        LOG_LEVEL: "WARNING",
        RATE_LIMIT_PER_MINUTE: "0",
      },
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npx next start --hostname 127.0.0.1 --port ${WEB_PORT}`,
      url: `http://127.0.0.1:${WEB_PORT}/`,
      env: { SUFFICIA_API_URL: `http://127.0.0.1:${API_PORT}`, NEXT_TELEMETRY_DISABLED: "1" },
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
