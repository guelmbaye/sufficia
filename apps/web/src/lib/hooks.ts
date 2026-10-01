"use client";

import { useEffect, useRef, useState } from "react";

import { api, type ApiError, toApiError } from "./api";
import type { SystemInfo } from "./types";

/** Milliseconds left until `expiresAt`, ticking every 250 ms; null when there is no deadline. */
export function useCountdown(expiresAt: string | null | undefined): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!expiresAt) return;
    const t = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(t);
  }, [expiresAt]);
  if (!expiresAt) return null;
  return new Date(expiresAt).getTime() - now;
}

/** True once `iso` is in the past; flips exactly once, without re-rendering on every tick. */
export function useDeadlinePassed(iso: string | null | undefined): boolean {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!iso) return;
    const left = new Date(iso).getTime() - Date.now();
    if (left <= 0) return;
    const t = window.setTimeout(() => setNow(Date.now()), left + 50);
    return () => window.clearTimeout(t);
  }, [iso]);
  return Boolean(iso) && new Date(iso as string).getTime() <= now;
}

let systemCache: Promise<SystemInfo> | null = null;

/** System info (chain mode, network), fetched once per page load. */
export function useSystem(): { system: SystemInfo | null; error: ApiError | null } {
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  useEffect(() => {
    let alive = true;
    systemCache ??= api.system();
    systemCache
      .then((s) => alive && setSystem(s))
      .catch((e: unknown) => {
        systemCache = null;
        if (alive) setError(toApiError(e));
      });
    return () => {
      alive = false;
    };
  }, []);
  return { system, error };
}

/** Runs an async loader whenever `key` changes. `data` is the latest settled result for the current
    key only, so a page never shows one record's data under another record's id. */
export function useLoad<T>(key: string | null, loader: () => Promise<T>): { data: T | null; error: ApiError | null } {
  const [settled, setSettled] = useState<{ key: string; data: T | null; error: ApiError | null } | null>(null);
  const loaderRef = useRef(loader);
  useEffect(() => {
    loaderRef.current = loader;
  });

  useEffect(() => {
    if (!key) return;
    let alive = true;
    loaderRef
      .current()
      .then((data) => alive && setSettled({ key, data, error: null }))
      .catch((e: unknown) => alive && setSettled({ key, data: null, error: toApiError(e) }));
    return () => {
      alive = false;
    };
  }, [key]);

  const current = settled && settled.key === key ? settled : null;
  return { data: current?.data ?? null, error: current?.error ?? null };
}
