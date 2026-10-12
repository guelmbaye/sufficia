/* Wakes a sleeping hosted API from the visitor's browser (see src/app/api/wake/route.ts). Fire and forget,
   once per page load: the answer is opaque (no-cors) and never read; the same-origin proxy does the real work. */

let started = false;

export function wakeApi(fetcher: typeof fetch = (...a) => fetch(...a)): Promise<void> {
  if (started || typeof window === "undefined") return Promise.resolve();
  started = true;
  return fetcher("/api/wake", { cache: "no-store" })
    .then((r) => r.json() as Promise<{ url: string | null }>)
    .then(async ({ url }) => {
      if (url) await fetcher(`${url}/health`, { mode: "no-cors", cache: "no-store" });
    })
    .catch(() => undefined);
}

/** Tests only. */
export function resetWake(): void {
  started = false;
}
