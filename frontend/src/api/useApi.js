import { useCallback, useEffect, useState } from "react";
import { api } from "./client.js";

/** GET `path` on mount (and when `path` changes). Returns {data, error, loading, reload}. */
export function useApi(path) {
  const [state, setState] = useState({ data: null, error: null, loading: true });

  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      setState({ data: await api(path), error: null, loading: false });
    } catch (err) {
      setState({ data: null, error: err, loading: false });
    }
  }, [path]);

  useEffect(() => {
    load();
  }, [load]);

  return { ...state, reload: load };
}
