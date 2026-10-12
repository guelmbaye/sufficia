/* The public address of the API, for the browser to wake it (src/lib/wake.ts).

   On Render's free plan a sleeping API is woken by requests from the internet, but requests from the web
   service (inside Render) get an immediate 502 and do not wake it. The browser therefore pings the API's
   public /health once per page load. Only an https address is published: a local or private API URL never
   leaves the server. SUFFICIA_API_PUBLIC_URL overrides it (tests, or an API behind another public name). */

export const dynamic = "force-dynamic";

export function GET(): Response {
  const configured = (process.env.SUFFICIA_API_PUBLIC_URL ?? process.env.SUFFICIA_API_URL ?? "").replace(/\/+$/, "");
  const url = process.env.SUFFICIA_API_PUBLIC_URL ? configured : /^https:\/\//.test(configured) ? configured : null;
  return Response.json({ url }, { headers: { "cache-control": "no-store" } });
}
