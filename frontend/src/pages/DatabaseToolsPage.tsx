import { useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent, CSSProperties } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { adminMaintenance, dbTools, maintenance, serverManagement } from '../api'
import { ConfirmDialog, DataTable, Skeleton, PageHeader } from '../components/ui'
import { HighRiskFlow } from '../components/HighRiskFlow'
import SqlQueryPage from './SqlQueryPage'
import type { SharedDatabaseContext } from './SqlQueryPage'
import CleanupJobDetail from './maintenance/CleanupJobDetail'
import ConnectionsTab from './maintenance/ConnectionsTab'
import CleanupJobsTab from './maintenance/CleanupJobsTab'
import { defaultConnForm, defaultJobForm, useMaintenanceData } from './maintenance/useMaintenanceData'
import { useCleanupJobActions } from './maintenance/useCleanupJobActions'
import { useConnectionFilters } from './maintenance/useConnectionFilters'
import { useAuthStore } from '../store'
import { ROUTES } from '../routes'

function pickData<T = any>(res: any, fallback: T): T {
  if (!res) return fallback
  if (res.data !== undefined) return res.data as T
  if (res.items !== undefined) return res.items as T
  if (res.servers !== undefined) return res.servers as T
  return res as T
}

function errorMessage(error: unknown, fallback: string) {
  if (typeof error === 'string') return error
  if (error instanceof Error && error.message) return error.message
  return fallback
}

const FORMAT_OPTIONS = [
  { value: 'csv', label: 'CSV' },
  { value: 'json', label: 'JSON' },
  { value: 'xlsx', label: 'Excel XLSX' },
  { value: 'md', label: 'Markdown Table' },
  { value: 'sql_query', label: 'SQL Query 文件' },
  ]

type DatabaseTab = 'query' | 'execute' | 'local' | 'connections' | 'cleanup'

const DATABASE_TABS: Array<{ key: DatabaseTab; label: string; hint: string }> = [
  { key: 'query', label: '业务库查询', hint: 'SELECT / WITH 只读查询' },
  { key: 'execute', label: 'SQL 执行', hint: 'UPDATE / DELETE / INSERT 受控执行' },
  { key: 'connections', label: '连接配置', hint: '数据库与 SSH 跳板机' },
  { key: 'cleanup', label: '数据清理', hint: 'Dry Run / 复核 / 分批执行' },
  { key: 'local', label: '本地 OPS 库', hint: '系统库查询、导出与维护' },
]

function normalizeTab(value: string | null): DatabaseTab {
  const tab = String(value || '').toLowerCase()
  if (['sql', 'query', 'external'].includes(tab)) return 'query'
  if (['execute', 'exec', 'write', 'sql-execute'].includes(tab)) return 'execute'
  if (['connections', 'connection', 'config'].includes(tab)) return 'connections'
  if (['cleanup', 'jobs', 'data-cleanup'].includes(tab)) return 'cleanup'
  if (['local', 'export', 'exports', 'ops'].includes(tab)) return 'local'
  return 'query'
}

