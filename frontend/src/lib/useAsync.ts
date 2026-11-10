import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { useAuth } from "../state/app";

export interface AsyncState<T> {
  data: T | null;
  error: ApiError | Error | null;
  loading: boolean;
  reload: () => void;
  /** replace data locally (optimistic updates) */
  setData: (updater: (prev: T | null) => T | null) => void;
}

/**
 * Load data when `key` changes (or the API key changes). `loader` is read from a ref, so callers
 * may pass an inline closure; encode everything it depends on in `key`. A null key means idle.
 */
export function useAsync<T>(key: string | null, loader: (signal: AbortSignal) => Promise<T>): AsyncState<T> {
  const { version } = useAuth();
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const [state, setState] = useState<{ data: T | null; error: Error | null; loading: boolean; key: string | null }>({
    data: null,
    error: null,
    loading: key !== null,
    key,
  });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (key === null) {
      setState({ data: null, error: null, loading: false, key });
      return;
    }
    const ctl = new AbortController();
    // keep previous data of the same key while reloading (no flicker); reset on key change
    setState((s) => (s.key === key ? { ...s, loading: true, error: null } : { data: null, error: null, loading: true, key }));
    loaderRef
      .current(ctl.signal)
      .then((data) => {
        if (!ctl.signal.aborted) setState({ data, error: null, loading: false, key });
      })
      .catch((e: unknown) => {
        if (ctl.signal.aborted) return;
        setState((s) => ({ data: s.key === key ? s.data : null, error: e instanceof Error ? e : new Error(String(e)), loading: false, key }));
      });
    return () => ctl.abort();
  }, [key, tick, version]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  const setData = useCallback((updater: (prev: T | null) => T | null) => setState((s) => ({ ...s, data: updater(s.data) })), []);
  return { data: state.key === key ? state.data : null, error: state.key === key ? state.error : null, loading: state.key !== key || state.loading, reload, setData };
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}
