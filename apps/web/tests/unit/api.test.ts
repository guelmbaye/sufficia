import { describe, expect, it } from "vitest";

import { ApiError, createClient } from "@/lib/api";

const respond = (status: number, body: unknown) =>
  (async () => new Response(typeof body === "string" ? body : JSON.stringify(body), { status })) as unknown as typeof fetch;

describe("API client", () => {
  it("keeps the machine code of API errors", async () => {
    const api = createClient("/api/v1", respond(403, { error: { code: "EVIDENCE_AUTHORIZATION_REQUIRED", message: "Decision is DENY", detail: { decision_state: "DENY" } } }));
    await expect(api.executeDecision("dec_1")).rejects.toMatchObject({ status: 403, code: "EVIDENCE_AUTHORIZATION_REQUIRED", detail: { decision_state: "DENY" } });
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
    const api = createClient("/api/v1", respond(502, "<html>bad gateway</html>"));
    await expect(api.system()).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
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
