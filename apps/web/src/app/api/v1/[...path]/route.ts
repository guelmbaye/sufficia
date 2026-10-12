/* Same-origin proxy for the SUFFICIA API.

   The browser only ever calls /api/v1/* on this origin, so there is no CORS, no API URL in client
   bundles, and the optional API key stays on the server. This is a route handler rather than a
   next.config rewrite on purpose: rewrites are frozen into the build, while SUFFICIA_API_URL and
   SUFFICIA_API_KEY must be read at runtime so one image can point at any API. */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const FORWARD_REQUEST_HEADERS = ["content-type", "accept", "x-request-id"];
const DROP_RESPONSE_HEADERS = new Set(["content-encoding", "content-length", "transfer-encoding", "connection", "keep-alive"]);

function apiBase(): string {
  return (process.env.SUFFICIA_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
}

/* A hosted API on a free plan may be asleep: its first answer can take about a minute, and while it boots the
   hosting platform's gateway can answer 502/503/504 with an HTML page. Those answers (and connection failures)
   are retried until SUFFICIA_API_TIMEOUT_MS; an answer from the API itself (JSON) is always passed through. */
function envMs(name: string, fallback: number): number {
  const value = Number(process.env[name]);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

const GATEWAY_STATUSES = new Set([502, 503, 504]);

function isGatewayError(res: Response): boolean {
  return GATEWAY_STATUSES.has(res.status) && !(res.headers.get("content-type") ?? "").includes("application/json");
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function unreachable(): Response {
  return Response.json(
    { error: { code: "API_UNREACHABLE", message: "The SUFFICIA API is not answering yet." } },
    { status: 502, headers: { "cache-control": "no-store" } },
  );
}

async function proxy(req: Request, ctx: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const { path } = await ctx.params;
  const incoming = new URL(req.url);
  const target = `${apiBase()}/api/v1/${path.map(encodeURIComponent).join("/")}${incoming.search}`;

  const headers = new Headers();
  for (const name of FORWARD_REQUEST_HEADERS) {
    const value = req.headers.get(name);
    if (value) headers.set(name, value);
  }
  const key = process.env.SUFFICIA_API_KEY;
  if (key) headers.set("x-api-key", key);

  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  const body = hasBody ? await req.arrayBuffer() : undefined;
  const deadline = Date.now() + envMs("SUFFICIA_API_TIMEOUT_MS", 60_000);
  let delay = envMs("SUFFICIA_API_RETRY_MS", 1_500);
  const idempotent = req.method === "GET" || req.method === "HEAD";
  const attemptMs = envMs("SUFFICIA_API_ATTEMPT_MS", 15_000);
  let upstream: Response | null = null;
  for (;;) {
    const remaining = deadline - Date.now();
    if (remaining <= 0) return unreachable();
    try {
      upstream = await fetch(target, {
        method: req.method,
        headers,
        body,
        cache: "no-store",
        redirect: "manual",
        // A read can be retried safely, so it never waits on one stuck connection; a write waits for its answer.
        signal: AbortSignal.timeout(idempotent ? Math.min(remaining, attemptMs) : remaining),
      });
    } catch {
      upstream = null;
    }
    if (upstream && !isGatewayError(upstream)) break;
    await upstream?.body?.cancel().catch(() => undefined);
    if (Date.now() + delay >= deadline) return unreachable();
    await sleep(delay);
    delay = Math.min(delay * 2, 8_000);
  }

  const out = new Headers();
  upstream.headers.forEach((value, name) => {
    if (!DROP_RESPONSE_HEADERS.has(name.toLowerCase())) out.set(name, value);
  });
  out.set("cache-control", "no-store");
  return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers: out });
}

export { proxy as DELETE, proxy as GET, proxy as PATCH, proxy as POST, proxy as PUT };
