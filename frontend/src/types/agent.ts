export type AgentStatus =
  | "idle"
  | "planning"
  | "executing"
  | "verifying"
  | "completed"
  | "failed";

export interface AgentRun {
  id: string;
  type: string;
  goal: string;
  status: AgentStatus;
}