function LocalOpsQueryPanel() {
  const [sql, setSql] = useState('SELECT name, host, port, user FROM servers LIMIT 50')
  const [limit, setLimit] = useState(100)
  const [format, setFormat] = useState('csv')
  const [filenameHint, setFilenameHint] = useState('ops-query-result')
  const [tables, setTables] = useState<any[]>([])
  const [result, setResult] = useState<any>(null)
  const [exportsList, setExportsList] = useState<any[]>([])
  const [exportsTotal, setExportsTotal] = useState(0)
  const [exportOffset, setExportOffset] = useState(0)
  const [exportPageSize, setExportPageSize] = useState(10)
  const [selectedExportIds, setSelectedExportIds] = useState<string[]>([])
  const [batchDeleteExportsOpen, setBatchDeleteExportsOpen] = useState(false)
  const [deleteExportTarget, setDeleteExportTarget] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')

  const rows: any[] = result?.rows || []
  const columns: string[] = result?.columns || []
  const maskedColumns = result?.sensitive_columns_masked || []
  const tableCount = useMemo(() => tables.length, [tables])
  const exportId = (item: any) => item.export_id || item.id

  const loadTables = async () => {
    try {
      const res = await dbTools.tables()
      const data = pickData<any>(res, {})
      setTables(data.tables || [])
    } catch (e) {
      setMessage(errorMessage(e, '加载数据库表失败'))
    }
  }

  const loadExports = async () => {
    try {
      const res = await dbTools.exports({ limit: exportPageSize, offset: exportOffset })
      const data = pickData<any>(res, {})
      const nextItems = data.items || []
      setExportsList(nextItems)
      setExportsTotal(Number(data.total || nextItems.length || 0))
      const visibleIds = new Set(nextItems.map((item: any) => exportId(item)))
      setSelectedExportIds((prev) => prev.filter((id) => visibleIds.has(id)))
    } catch (_) {}
  }

  useEffect(() => {
    loadTables()
  }, [])

  useEffect(() => {
    loadExports()
  }, [exportOffset, exportPageSize])

  const runQuery = async () => {
    setLoading(true)
    setMessage('')
    try {
      const res = await dbTools.query({ sql, limit })
      setResult(pickData<any>(res, null))
    } catch (e) {
      setMessage(errorMessage(e, '只读查询失败'))
    } finally {
      setLoading(false)
    }
  }

  const exportQuery = async () => {
    setLoading(true)
    setMessage('')
    try {
      const res = await dbTools.exportQuery({ sql, limit, format, filename_hint: filenameHint })
      const data = pickData<any>(res, {})
      const exportItem = data.export
      await loadExports()
      if (exportItem?.export_id || exportItem?.id) {
        window.open(dbTools.downloadUrl(exportItem.export_id || exportItem.id), '_blank')
      }
    } catch (e) {
      setMessage(errorMessage(e, '导出失败'))
    } finally {
      setLoading(false)
    }
  }

  const deleteExport = async () => {
    if (!deleteExportTarget) return
    setLoading(true)
    setMessage('')
    try {
      await dbTools.deleteExports({ export_ids: [exportId(deleteExportTarget)] })
      setDeleteExportTarget(null)
      await loadExports()
    } catch (e) {
      setMessage(errorMessage(e, '删除导出失败'))
    } finally {
      setLoading(false)
    }
  }

  function toggleExportSelection(id: string, checked: boolean) {
    setSelectedExportIds((prev) => checked ? Array.from(new Set([...prev, id])) : prev.filter((x) => x !== id))
  }

  const deleteSelectedExports = async (ids = selectedExportIds) => {
    if (!ids.length) return
    setLoading(true)
    setMessage('')
    try {
      await dbTools.deleteExports({ export_ids: ids })
      setBatchDeleteExportsOpen(false)
      setSelectedExportIds([])
      await loadExports()
    } catch (e) {
      setMessage(errorMessage(e, '批量删除导出失败'))
    } finally {
      setLoading(false)
    }
  }

  const exportPage = Math.floor(exportOffset / exportPageSize) + 1
  const exportPages = Math.max(1, Math.ceil(exportsTotal / exportPageSize))

  return (
    <div className="database-tab-panel">
      <section className="glass-panel sql-hero sql-hero--compact">
        <div>
          <span className="eyebrow">Local OPS DB · Read-only</span>
          <h1>本地 OPS 数据库查询与多格式导出</h1>
          <p>面向 AI/MCP 的安全只读查询入口。只允许 SELECT/WITH，自动限制行数、脱敏敏感字段，并把导出制品写入报告中心。</p>
        </div>
        <div className="sql-hero-metrics">
          <div className="metric-pill"><span>{tableCount}</span><small>本地表</small></div>
          <div className="metric-pill"><span>{limit}</span><small>查询上限</small></div>
          <div className="metric-pill metric-pill--safe"><span>{format.toUpperCase()}</span><small>导出格式</small></div>
        </div>
      </section>

      {message && <div className="alert-card alert-card--danger"><strong>操作失败</strong><span>{message}</span></div>}

      <div className="sql-layout">
        <main className="sql-main-column">
          <section className="glass-card sql-editor-card">
            <div className="section-title-row">
              <div>
                <h2>只读 SQL</h2>
                <p>默认查询 OPS 本地库。业务数据库请切换到“业务库查询”。</p>
              </div>
              <span className="status-badge status-badge--success">SELECT ONLY</span>
            </div>
            <div className="sql-snippet-row">
              <button className="sql-snippet-btn" onClick={() => setSql('SELECT name, host, port, user FROM servers LIMIT 50')}>服务器资产</button>
              <button className="sql-snippet-btn" onClick={() => setSql('SELECT name, display_name, system_name FROM services LIMIT 50')}>服务清单</button>
              <button className="sql-snippet-btn" onClick={() => setSql('SELECT report_type, title, format, size_bytes, created_at FROM report_artifacts ORDER BY created_at DESC LIMIT 50')}>报告制品</button>
              <button className="sql-snippet-btn sql-snippet-btn--muted" onClick={() => navigator.clipboard?.writeText(sql)}>复制 SQL</button>
            </div>
            <textarea className="sql-editor" value={sql} spellCheck={false} onChange={(e) => setSql(e.target.value)} />
            <div className="sql-form-grid">
              <label className="field-label">最大行数
                <input type="number" min={1} max={5000} value={limit} onChange={(e) => setLimit(Number(e.target.value))} />
              </label>
              <label className="field-label">导出格式
                <select value={format} onChange={(e) => setFormat(e.target.value)}>
                  {FORMAT_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
              </label>
              <label className="field-label">文件名提示
                <input value={filenameHint} onChange={(e) => setFilenameHint(e.target.value)} />
              </label>
            </div>
            <div className="sql-actions">
              <button className="btn btn-subtle" onClick={runQuery} disabled={loading || !sql.trim()}>{loading ? '处理中...' : '执行只读查询'}</button>
              <button className="btn btn-primary" onClick={exportQuery} disabled={loading || !sql.trim()}>{loading ? '处理中...' : `导出 ${format.toUpperCase()}`}</button>
            </div>
          </section>

          {result && (
            <section className="glass-card sql-result-card page-enter">
              <div className="section-title-row">
                <div>
                  <h2>查询结果预览</h2>
                  <p>{result.row_count ?? rows.length} 行 · {result.duration_ms ?? '-'} ms · 来源 {result.source || 'local_ops_db'}</p>
                </div>
                {maskedColumns.length > 0 && <span className="status-badge status-badge--warning">已脱敏 {maskedColumns.join(', ')}</span>}
              </div>
              <div className="sql-context-strip" style={{ marginBottom: 12 }}>
                {(result.protections || []).map((item: string) => <span key={item}>{item}</span>)}
              </div>
              <div className="sql-table-wrap">
                <DataTable<Record<string, any>>
                  rows={rows}
                  rowKey={(_, index) => String(index)}
                  columns={columns.map((column) => ({ key: column, title: column, render: (row) => <span title={String(row[column] ?? '')}>{String(row[column] ?? '')}</span> }))}
                />
                {columns.length === 0 && <div className="empty-state"><strong>没有返回表格数据</strong><span>查询执行成功，但没有可展示的列。</span></div>}
              </div>
            </section>
          )}
        </main>

        <aside className="sql-side-column">
          <section className="glass-card">
            <div className="section-title-row section-title-row--compact"><div><h2>本地表</h2><p>用于辅助 AI 生成只读查询。</p></div></div>
            <div className="history-list">
              {tables.slice(0, 40).map((t: any) => (
                <button key={t.name} className="history-item" onClick={() => setSql(`SELECT * FROM ${t.name} LIMIT 50`)} disabled={!!t.denied}>
                  <span className={`status-dot ${t.denied ? 'offline' : 'online'}`} />
                  <span><strong>{t.name}</strong><small>{t.column_count ?? '-'} 列 {t.denied ? ' · 禁止查询' : ''}</small></span>
                </button>
              ))}
            </div>
          </section>

          <section className="glass-card">
            <div className="section-title-row section-title-row--compact">
              <div><h2>最近导出</h2><p>报告中心制品，可下载和审计。</p></div>
              <span style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                <button className="btn btn-subtle" onClick={loadExports}>刷新</button>
                <button className="btn btn-danger" disabled={!selectedExportIds.length || loading} onClick={() => setBatchDeleteExportsOpen(true)}>批量删除 ({selectedExportIds.length})</button>
              </span>
            </div>
            <div className="history-list">
              {exportsList.map((item: any) => (
                <div key={item.id} className="history-item">
                  <input type="checkbox" checked={selectedExportIds.includes(exportId(item))} onChange={(e) => toggleExportSelection(exportId(item), e.target.checked)} aria-label={`选择导出 ${item.title || item.id}`} />
                  <span className="status-dot online" />
                  <span><strong>{item.title}</strong><small>{item.format} · {item.size_bytes || 0} bytes</small><code>{item.sha256}</code></span>
                  <span style={{ display: 'flex', gap: 6, marginLeft: 'auto' }}>
                    <a className="btn btn-subtle" href={dbTools.downloadUrl(exportId(item))} target="_blank" rel="noreferrer">下载</a>
                    <button className="btn btn-danger" onClick={() => setDeleteExportTarget(item)} disabled={loading}>删除</button>
                  </span>
                </div>
              ))}
              {exportsList.length === 0 && <div className="empty-state"><strong>暂无导出</strong><span>生成 CSV/SQL/JSON 后会显示在这里。</span></div>}
            </div>
            <div className="pagination-bar" style={{ marginTop: 10 }}>
              <div className="pagination-info">第 {exportPage}/{exportPages} 页 · 共 {exportsTotal} 条</div>
              <div className="pagination-controls">
                <label className="pagination-size-label">每页
                  <select value={exportPageSize} onChange={(e) => { setExportPageSize(Number(e.target.value)); setExportOffset(0) }}>
                    {[10, 20, 50].map((n) => <option key={n} value={n}>{n}</option>)}
                  </select>
                </label>
                <button className="pagination-btn" disabled={exportOffset <= 0} onClick={() => setExportOffset(Math.max(0, exportOffset - exportPageSize))}>上一页</button>
                <button className="pagination-btn" disabled={exportOffset + exportPageSize >= exportsTotal} onClick={() => setExportOffset(exportOffset + exportPageSize)}>下一页</button>
              </div>
            </div>
          </section>
        </aside>
      </div>
      <ConfirmDialog
        open={Boolean(deleteExportTarget)}
        title="删除数据库导出报告"
        description={`确认删除 ${deleteExportTarget?.title || deleteExportTarget?.id}？会删除报告制品记录和本地导出文件。`}
        confirmLabel="删除"
        danger
        onCancel={() => setDeleteExportTarget(null)}
        onConfirm={deleteExport}
      />
      <ConfirmDialog
        open={batchDeleteExportsOpen}
        title="批量删除数据库导出"
        description={`确认删除选中的 ${selectedExportIds.length} 个导出制品？会同步删除本地导出文件。`}
        confirmLabel="批量删除"
        danger
        onCancel={() => setBatchDeleteExportsOpen(false)}
        onConfirm={() => deleteSelectedExports(selectedExportIds)}
      />
    </div>
  )
}

function DatabaseExecutePanel({ connections, sharedContext, onSharedContextChange, onRunValidationQuery }: { connections: any[]; sharedContext: SharedDatabaseContext; onSharedContextChange: (patch: Partial<SharedDatabaseContext>) => void; onRunValidationQuery?: (sql: string) => void }) {
  const [connectionId, setConnectionId] = useState(sharedContext.connectionId || '')
  const [databaseName, setDatabaseName] = useState(sharedContext.databaseName || '')
  const [sql, setSql] = useState("UPDATE report_artifacts SET status = 'archived' WHERE id = 'report_id'")
  const [maxAffectedRows, setMaxAffectedRows] = useState(sharedContext.maxAffectedRows || 100)
  const [previewLevel, setPreviewLevel] = useState<'fast' | 'standard' | 'full'>('standard')
  const [reason, setReason] = useState('')
  const [preview, setPreview] = useState<any>(null)
  const previewRef = useRef(preview)
  previewRef.current = preview
  const [result, setResult] = useState<any>(null)
  const [history, setHistory] = useState<any[]>([])
  const [historyTotal, setHistoryTotal] = useState(0)
  const [historyOffset, setHistoryOffset] = useState(0)
  const [historyPageSize, setHistoryPageSize] = useState(20)
  const [selectedHistoryIds, setSelectedHistoryIds] = useState<string[]>([])
  const [deleteHistoryTarget, setDeleteHistoryTarget] = useState<any | null>(null)
  const [batchDeleteHistoryOpen, setBatchDeleteHistoryOpen] = useState(false)
  const [message, setMessage] = useState('')
  const [loading, setLoading] = useState(false)
  

  const selectedConnection = connections.find((item: any) => item.id === connectionId)
  const dmlConnections = connections.filter((item: any) => item.allow_dml)
  const rows: any[] = preview?.before_sample_rows || []
  const columns = rows[0] ? Object.keys(rows[0]) : []

  useEffect(() => {
    if (sharedContext.connectionId !== undefined && sharedContext.connectionId !== connectionId) setConnectionId(sharedContext.connectionId || '')
    if (sharedContext.databaseName !== undefined && sharedContext.databaseName !== databaseName) setDatabaseName(sharedContext.databaseName || '')
    if (sharedContext.maxAffectedRows !== undefined && sharedContext.maxAffectedRows !== maxAffectedRows) setMaxAffectedRows(sharedContext.maxAffectedRows || 100)
  }, [sharedContext.connectionId, sharedContext.databaseName, sharedContext.maxAffectedRows])

  const loadHistory = async (nextOffset = historyOffset, nextPageSize = historyPageSize) => {
    try {
      const res = await dbTools.executeHistory({ limit: nextPageSize, offset: nextOffset })
      const data = pickData<any>(res, {})
      const items = data.items || []
      setHistory(items)
      setHistoryTotal(Number(data.pagination?.total ?? data.total ?? items.length))
      const visibleIds = new Set(items.map((item: any) => String(item.id || item.execution_id || '')).filter(Boolean))
      setSelectedHistoryIds((prev) => prev.filter((id) => visibleIds.has(id)))
    } catch (_) {}
  }

  useEffect(() => { loadHistory() }, [historyOffset, historyPageSize])

  const historyPageIds = history.map((item: any) => String(item.id || item.execution_id || '')).filter(Boolean)
  const allHistorySelected = historyPageIds.length > 0 && historyPageIds.every((id) => selectedHistoryIds.includes(id))
  const historyPage = Math.floor(historyOffset / historyPageSize) + 1
  const historyPages = Math.max(1, Math.ceil(historyTotal / historyPageSize))

  const toggleHistorySelection = (id: string, checked: boolean) => {
    setSelectedHistoryIds((prev) => checked ? Array.from(new Set([...prev, id])) : prev.filter((item) => item !== id))
  }

  const deleteSelectedDmlHistory = async (ids: string[]) => {
    const uniqueIds = Array.from(new Set(ids.filter(Boolean)))
    if (uniqueIds.length === 0) return
    try {
      await dbTools.deleteExecuteHistories({ execution_ids: uniqueIds })
      setSelectedHistoryIds([])
      setBatchDeleteHistoryOpen(false)
      setDeleteHistoryTarget(null)
      const remaining = Math.max(0, historyTotal - uniqueIds.length)
      const maxOffset = Math.max(0, Math.floor(Math.max(remaining - 1, 0) / historyPageSize) * historyPageSize)
      const nextOffset = Math.min(historyOffset, maxOffset)
      if (nextOffset !== historyOffset) setHistoryOffset(nextOffset)
      else await loadHistory(nextOffset, historyPageSize)
    } catch (e) {
      setMessage(errorMessage(e, '删除 DML 执行历史失败'))
    }
  }

  const runPreview = async () => {
    setLoading(true)
    setMessage('')
    setResult(null)
    try {
      const res = await dbTools.previewExecute({ sql, connection_id: connectionId || undefined, database_name: databaseName || selectedConnection?.database_name || undefined, max_affected_rows: maxAffectedRows, preview_level: previewLevel })
      setPreview(pickData<any>(res, null))
    } catch (e) {
      setPreview(null)
      setMessage(errorMessage(e, '执行预检失败'))
    } finally {
      setLoading(false)
    }
  }

  const runFullPreview = async () => {
    setPreviewLevel('full')
    setLoading(true)
    setMessage('')
    setResult(null)
    try {
      const res = await dbTools.previewExecute({ sql, connection_id: connectionId || undefined, database_name: databaseName || selectedConnection?.database_name || undefined, max_affected_rows: maxAffectedRows, preview_level: 'full' })
      setPreview(pickData<any>(res, null))
    } catch (e) {
      setPreview(null)
      setMessage(errorMessage(e, '执行预检失败'))
    } finally {
      setLoading(false)
    }
  }

  const handleHighRiskPrecheck = async () => {
    setLoading(true)
    setPreviewLevel('full')
    try {
      const res = await dbTools.previewExecute({ sql, connection_id: connectionId || undefined, database_name: databaseName || selectedConnection?.database_name || undefined, max_affected_rows: maxAffectedRows, preview_level: 'full' })
      const data = pickData<any>(res, null)
      setPreview(data)
      setLoading(false)
      return {
        detail: `预计影响 ${data?.estimated_affected_rows ?? '未知'} 行`,
        risk_level: data?.risk_level || 'high',
        confirm_text: data?.confirm_text || 'EXECUTE SQL',
        details: [
          { label: '连接', value: data?.connection_name || '-' },
          { label: '表', value: data?.table_name || '-' },
          { label: '预计影响行数', value: data?.estimated_affected_rows ?? '未知' },
          { label: '保护阈值', value: data?.max_affected_rows ?? maxAffectedRows },
          { label: 'WHERE 条件', value: data?.where_summary || '-' },
          { label: 'SQL 类型', value: data?.statement_type || '-' },
        ],
      }
    } catch (e) {
      setLoading(false)
      throw e
    }
  }

  const handleHighRiskExecute = async (executeReason: string): Promise<{ success: boolean; message?: string; reportUrl?: string }> => {
    const p = previewRef.current
    if (!p) return { success: false, message: '预检数据不可用' }
    setLoading(true)
    try {
      const res = await dbTools.execute({
        sql,
        connection_id: connectionId || undefined,
        database_name: databaseName || selectedConnection?.database_name || undefined,
        max_affected_rows: maxAffectedRows,
        confirm_text: p.confirm_text || 'EXECUTE SQL',
        reason: executeReason,
      })
      const data = pickData<any>(res, null)
      setResult(data)
      setPreview(null)
      await loadHistory()
      setLoading(false)
      return { success: true, message: data?.summary || `影响 ${data?.affected_rows} 行, 耗时 ${data?.duration_ms}ms` }
    } catch (e) {
      setLoading(false)
      setMessage(errorMessage(e, '执行失败'))
      return { success: false, message: errorMessage(e, '执行失败') }
    }
  }

  const templates = [
    { label: 'UPDATE 模板', value: "UPDATE your_table SET status = 'fixed' WHERE id = 123" },
    { label: 'DELETE 模板', value: "DELETE FROM your_table WHERE id = 123" },
    { label: 'INSERT 模板', value: "INSERT INTO your_table (name, status) VALUES ('sample', 'active')" },
    { label: '更新报告状态', value: "UPDATE report_artifacts SET status = 'archived' WHERE id = 'report_id'" },
  ]

  return (
    <div className="database-tab-panel">
      <section className="glass-panel sql-hero sql-hero--compact">
        <div>
          <span className="eyebrow">Controlled DML Execute</span>
          <h1>DML 受控执行中心</h1>
          <p>支持 INSERT / UPDATE / DELETE。执行链路固定为预检、样例快照、一键确认、事务执行、超阈值回滚、审计复盘；普通查询页仍保持只读。</p>
        </div>
        <div className="sql-hero-metrics">
          <div className="metric-pill metric-pill--risk"><span>{maxAffectedRows}</span><small>影响行保护</small></div>
          <div className="metric-pill"><span>{connectionId ? '业务库' : 'OPS'}</span><small>执行来源</small></div>
          <div className="metric-pill metric-pill--safe"><span>{dmlConnections.length}</span><small>DML 连接</small></div>
        </div>
      </section>

      {message && <div className="alert-card alert-card--danger"><strong>操作失败</strong><span>{message}</span></div>}

      <div className="sql-layout">
        <main className="sql-main-column">
          <section className="glass-card sql-editor-card">
            <div className="section-title-row">
              <div><h2>SQL 执行器</h2><p>适合报告删除、状态更新、少量数据修正。大批量业务数据变更请优先走数据清理任务或 DBA 流程。</p></div>
              <span className="status-badge status-badge--danger">DML GOVERNED</span>
            </div>
            <div className="sql-snippet-row">
              {templates.map((item) => <button key={item.label} className="sql-snippet-btn" onClick={() => { setSql(item.value); setPreview(null); setResult(null) }}>{item.label}</button>)}
              <button className="sql-snippet-btn sql-snippet-btn--muted" onClick={() => navigator.clipboard?.writeText(sql)}>复制 SQL</button>
            </div>
            <textarea className="sql-editor" value={sql} spellCheck={false} onChange={(e) => { setSql(e.target.value); setPreview(null); setResult(null) }} />
            <div className="sql-form-grid">
              <label className="field-label">连接
                <select value={connectionId} onChange={(e) => { const id = e.target.value; const conn = connections.find((c: any) => c.id === id); const dbName = conn?.database_name || ''; const maxRows = conn?.max_affected_rows_default || 100; setConnectionId(id); setDatabaseName(dbName); setMaxAffectedRows(maxRows); onSharedContextChange({ connectionId: id, databaseName: dbName, maxAffectedRows: maxRows }); setPreview(null) }}>
                  <option value="">本地 OPS 库</option>
                  {connections.map((conn: any) => <option key={conn.id} value={conn.id} disabled={!conn.allow_dml}>{conn.name} · {conn.environment} · {conn.allow_dml ? `DML上限${conn.max_affected_rows_default || 100}` : '只读'} · {conn.database_name}</option>)}
                </select>
              </label>
              <label className="field-label">数据库名
                <input value={databaseName} onChange={(e) => { setDatabaseName(e.target.value); onSharedContextChange({ databaseName: e.target.value }) }} placeholder={selectedConnection?.database_name || 'ops'} />
              </label>
              <label className="field-label">最大影响行数
                <input type="number" min={1} max={1000} value={maxAffectedRows} onChange={(e) => { const next = Number(e.target.value); setMaxAffectedRows(next); onSharedContextChange({ maxAffectedRows: next }) }} />
              </label>
              <label className="field-label">预检级别
                <select value={previewLevel} onChange={(e) => { setPreviewLevel(e.target.value as any); setPreview(null) }}>
                  <option value="fast">快速：语法和风险</option>
                  <option value="standard">标准：增加影响行数</option>
                  <option value="full">完整：增加样例数据</option>
                </select>
              </label>
              <label className="field-label">执行原因
                <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="例如：修正报告状态 / 删除过期导出记录" />
              </label>
            </div>
            <div className="sql-actions">
              <button className="btn btn-subtle" onClick={runPreview} disabled={loading || !sql.trim()}>{loading ? '处理中...' : '预检执行影响'}</button>
              <button className="btn btn-subtle" onClick={runFullPreview} disabled={loading || !sql.trim()}>查看样例</button>
            </div>
            <HighRiskFlow
              title="确认执行数据库 DML"
              target={`${preview?.connection_name || 'local_ops_db'} / ${preview?.table_name || '-'}`}
              description="该操作会修改数据库数据，请确认已完成预检并了解影响范围。"
              confirmText={preview?.confirm_text || 'EXECUTE SQL'}
              riskLevel={preview?.risk_level || 'high'}
              confirmMode="one-click"
              reasonRequired={true}
              reasonLabel="执行原因"
              confirmButtonLabel="确认执行"
              onPrecheck={handleHighRiskPrecheck}
              onExecute={handleHighRiskExecute}
            />{/* RiskActionGuard kept as fallback for direct execution */}
          </section>

          {preview && (
            <section className="glass-card sql-result-card page-enter">
              <div className="section-title-row"><div><h2>执行预检</h2><p>{preview.connection_name} · {preview.statement_type || preview.query_type} · {preview.table_name} · 预计影响 {preview.estimated_affected_rows ?? '未知'} 行</p></div><span className={`status-badge ${preview.executable === false ? 'status-badge--danger' : 'status-badge--warning'}`}>{preview.executable === false ? '已阻断' : preview.risk_level || '可执行'}</span></div>
              <div className="sql-context-strip" style={{ marginBottom: 12 }}>
                <span>WHERE：{preview.where_summary || '-'}</span>
                <span>保护阈值：{preview.max_affected_rows}</span>
                <span>预检级别：{preview.preview_level || previewLevel}</span>
                {(preview.changed_columns || []).length > 0 && <span>更新字段：{preview.changed_columns.join(', ')}</span>}
              </div>
              <div className="sql-context-strip" style={{ marginBottom: 12 }}>{(preview.protections || []).map((item: string) => <span key={item}>{item}</span>)}</div>
              <div className="risk-panel risk-panel--high" style={{ marginBottom: 12 }}>
                {(preview.risk?.warnings || []).map((item: string, idx: number) => <div key={`w-${idx}`}>警告：{item}</div>)}
                {(preview.risk?.suggestions || []).map((item: string, idx: number) => <div key={`s-${idx}`}>建议：{item}</div>)}
                {preview.over_limit && <div>阻断：预计影响行数超过保护阈值。</div>}
              </div>
              <pre className="code-block">{preview.history_sql || preview.normalized_sql}</pre>
              {preview.verification_sql && (
                <div className="alert-card alert-card--info" style={{ marginTop: 12 }}>
                  <strong>执行后验证 SQL</strong>
                  <code>{preview.verification_sql}</code>
                  <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                    <button className="btn btn-subtle" onClick={() => navigator.clipboard?.writeText(preview.verification_sql)}>复制验证 SQL</button>
                    {onRunValidationQuery && <button className="btn btn-subtle" onClick={() => onRunValidationQuery(preview.verification_sql)}>回填到查询页</button>}
                  </div>
                </div>
              )}
              {rows.length > 0 && (
                <div className="sql-table-wrap" style={{ marginTop: 12 }}>
                  <div className="section-title-row section-title-row--compact"><div><h2>执行前样例</h2><p>最多 20 行，敏感字段自动脱敏。用于 UPDATE / DELETE 复核。</p></div></div>
                  <DataTable<Record<string, any>> rows={rows} rowKey={(_, index) => String(index)} columns={columns.map((column) => ({ key: column, title: column, render: (row) => <span title={String(row[column] ?? '')}>{String(row[column] ?? '')}</span> }))} />
                </div>
              )}
            </section>
          )}

          {result && (
            <section className="glass-card sql-result-card page-enter">
              <div className="section-title-row"><div><h2>执行结果</h2><p>{result.summary}</p></div><span className="status-badge status-badge--success">{result.status}</span></div>
              <div className="sql-context-strip"><span>执行 ID：{result.execution_id || '-'}</span><span>影响行数：{result.affected_rows}</span><span>耗时：{result.duration_ms} ms</span><span>操作原因：{result.reason || '-'}</span></div>
              {result.verification_sql && (
                <div className="alert-card alert-card--info" style={{ marginTop: 12 }}>
                  <strong>建议立即验证</strong>
                  <code>{result.verification_sql}</code>
                  <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                    <button className="btn btn-subtle" onClick={() => navigator.clipboard?.writeText(result.verification_sql)}>复制验证 SQL</button>
                    {onRunValidationQuery && <button className="btn btn-primary" onClick={() => onRunValidationQuery(result.verification_sql)}>回填到查询页</button>}
                  </div>
                </div>
              )}
            </section>
          )}
        </main>
        <aside className="sql-side-column">
          <section className="glass-card"><div className="section-title-row section-title-row--compact"><div><h2>执行边界</h2><p>后端强制执行，无法通过 MCP 绕过。</p></div></div>
            <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--text-secondary)', lineHeight: 1.8, fontSize: 13 }}>
              <li>只允许单条 UPDATE / DELETE / INSERT。</li>
              <li>UPDATE / DELETE 必须包含 WHERE。</li>
              <li>业务库连接必须显式开启 DML 策略。</li>
              <li>本地 OPS 库仅开放报告、通知、审计和日志等维护表。</li>
              <li>执行必须填写原因，并进入历史与审计链路。</li>
            </ul>
          </section>
          <section className="glass-card">
            <div className="section-title-row section-title-row--compact">
              <div><h2>最近 DML 执行</h2><p>共 {historyTotal} 条，第 {historyPage}/{historyPages} 页。</p></div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                <button className="btn btn-subtle" onClick={() => loadHistory()}>刷新</button>
                <button className="btn btn-danger" disabled={selectedHistoryIds.length === 0} onClick={() => setBatchDeleteHistoryOpen(true)}>批量删除 ({selectedHistoryIds.length})</button>
              </div>
            </div>
            <div className="record-selection-row">
              <label><input type="checkbox" checked={allHistorySelected} disabled={historyPageIds.length === 0} onChange={(e) => setSelectedHistoryIds(e.target.checked ? historyPageIds : [])} /> 选择本页</label>
              <select value={historyPageSize} onChange={(e) => { setHistoryPageSize(Number(e.target.value)); setHistoryOffset(0); setSelectedHistoryIds([]) }}>
                {[10, 20, 50].map((size) => <option key={size} value={size}>{size} 条/页</option>)}
              </select>
            </div>
            <div className="history-list">
              {history.map((item: any) => (
                <div key={item.id} className="history-item history-item--selectable">
                  <input type="checkbox" checked={selectedHistoryIds.includes(String(item.id))} onChange={(e) => toggleHistorySelection(String(item.id), e.target.checked)} />
                  <span className={`status-dot ${item.status === 'success' ? 'online' : 'offline'}`} />
                  <span><strong>{item.statement_type} · {item.table_name || '-'}</strong><small>{item.connection_name || 'local_ops_db'} · 影响 {item.affected_rows ?? '-'} 行 · {item.status}</small><code>{item.history_sql}</code></span>
                  <button className="btn btn-danger btn-sm" onClick={() => setDeleteHistoryTarget(item)}>删除</button>
                </div>
              ))}
              {history.length === 0 && <div className="empty-state"><strong>暂无执行记录</strong><span>DML 执行后会显示在这里。</span></div>}
            </div>
            <div className="pagination-footer">
              <span className="pagination-page">显示 {historyTotal === 0 ? 0 : historyOffset + 1}-{Math.min(historyOffset + historyPageSize, historyTotal)} / {historyTotal}</span>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="pagination-btn" disabled={historyOffset === 0} onClick={() => setHistoryOffset(Math.max(0, historyOffset - historyPageSize))}>上一页</button>
                <button className="pagination-btn" disabled={historyOffset + historyPageSize >= historyTotal} onClick={() => setHistoryOffset(historyOffset + historyPageSize)}>下一页</button>
              </div>
            </div>
          </section>
        </aside>
      </div>
      <ConfirmDialog
        open={Boolean(deleteHistoryTarget)}
        title="删除 DML 执行历史"
        description={`确认删除 ${deleteHistoryTarget?.statement_type || ''} ${deleteHistoryTarget?.table_name || ''} 的执行记录？只删除历史记录，不回滚数据库变更。`}
        confirmLabel="删除"
        danger
        onCancel={() => setDeleteHistoryTarget(null)}
        onConfirm={() => deleteSelectedDmlHistory([String(deleteHistoryTarget?.id || '')])}
      />
      <ConfirmDialog
        open={batchDeleteHistoryOpen}
        title="批量删除 DML 执行历史"
        description={`确认删除选中的 ${selectedHistoryIds.length} 条 DML 执行历史？只删除历史记录，不回滚数据库变更。`}
        confirmLabel="批量删除"
        danger
        onCancel={() => setBatchDeleteHistoryOpen(false)}
        onConfirm={() => deleteSelectedDmlHistory(selectedHistoryIds)}
      />
    </div>
  )
}

