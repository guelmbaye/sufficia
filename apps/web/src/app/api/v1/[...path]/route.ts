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

/* A hosted API on a free plan may be asleep: its first answer can take about a minute. */
function apiTimeoutMs(): number {
  const value = Number(process.env.SUFFICIA_API_TIMEOUT_MS);
  return Number.isFinite(value) && value > 0 ? value : 60_000;
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
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body: hasBody ? await req.arrayBuffer() : undefined,
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(apiTimeoutMs()),
    });
  } catch {
    return Response.json(
      { error: { code: "API_UNREACHABLE", message: "The SUFFICIA API cannot be reached from the web server." } },
      { status: 502, headers: { "cache-control": "no-store" } },
    );
  }

  const out = new Headers();
  upstream.headers.forEach((value, name) => {
    if (!DROP_RESPONSE_HEADERS.has(name.toLowerCase())) out.set(name, value);
  });
  out.set("cache-control", "no-store");
  return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers: out });
}

export { proxy as DELETE, proxy as GET, proxy as PATCH, proxy as POST, proxy as PUT };
