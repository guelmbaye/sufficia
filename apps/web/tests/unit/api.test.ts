import { describe, expect, it } from "vitest";

import { ApiError, createClient } from "@/lib/api";

const respond = (status: number, body: unknown) =>
  (async () => new Response(typeof body === "string" ? body : JSON.stringify(body), { status })) as unknown as typeof fetch;

describe("API client", () => {
  it("keeps the machine code of API errors", async () => {
    const api = createClient("/api/v1", respond(403, { error: { code: "EVIDENCE_AUTHORIZATION_REQUIRED", message: "Decision is DENY", detail: { decision_state: "DENY" } } }));
    await expect(api.authorize("dec_1")).rejects.toMatchObject({ status: 403, code: "EVIDENCE_AUTHORIZATION_REQUIRED", detail: { decision_state: "DENY" } });
  });
  it("reports an unreachable API explicitly", async () => {
    const api = createClient("/api/v1", (async () => {
      throw new TypeError("fetch failed");
    }) as unknown as typeof fetch);
    const err = await api.system().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("API_UNREACHABLE");
  });
  it("rejects non-JSON responses", async () => {
    const api = createClient("/api/v1", respond(200, "<html>not the API</html>"));
    await expect(api.system()).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
  });
  it("retries a gateway error page, then reports the API as unreachable", async () => {
    let calls = 0;
    const fetcher = (async () => {
      calls += 1;
      return new Response("<html>bad gateway</html>", { status: 502 });
    }) as unknown as typeof fetch;
    const api = createClient("/api/v1", fetcher, { gatewayRetries: 2, retryDelayMs: 0 });
    await expect(api.system()).rejects.toMatchObject({ status: 502, code: "API_UNREACHABLE" });
    expect(calls).toBe(3);
  });
  it("recovers when the API answers after a gateway error page", async () => {
    const answers = [new Response("<html>bad gateway</html>", { status: 503 }), new Response('{"version":"1.0.0"}', { status: 200 })];
    const api = createClient("/api/v1", (async () => answers.shift()) as unknown as typeof fetch, { retryDelayMs: 0 });
    await expect(api.system()).resolves.toMatchObject({ version: "1.0.0" });
  });
  it("builds exact paths with encoded ids", async () => {
    let seen = "";
    const api = createClient("/api/v1", (async (url: string) => {
      seen = url;
      return new Response("{}", { status: 200 });
    }) as unknown as typeof fetch);
    await api.testCondition("ec/1", "stale");
    expect(seen).toBe("/api/v1/demo/contracts/ec%2F1/test/stale");
  });
});
