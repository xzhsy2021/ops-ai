import { apiClient } from "../api/client";

export const taskService = {
  list: () => apiClient.get("/tasks"),
  detail: (id: string) => apiClient.get(`/tasks/${id}`),
  execute: (id: string) => apiClient.post(`/tasks/${id}/execute`),
  cancel: (id: string) => apiClient.post(`/tasks/${id}/cancel`),
};
