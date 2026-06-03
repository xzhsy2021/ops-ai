import type { Pagination } from './common'

export interface DeploymentRecord {
  id: string
  system: string
  service?: string
  environment?: string
  strategy?: string
  status: string
  servers?: string
  version?: string
  message?: string
  started_at?: string
  finished_at?: string
  created_by?: string
  can_rollback?: boolean
}

export interface DeploymentHistoryFilters {
  q?: string
  system?: string
  service?: string
  environment?: string
  status?: string
  created_by?: string
  limit?: number
  offset?: number
}

export interface DeploymentHistoryPayload {
  items: DeploymentRecord[]
  pagination: Pagination
  filters?: DeploymentHistoryFilters
}

export interface DeploymentLogEntry {
  level: string
  message: string
  step_name?: string
  created_at?: string
  task_id?: string
}

export interface DeploymentReportSummary {
  app?: string
  system?: string
  service?: string
  environment?: string
  version?: string
  status?: string
  duration_seconds?: number | null
  servers?: string[]
  operator?: string
  rollback_available?: boolean
}

export interface DeploymentFailureItem {
  server_name?: string
  step_name?: string
  step_type?: string
  status?: string
  message?: string
}

export interface DeploymentReport {
  id: string
  system?: string
  service?: string
  environment?: string
  status?: string
  version?: string
  servers?: string
  message?: string
  created_by?: string
  duration_seconds?: number | null
  rollback_available?: boolean
  summary?: DeploymentReportSummary
  summary_text?: string
  failure_analysis?: Record<string, unknown>
  failed_servers?: DeploymentFailureItem[]
  failed_steps?: DeploymentFailureItem[]
  suggestions?: string[]
  log_summary?: Record<string, unknown>
  server_tasks?: DeploymentFailureItem[]
  step_tasks?: DeploymentFailureItem[]
  distributions?: Array<Record<string, unknown>>
}
