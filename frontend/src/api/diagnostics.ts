import { systemHealth } from '../api'
import type { DiagnosticsPayload, RecentErrorsPayload } from '../types/diagnostics'

function unwrap<T>(res: any): T {
  return (res?.data ?? res) as T
}

export const diagnosticsApi = {
  async get(): Promise<DiagnosticsPayload> {
    return unwrap<DiagnosticsPayload>(await systemHealth.diagnostics())
  },
  async report(): Promise<any> {
    return unwrap<any>(await systemHealth.diagnosticsReport())
  },
  async recentErrors(limit = 20): Promise<RecentErrorsPayload> {
    return unwrap<RecentErrorsPayload>(await systemHealth.recentErrors({ limit, include_warnings: true }))
  },
  exportUrl(): string {
    return systemHealth.diagnosticsExportUrl()
  },
  reportExportUrl(): string {
    return systemHealth.diagnosticsReportExportUrl()
  },
}
