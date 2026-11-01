import { type DependencyList, useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../services/http";

export interface AsyncState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
  setData: (value: T) => void;
}

/** Run `load` on mount and whenever `deps` change. Stale responses are ignored. */
export function useAsync<T>(load: () => Promise<T>, deps: DependencyList): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const run = useRef(0);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const exec = useCallback(load, deps);

  const reload = useCallback(() => {
    const id = ++run.current;
    setLoading(true);
    setError(null);
    exec().then(
      (value) => { if (id === run.current) { setData(value); setLoading(false); } },
      (err: unknown) => {
        if (id !== run.current) return;
        setError(err instanceof ApiError ? err : new ApiError(0, err instanceof Error ? err.message : "Unexpected error."));
        setLoading(false);
      },
    );
  }, [exec]);

  useEffect(() => { reload(); }, [reload]);

  return { data, error, loading, reload, setData };
}

/** Wrap a mutation: tracks `busy` and returns the result or throws the ApiError. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const act = useCallback(async <R,>(fn: () => Promise<R>): Promise<R> => {
    setBusy(true);
    try {
      return await fn();
    } finally {
      setBusy(false);
    }
  }, []);
  return { busy, act };
}
