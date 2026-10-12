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

  it("answers 502 API_UNREACHABLE when the API stays down until the timeout", async () => {
    vi.stubEnv("SUFFICIA_API_TIMEOUT_MS", "60");
    vi.stubEnv("SUFFICIA_API_RETRY_MS", "10");
    const fetchMock = vi.fn(async () => Promise.reject(new TypeError("fetch failed")));
    vi.stubGlobal("fetch", fetchMock);
    const res = await GET(new Request("http://web/api/v1/system"), ctx("system"));
    expect(res.status).toBe(502);
    expect(((await res.json()) as { error: { code: string } }).error.code).toBe("API_UNREACHABLE");
    expect(fetchMock.mock.calls.length).toBeGreaterThan(1);
  });

  it("retries a hosting gateway's error page while the API wakes up, then passes the answer through", async () => {
    vi.stubEnv("SUFFICIA_API_TIMEOUT_MS", "5000");
    vi.stubEnv("SUFFICIA_API_RETRY_MS", "1");
    const answers = [
      new Response("<html>502 Bad Gateway</html>", { status: 502, headers: { "content-type": "text/html" } }),
      new Response('{"passed":true}', { status: 200, headers: { "content-type": "application/json" } }),
    ];
    const fetchMock = vi.fn(async () => answers.shift() as Response);
    vi.stubGlobal("fetch", fetchMock);
    const req = new Request("http://web/api/v1/demo/scenarios/grand-prize-v1/run", { method: "POST", body: '{"stop_before":"market"}' });
    const res = await POST(req, ctx("demo", "scenarios", "grand-prize-v1", "run"));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ passed: true });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const second = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(new TextDecoder().decode(second[1].body as ArrayBuffer)).toBe('{"stop_before":"market"}');
  });

  it("gives up on a stuck read after SUFFICIA_API_ATTEMPT_MS and tries again", async () => {
    vi.stubEnv("SUFFICIA_API_TIMEOUT_MS", "5000");
    vi.stubEnv("SUFFICIA_API_ATTEMPT_MS", "30");
    vi.stubEnv("SUFFICIA_API_RETRY_MS", "1");
    let calls = 0;
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      calls += 1;
      if (calls === 1) {
        return new Promise<Response>((_resolve, reject) => init.signal?.addEventListener("abort", () => reject(new Error("aborted"))));
      }
      return new Response('{"version":"1.0.0"}', { status: 200, headers: { "content-type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    const res = await GET(new Request("http://web/api/v1/system"), ctx("system"));
    expect(res.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("passes the API's own JSON errors through without retrying", async () => {
    vi.stubEnv("SUFFICIA_API_RETRY_MS", "1");
    const fetchMock = vi.fn(
      async () => new Response('{"error":{"code":"CHAIN_UNAVAILABLE"}}', { status: 503, headers: { "content-type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const res = await GET(new Request("http://web/api/v1/system"), ctx("system"));
    expect(res.status).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
