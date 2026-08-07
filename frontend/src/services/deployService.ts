import { apiClient } from "../api/client";

export const deployService = {
  createRelease: (payload: unknown) => apiClient.post("/deploy/releases", payload),
  status: (id: string) => apiClient.get(`/deploy/releases/${id}`),
  rollback: (id: string) => apiClient.post(`/deploy/releases/${id}/rollback`),
};