export default function DatabaseToolsPage() {
  const { user } = useAuthStore()
  const isAdmin = Boolean(user?.is_admin)
  const role = String(user?.role || '').toLowerCase()
  const isOperator = isAdmin || role === 'operator' || role === 'developer'
  const location = useLocation()
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState<DatabaseTab>(() => normalizeTab(new URLSearchParams(location.search).get('tab')))
  const [dbContext, setDbContext] = useState<SharedDatabaseContext>({ maxRows: 100, timeoutSeconds: 30, maxAffectedRows: 100 })
  const cleanupTab = 'jobs' as const
  const [confirmState, setConfirmState] = useState<{ title: string; description?: string; confirmLabel?: string; danger?: boolean } | null>(null)
  const [bastionServers, setBastionServers] = useState<any[]>([])
  const [bastionServersLoading, setBastionServersLoading] = useState(false)
  const [sshKeys, setSshKeys] = useState<any[]>([])
  const [sshKeysLoading, setSshKeysLoading] = useState(false)
  const [sshKeyUploading, setSshKeyUploading] = useState(false)
  const confirmResolverRef = useRef<((value: boolean) => void) | null>(null)

  const requestConfirm = (config: { title: string; description?: string; confirmLabel?: string; danger?: boolean }) => new Promise<boolean>((resolve) => {
    confirmResolverRef.current = resolve
    setConfirmState(config)
  })
  const closeConfirm = (value: boolean) => {
    confirmResolverRef.current?.(value)
    confirmResolverRef.current = null
    setConfirmState(null)
  }

  const {
    message,
    setMessage,
    errorMsg,
    setErrorMsg,
    connections,
    connLoading,
    connSearch,
    setConnSearch,
    connEnvFilter,
    setConnEnvFilter,
    showConnForm,
    setShowConnForm,
    connForm,
    setConnForm,
    connSubmitting,
    setConnSubmitting,
    jobs,
    jobsLoading,
    jobSearch,
    setJobSearch,
    statusFilter,
    setStatusFilter,
    showJobForm,
    setShowJobForm,
    jobForm,
    setJobForm,
    jobSubmitting,
    setJobSubmitting,
    tableOptions,
    setTableOptions,
    columnOptions,
    setColumnOptions,
    tablesLoading,
    setTablesLoading,
    columnsLoading,
    setColumnsLoading,
    selectedJob,
    setSelectedJob,
    jobDetail,
    setJobDetail,
    jobBatches,
    setJobBatches,
    jobEvents,
    setJobEvents,
    dryRunResult,
    setDryRunResult,
    dryRunLoading,
    setDryRunLoading,
    confirmText,
    setConfirmText,
    startPlan,
    setStartPlan,
    actionLoading,
    setActionLoading,
    loadConnections,
    loadJobs,
  } = useMaintenanceData({ isAdmin: isOperator, mainTab: activeTab === 'cleanup' ? 'cleanup' : 'sql', cleanupTab })

  useEffect(() => {
    const nextTab = normalizeTab(new URLSearchParams(location.search).get('tab'))
    setActiveTab(nextTab)
    const raw = new URLSearchParams(location.search).get('tab')
    if (raw === 'jobs') navigate(`${ROUTES.database}?tab=cleanup`, { replace: true })
  }, [location.search, navigate])

  const showMsg = (msg: string) => { setMessage(msg); window.setTimeout(() => setMessage(''), 3000) }
  const showErr = (msg: string) => { setErrorMsg(msg); window.setTimeout(() => setErrorMsg(''), 5000) }

  const {
    handleConnSubmit,
    handleConnDelete,
    handleConnTest,
    handleJobFormConnChange,
    handleJobFormTableChange,
    handleJobSubmit,
    openJobDetail,
    handleDryRun,
    handleJobAction,
  } = useCleanupJobActions({
    connections,
    connForm,
    setConnForm,
    setConnSubmitting,
    setShowConnForm,
    jobForm,
    setJobForm,
    setJobSubmitting,
    setShowJobForm,
    statusFilter,
    selectedJob,
    setSelectedJob,
    setJobDetail,
    setJobBatches,
    setJobEvents,
    setDryRunResult,
    setDryRunLoading,
    confirmText,
    setConfirmText,
    startPlan,
    setStartPlan,
    setActionLoading,
    setTableOptions,
    setColumnOptions,
    setTablesLoading,
    setColumnsLoading,
    loadConnections,
    loadJobs,
    showMsg,
    showErr,
    requestConfirm,
  })


  const normalizeServerAssets = (payload: any): any[] => {
    const data = pickData<any>(payload, [])
    if (Array.isArray(data)) return data
    if (Array.isArray(data?.data)) return data.data
    if (Array.isArray(data?.items)) return data.items
    if (Array.isArray(data?.servers)) return data.servers
    return []
  }

  const mergeServerAssets = (...lists: any[][]): any[] => {
    const byKey = new Map<string, any>()
    for (const list of lists) {
      for (const raw of list || []) {
        if (!raw) continue
        const host = raw.host || raw.hostname || raw.ip || ''
        const name = raw.name || raw.display_name || host
        if (!name && !host) continue
        const item = {
          ...raw,
          name,
          host,
          port: Number(raw.port || raw.ssh_port || 22),
          user: raw.user || raw.username || raw.ssh_username || 'root',
          username: raw.username || raw.user || raw.ssh_username || 'root',
          key: raw.key || raw.key_file || raw.ssh_key_path || '',
          key_file: raw.key_file || raw.key || raw.ssh_key_path || '',
          source: raw.source || 'server',
        }
        const key = item.name || item.host
        if (!byKey.has(key)) byKey.set(key, item)
        else byKey.set(key, { ...item, ...byKey.get(key) })
      }
    }
    return Array.from(byKey.values()).sort((a, b) => String(a.name || a.host).localeCompare(String(b.name || b.host)))
  }

  const loadBastionServers = async () => {
    setBastionServersLoading(true)
    try {
      const results = await Promise.allSettled([
        (maintenance as any).sshServerAssets?.(),
        serverManagement.list(false),
      ])
      const lists = results
        .filter((item): item is PromiseFulfilledResult<any> => item.status === 'fulfilled')
        .map((item) => normalizeServerAssets(item.value))
      const merged = mergeServerAssets(...lists)
      setBastionServers(merged)
      if (merged.length === 0) {
        showErr('未读取到服务器资产：请确认服务器模块已有管理服务器，或检查 /api/v2/maintenance/ssh-server-assets 返回。')
      }
    } catch (e) {
      showErr(errorMessage(e, '加载服务器资产失败'))
      setBastionServers([])
    } finally {
      setBastionServersLoading(false)
    }
  }

  const loadSshKeys = async () => {
    setSshKeysLoading(true)
    try {
      const res: any = await adminMaintenance.sshKeys.list()
      const data = pickData<any[]>(res, [])
      setSshKeys(Array.isArray(data) ? data : [])
    } catch (e) {
      showErr(errorMessage(e, '加载 SSH 密钥失败'))
      setSshKeys([])
    } finally {
      setSshKeysLoading(false)
    }
  }

  const uploadDbSshKeyToField = async (event: ChangeEvent<HTMLInputElement>, fieldName: 'ssh_key_path' | 'ssh_target_key_path') => {
    const file = event.target.files?.[0]
    if (!file) return
    setSshKeyUploading(true)
    try {
      const res: any = await adminMaintenance.uploadKey(file)
      const data = pickData<any>(res, {})
      const name = data.name || data.path || data.filename || file.name
      setConnForm((f: any) => ({ ...f, [fieldName]: name }))
      await loadSshKeys()
      showMsg(`密钥已上传并选中：${name}`)
    } catch (e) {
      showErr(errorMessage(e, '上传 SSH 密钥失败'))
    } finally {
      setSshKeyUploading(false)
      event.target.value = ''
    }
  }

  const handleDbSshKeyUpload = async (event: ChangeEvent<HTMLInputElement>) => uploadDbSshKeyToField(event, 'ssh_key_path')
  const handleDbTargetSshKeyUpload = async (event: ChangeEvent<HTMLInputElement>) => uploadDbSshKeyToField(event, 'ssh_target_key_path')

  const handleUploadSshKeyContent = async (connectionId: string, content: string) => {
    try {
      await maintenance.connections.uploadSshKey(connectionId, content)
      showMsg('SSH 密钥已保存')
      loadConnections()
    } catch (e: any) {
      showErr(errorMessage(e, '保存 SSH 密钥失败'))
      throw e
    }
  }

  const handleDeleteSshKeyContent = async (connectionId: string) => {
    try {
      await maintenance.connections.deleteSshKey(connectionId)
      showMsg('SSH 密钥已删除')
      loadConnections()
    } catch (e: any) {
      showErr(errorMessage(e, '删除 SSH 密钥失败'))
      throw e
    }
  }

  useEffect(() => {
    if (activeTab === 'connections') {
      if (bastionServers.length === 0 && !bastionServersLoading) loadBastionServers()
      if (sshKeys.length === 0 && !sshKeysLoading) loadSshKeys()
    }
  }, [activeTab])

  const { envOptions, filteredConnections, connStats } = useConnectionFilters(connections, connSearch, connEnvFilter)
  const filteredJobs = jobs.filter((j: any) => {
    const text = `${j.name || ''} ${j.connection_name || ''} ${j.database_name || ''} ${j.table_name || ''} ${j.status || ''}`.toLowerCase()
    return !jobSearch.trim() || text.includes(jobSearch.trim().toLowerCase())
  })
  const jobStats = {
    total: jobs.length,
    running: jobs.filter((j: any) => j.status === 'running').length,
    pending: jobs.filter((j: any) => ['pending_approval', 'approved', 'ready'].includes(j.status)).length,
    failed: jobs.filter((j: any) => j.status === 'failed').length,
  }

  const inputStyle: CSSProperties = {
    background: 'var(--bg-page)',
    border: '1px solid var(--border-strong)',
    borderRadius: '6px',
    padding: '8px 12px',
    color: 'var(--text-primary)',
    fontSize: '14px',
    width: '100%',
    boxSizing: 'border-box',
  }
  const selectStyle: CSSProperties = { ...inputStyle, appearance: 'auto' as any }

  const updateDbContext = (patch: Partial<SharedDatabaseContext>) => {
    setDbContext((prev) => ({ ...prev, ...patch }))
  }

  const openTab = (tab: DatabaseTab) => {
    setActiveTab(tab)
    const queryTab = tab === 'cleanup' ? 'cleanup' : tab
    navigate(`${ROUTES.database}?tab=${queryTab}`, { replace: true })
  }

  const runValidationQuery = (validationSql: string) => {
    updateDbContext({ sql: validationSql })
    openTab('query')
  }

  const renderConnections = () => (
    <ConnectionsTab
      connStats={connStats}
      connSearch={connSearch}
      connEnvFilter={connEnvFilter}
      envOptions={envOptions}
      connLoading={connLoading}
      showConnForm={showConnForm}
      connForm={connForm}
      connSubmitting={connSubmitting}
      filteredConnections={filteredConnections}
      bastionServers={bastionServers}
      bastionServersLoading={bastionServersLoading}
      loadBastionServers={loadBastionServers}
      sshKeys={sshKeys}
      sshKeysLoading={sshKeysLoading}
      sshKeyUploading={sshKeyUploading}
      loadSshKeys={loadSshKeys}
      onUploadSshKey={handleDbSshKeyUpload}
      onUploadTargetSshKey={handleDbTargetSshKeyUpload}
      inputStyle={inputStyle}
      selectStyle={selectStyle}
      setConnSearch={setConnSearch}
      setConnEnvFilter={setConnEnvFilter}
      setConnForm={setConnForm}
      loadConnections={loadConnections}
      onShowCreate={() => { setShowConnForm(true); setConnForm({ ...defaultConnForm }); if (bastionServers.length === 0) loadBastionServers(); if (sshKeys.length === 0) loadSshKeys() }}
      onCloseForm={() => setShowConnForm(false)}
      onSubmit={handleConnSubmit}
      onTest={handleConnTest}
      onDelete={handleConnDelete}
      onEdit={(c) => {
        setConnForm({
          ...defaultConnForm,
          id: c.id,
          name: c.name || '',
          environment: c.environment || 'dev',
          db_type: c.db_type || 'mysql',
          host: c.host || '',
          port: c.port || 3306,
          username: c.username || '',
          password: '',
          database_name: c.database_name || '',
          description: c.description || '',
          use_ssh_tunnel: c.use_ssh_tunnel || false,
          ssh_mode: c.ssh_mode || 'manual',
          ssh_server_name: c.ssh_server_name || '',
          ssh_host: c.ssh_host || '',
          ssh_port: c.ssh_port || 22,
          ssh_username: c.ssh_username || '',
          ssh_password: '',
          ssh_key_path: c.ssh_key_path || '',
          ssh_key_passphrase: '',
          ssh_remote_bind_host: c.ssh_remote_bind_host || '',
          ssh_target_server_name: c.ssh_target_server_name || '',
          ssh_target_host: c.ssh_target_host || '',
          ssh_target_port: c.ssh_target_port || 22,
          ssh_target_username: c.ssh_target_username || '',
          ssh_target_password: '',
          ssh_target_key_path: c.ssh_target_key_path || '',
          ssh_target_key_passphrase: '',
          allow_dml: c.allow_dml || false,
          allowed_dml_types: c.allowed_dml_types || ['insert', 'update', 'delete'],
          allowed_tables_text: (c.allowed_tables || []).join(', '),
          blocked_tables_text: (c.blocked_tables || []).join(', '),
          max_affected_rows_default: c.max_affected_rows_default || 100,
          require_dml_reason: c.require_dml_reason !== false,
        })
        setShowConnForm(true)
        if (bastionServers.length === 0) loadBastionServers()
        if (sshKeys.length === 0) loadSshKeys()
      }}
      onUploadSshKeyContent={handleUploadSshKeyContent}
      onDeleteSshKeyContent={handleDeleteSshKeyContent}
    />
  )

  const renderCleanupJobs = () => (
    <CleanupJobsTab
      jobStats={jobStats}
      jobSearch={jobSearch}
      statusFilter={statusFilter}
      jobsLoading={jobsLoading}
      showJobForm={showJobForm}
      jobForm={jobForm}
      connections={connections}
      tableOptions={tableOptions}
      columnOptions={columnOptions}
      tablesLoading={tablesLoading}
      columnsLoading={columnsLoading}
      jobSubmitting={jobSubmitting}
      filteredJobs={filteredJobs}
      inputStyle={inputStyle}
      selectStyle={selectStyle}
      setJobSearch={setJobSearch}
      setStatusFilter={setStatusFilter}
      setJobForm={setJobForm}
      loadJobs={loadJobs}
      onShowCreate={() => { setShowJobForm(true); setJobForm({ ...defaultJobForm }); setTableOptions([]); setColumnOptions([]) }}
      onConnectionChange={handleJobFormConnChange}
      onTableChange={handleJobFormTableChange}
      onSubmit={handleJobSubmit}
      onCancelForm={() => setShowJobForm(false)}
      onOpenJob={openJobDetail}
    />
  )

  if (!user) return <div style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '40px' }}>请先登录</div>

  return (
    <div className="page-stack database-workbench page-enter">
      <PageHeader
        title="数据库工作台"
        description="统一承载业务库查询、受控 SQL 执行、连接配置、数据清理和本地 OPS 库导出"
        breadcrumbs={[
          { label: '基础设施', href: '/database' },
          { label: '数据库工作台' },
        ]}
      />
      <section className="hero-panel database-workbench-hero">
        <div>
          <p className="eyebrow">Unified Database Workbench</p>
          <h1>数据库工作台</h1>
          <p className="hero-subtitle">统一承载业务库查询、受控 SQL 执行、连接配置、数据清理和本地 OPS 库导出。写操作必须先预检、再一键确认并进入审计。</p>
        </div>
        <div className="hero-actions database-hero-metrics">
          <span><strong>{connections.length}</strong><small>连接</small></span>
          <span><strong>{jobs.length}</strong><small>评估任务</small></span>
          <span><strong>{dbContext.connectionId ? '已选' : '未选'}</strong><small>共享上下文</small></span>
        </div>
      </section>

      {connLoading && connections.length === 0 && (
        <Skeleton type="card" count={3} />
      )}

      <nav className="tool-tabs database-tabs" aria-label="数据库工作台功能区">
        {DATABASE_TABS.map((tab) => (
          <button key={tab.key} className={activeTab === tab.key ? 'active' : ''} onClick={() => openTab(tab.key)}>
            <strong>{tab.label}</strong>
            <span>{tab.hint}</span>
          </button>
        ))}
      </nav>

      {message && <div className="card" style={{ background: 'var(--success-surface)', color: 'var(--success)', fontSize: '14px' }}>{message}</div>}
      {errorMsg && <div className="card" style={{ background: 'var(--danger-surface)', color: 'var(--danger)', fontSize: '14px' }}>{errorMsg}</div>}

      {activeTab === 'query' && <SqlQueryPage embedded sharedContext={dbContext} onSharedContextChange={updateDbContext} externalConnections={connections} />}
      {activeTab === 'execute' && (
        isAdmin
          ? <DatabaseExecutePanel connections={connections} sharedContext={dbContext} onSharedContextChange={updateDbContext} onRunValidationQuery={runValidationQuery} />
          : <div className="card" style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '40px' }}>SQL 写操作仅限管理员</div>
      )}
      {activeTab === 'local' && <LocalOpsQueryPanel />}
      {activeTab === 'connections' && (
        isOperator
          ? renderConnections()
          : <div className="card" style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '40px' }}>数据库连接管理需要运维权限</div>
      )}
      {activeTab === 'cleanup' && (
        isOperator ? (
          <section className="maintenance-cleanup-content database-cleanup-single">
            {selectedJob && jobDetail ? (
              <CleanupJobDetail
                jobDetail={jobDetail}
                dryRunResult={dryRunResult}
                dryRunLoading={dryRunLoading}
                startPlan={startPlan}
                setConfirmText={setConfirmText}
                actionLoading={actionLoading}
                isAdmin={isAdmin}
                jobBatches={jobBatches}
                jobEvents={jobEvents}
                onBack={() => { setSelectedJob(null); setJobDetail(null) }}
                onDryRun={handleDryRun}
                onJobAction={handleJobAction}
              />
            ) : renderCleanupJobs()}
          </section>
        ) : <div className="card" style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '40px' }}>数据清理任务需要运维权限</div>
      )}
      <ConfirmDialog
        open={Boolean(confirmState)}
        title={confirmState?.title || ''}
        description={confirmState?.description}
        confirmLabel={confirmState?.confirmLabel || '确认'}
        danger={confirmState?.danger}
        onCancel={() => closeConfirm(false)}
        onConfirm={() => closeConfirm(true)}
      />
    </div>
  )
}
