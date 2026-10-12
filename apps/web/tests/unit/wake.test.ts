import { afterEach, describe, expect, it, vi } from "vitest";

import { GET } from "@/app/api/wake/route";
import { resetWake, wakeApi } from "@/lib/wake";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  resetWake();
});

describe("API wake-up", () => {
  it("publishes only an https API address", async () => {
    vi.stubEnv("SUFFICIA_API_URL", "https://sufficia-api.onrender.com/");
    expect(await GET().json()).toEqual({ url: "https://sufficia-api.onrender.com" });
    vi.stubEnv("SUFFICIA_API_URL", "http://127.0.0.1:8000");
    expect(await GET().json()).toEqual({ url: null });
    vi.stubEnv("SUFFICIA_API_PUBLIC_URL", "http://127.0.0.1:8900");
    expect(await GET().json()).toEqual({ url: "http://127.0.0.1:8900" });
  });

  it("pings the public /health from the browser once per page load, without reading the answer", async () => {
    vi.stubGlobal("window", {});
    const calls: [string, RequestInit | undefined][] = [];
    const fetcher = (async (url: string, init?: RequestInit) => {
      calls.push([url, init]);
      return url === "/api/wake" ? Response.json({ url: "https://api.example" }) : new Response(null, { status: 200 });
    }) as unknown as typeof fetch;
    await wakeApi(fetcher);
    await wakeApi(fetcher);
    expect(calls.map(([u]) => u)).toEqual(["/api/wake", "https://api.example/health"]);
    expect(calls[1]?.[1]?.mode).toBe("no-cors");
  });

  it("does nothing when no public address is published, and never throws", async () => {
    vi.stubGlobal("window", {});
    const seen: string[] = [];
    await wakeApi((async (url: string) => {
      seen.push(url);
      return Response.json({ url: null });
    }) as unknown as typeof fetch);
    expect(seen).toEqual(["/api/wake"]);
    resetWake();
    await expect(wakeApi((async () => Promise.reject(new TypeError("offline"))) as unknown as typeof fetch)).resolves.toBeUndefined();
  });
});
