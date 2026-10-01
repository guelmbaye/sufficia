import Link from "next/link";

import type { ApiError } from "@/lib/api";

import { ErrorNote } from "./ErrorNote";

/** A screen whose record cannot be loaded: says which record, why, and where to go instead. */
export function LoadError({ error, what, id }: { error: ApiError; what: string; id: string }) {
  const missing = error.status === 404;
  return (
    <div className="mx-auto max-w-[900px] px-6 py-14">
      <h1 className="font-expanded text-3xl font-extrabold">{missing ? `${what} not found` : `${what} could not be loaded`}</h1>
      <p className="mt-3 text-lg text-ink-2">
        {missing ? (
          <>
            This API has no {what.toLowerCase()} with the id <span className="font-mono text-base">{id}</span>. It may belong to another
            database, or the link is incomplete.
          </>
        ) : (
          "The request did not complete. Nothing was changed."
        )}
      </p>
      {missing ? null : <ErrorNote error={error} className="mt-4" />}
      <Link href="/" className="mt-6 inline-block rounded-sm bg-ink px-4 py-2 text-sm font-semibold text-paper">
        Start from the mission
      </Link>
    </div>
  );
}
