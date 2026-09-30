import { afterEach, describe, expect, it, vi } from "vitest";

import { GET, POST } from "@/app/api/v1/[...path]/route";

const ctx = (...path: string[]) => ({ params: Promise.resolve({ path }) });

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("API proxy route", () => {
  it("forwards method, path, query and body to SUFFICIA_API_URL read at request time", async () => {
    vi.stubEnv("SUFFICIA_API_URL", "http://api.internal:9000/");
    vi.stubEnv("SUFFICIA_API_KEY", "k-123");
    const fetchMock = vi.fn(async () =>
      new Response('{"ok":true}', { status: 201, headers: { "content-type": "application/json", "content-encoding": "gzip", "x-request-id": "r1" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const req = new Request("http://web/api/v1/intents?x=1", {
      method: "POST",
      headers: { "content-type": "application/json", cookie: "secret=1" },
      body: JSON.stringify({ text: "hi" }),
    });
    const res = await POST(req, ctx("intents"));

    expect(res.status).toBe(201);
    expect(await res.json()).toEqual({ ok: true });
    expect(res.headers.get("content-encoding")).toBeNull();
    expect(res.headers.get("x-request-id")).toBe("r1");
    expect(res.headers.get("cache-control")).toBe("no-store");

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://api.internal:9000/api/v1/intents?x=1");
    expect(init.method).toBe("POST");
    const headers = init.headers as Headers;
    expect(headers.get("x-api-key")).toBe("k-123");
    expect(headers.get("content-type")).toBe("application/json");
    expect(headers.get("cookie")).toBeNull();
    expect(new TextDecoder().decode(init.body as ArrayBuffer)).toBe('{"text":"hi"}');
  });

  it("encodes path segments and sends no body for GET", async () => {
    vi.stubEnv("SUFFICIA_API_URL", "http://api");
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await GET(new Request("http://web/api/v1/receipts/a%2Fb"), ctx("receipts", "a/b"));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://api/api/v1/receipts/a%2Fb");
    expect(init.body).toBeUndefined();
  });

  it("answers 502 API_UNREACHABLE when the API is down", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("fetch failed"))));
    const res = await GET(new Request("http://web/api/v1/system"), ctx("system"));
    expect(res.status).toBe(502);
    expect(((await res.json()) as { error: { code: string } }).error.code).toBe("API_UNREACHABLE");
  });
});
