import { apiClient } from "../api/client";

export const inspectionService = {
  metrics: () => apiClient.get("/inspection/metrics"),
  alerts: () => apiClient.get("/inspection/alerts"),
  diagnosis: (id: string) => apiClient.get(`/inspection/diagnosis/${id}`),
};
