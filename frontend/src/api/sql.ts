import { maintenance } from '../api'
import type { SqlConnectionItem, SqlHistoryItem, SqlPreviewResult, SqlQueryResult } from '../types/sql'
function unwrap<T>(res: any): T { return (res?.data ?? res) as T }
export const sqlApi = {
  async connections(): Promise<SqlConnectionItem[]> { return unwrap<SqlConnectionItem[]>(await maintenance.connections.list()) },
  async preview(connectionId: string, payload: Record<string, unknown>): Promise<SqlPreviewResult> { return unwrap<SqlPreviewResult>(await maintenance.query.preview(connectionId, payload)) },
  async execute(connectionId: string, payload: Record<string, unknown>): Promise<SqlQueryResult> { return unwrap<SqlQueryResult>(await maintenance.query.execute(connectionId, payload)) },
  async history(connectionId?: string, limit = 30, offset = 0): Promise<SqlHistoryItem[]> { const payload = unwrap<any>(await maintenance.query.history(connectionId, limit, offset)); return Array.isArray(payload) ? payload : (payload.items || []) },
}
