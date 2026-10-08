import { useState, useEffect, useCallback } from "react";
import { apiJson } from "../lib/api";

export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(!!path);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    try {
      const res = await apiJson<T>(path);
      setData(res);
      setError(null);
    } catch (e: any) {
      setError(e.message ?? "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    refetch();
  }, [refetch]);

  return { data, loading, error, refetch };
}
