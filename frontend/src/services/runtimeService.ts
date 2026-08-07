import { apiClient } from "../api/client";

export async function getRuntimeStatus() {
  const { data } = await apiClient.get("/runtime/status");
  return data;
}
