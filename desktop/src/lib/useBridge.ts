// Read-only bridge queries for inspection views. They refetch when the engine appends events (eventTick),
// so what is shown is always the engine's current record, never a value the UI derived.
import { useCallback, useEffect, useRef, useState } from "react";

import { useApp } from "../store/app";
import { BridgeError, call } from "./bridge";

export interface Query<T> {
  data: T | undefined;
  error: BridgeError | undefined;
  loading: boolean;
}

/**
 * `params === null` skips the call. `live` refetches on every event burst for the open project; `version` refetches
 * when it changes without being sent (for example the commit a file view belongs to).
 */
export function useBridge<T>(
  method: string,
  params: Record<string, unknown> | null,
  { live = true, version = "" }: { live?: boolean; version?: string } = {},
): Query<T> {
  const tick = useApp((s) => (live ? s.eventTick : 0));
  const key = params === null ? null : JSON.stringify([params, version]);
  const [state, setState] = useState<Query<T>>({ data: undefined, error: undefined, loading: key !== null });
  const latest = useRef(0);
  const lastKey = useRef<string | null>(null);

  const run = useCallback(async () => {
    if (key === null) return;
    const id = ++latest.current;
    const fresh = lastKey.current !== key;
    lastKey.current = key;
    // A new subject clears the old data; a refresh of the same subject keeps it on screen while loading.
    setState((s) => ({ data: fresh ? undefined : s.data, error: undefined, loading: true }));
    try {
      const [sent] = JSON.parse(key) as [Record<string, unknown>, string];
      const data = await call<T>(method, sent);
      if (id === latest.current) setState({ data, error: undefined, loading: false });
    } catch (err) {
      if (id === latest.current) {
        setState((s) => ({ data: fresh ? undefined : s.data, error: err as BridgeError, loading: false }));
      }
    }
  }, [method, key]);

  useEffect(() => {
    void run();
  }, [run, tick]);

  return state;
}
