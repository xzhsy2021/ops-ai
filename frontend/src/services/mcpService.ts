import { apiClient } from "../api/client";

export const mcpService = {
  tools: () => apiClient.get("/mcp/tools"),
  invoke: (name: string, payload: unknown) =>
    apiClient.post(`/mcp/tools/${name}`, payload),
  history: () => apiClient.get("/mcp/history"),
};
