import { useEffect, useState } from "react";
import { getRuntimeStatus } from "../services/runtimeService";

export function useRuntime() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getRuntimeStatus()
      .then(setData)
      .finally(() => setLoading(false));
  }, []);

  return { data, loading };
}
