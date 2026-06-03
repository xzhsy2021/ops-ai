import { useEffect, useMemo, useState } from 'react'
import { maintenance } from '../api'
import { DataTable } from '../components/ui'

function pickData<T = any>(res: any, fallback: T): T {
  if (!res) return fallback
  if (Array.isArray(res)) return res as T
  if (res.data !== undefined) return res.data as T
  return res as T
}

function getErrorMessage(error: unknown, fallback: string) {
  if (typeof error === 'string') return error
  if (error instanceof Error && error.message) return error.message
  return fallback
}


function getErrorDiagnostics(message: string) {
  const lower = message.toLowerCase()
  if (message.includes('database_connections.description') || message.includes('no such column')) {
    return {
      title: '数据库结构迁移未完成',
      cause: '后端模型已升级，但当前 ops.db 仍是旧表结构。',
      action: '请确认 app/db/base.py 迁移已应用，并重启后端；如仍失败，备份 ops.db 后执行迁移脚本。',
      tone: 'danger',
    }
  }
  if (lower.includes('backend_offline') || lower.includes('network') || lower.includes('failed to load')) {
    return {
      title: '后端连接不可用',
      cause: '前端无法连接 /api/v2，常见原因是 VITE_API_BASE_URL 指向 127.0.0.1:8000 或代理未生效。',
      action: '建议设置 VITE_API_BASE_URL=/api/v2，并检查 nginx / Vite proxy 与后端 /readyz。',
      tone: 'danger',
    }
  }
  if (lower.includes('cors')) {
    return {
      title: '跨域或代理配置异常',
      cause: '浏览器拦截了 API 请求，通常是前端绕过代理直连后端造成。',
      action: '使用相对 API 路径 /api/v2，避免浏览器从 3000 直接请求 8000。',
      tone: 'warning',
    }
  }
  return {
    title: '请求失败',
    cause: '接口返回异常，可能是权限、连接配置或 SQL 语句问题。',
    action: '请查看后端日志中的最新 traceback，或先刷新连接列表。',
    tone: 'warning',
  }
}

function isSelectLike(sql: string) {
  return /^\s*(select|show|describe|desc|explain|with)\b/i.test(sql)
}

export type SharedDatabaseContext = {
  connectionId?: string
  databaseName?: string
  maxRows?: number
  timeoutSeconds?: number
  maxAffectedRows?: number
  sql?: string
}

type SqlQueryPageProps = {
  embedded?: boolean
  sharedContext?: SharedDatabaseContext
  onSharedContextChange?: (patch: Partial<SharedDatabaseContext>) => void
  externalConnections?: any[]
}

export default function SqlQueryPage({ embedded = false, sharedContext, onSharedContextChange, externalConnections }: SqlQueryPageProps = {}) {
  const [connections, setConnections] = useState<any[]>(externalConnections || [])
  const [connectionId, setConnectionId] = useState(sharedContext?.connectionId || '')
  const [databaseName, setDatabaseName] = useState(sharedContext?.databaseName || '')
  const [sql, setSql] = useState('SELECT 1')
  const [limit, setLimit] = useState(sharedContext?.maxRows || 100)
  const [timeoutSeconds, setTimeoutSeconds] = useState(sharedContext?.timeoutSeconds || 30)
  const [preview, setPreview] = useState<any>(null)
  const [result, setResult] = useState<any>(null)
  const [history, setHistory] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const [connectionsLoading, setConnectionsLoading] = useState(false)

  const selectedConn = useMemo(
    () => connections.find((c) => c.id === connectionId),
    [connections, connectionId]
  )

  useEffect(() => {
    if (externalConnections) setConnections(externalConnections)
  }, [externalConnections])

  useEffect(() => {
    if (!sharedContext) return
    if (sharedContext.connectionId !== undefined && sharedContext.connectionId !== connectionId) setConnectionId(sharedContext.connectionId)
    if (sharedContext.databaseName !== undefined && sharedContext.databaseName !== databaseName) setDatabaseName(sharedContext.databaseName)
    if (sharedContext.maxRows !== undefined && sharedContext.maxRows !== limit) setLimit(sharedContext.maxRows)
    if (sharedContext.timeoutSeconds !== undefined && sharedContext.timeoutSeconds !== timeoutSeconds) setTimeoutSeconds(sharedContext.timeoutSeconds)
    if (sharedContext.sql !== undefined && sharedContext.sql !== sql) {
      setSql(sharedContext.sql)
      setPreview(null)
      setResult(null)
    }
  }, [sharedContext?.connectionId, sharedContext?.databaseName, sharedContext?.maxRows, sharedContext?.timeoutSeconds, sharedContext?.sql])

  const updateSharedContext = (patch: Partial<SharedDatabaseContext>) => {
    onSharedContextChange?.(patch)
  }

  const readonlySafe = isSelectLike(sql)
  const cols: string[] = result?.columns || []
  const rows: any[] = result?.rows || []
  const [resultPage, setResultPage] = useState(1)
  const resultPageSize = 50
  const resultTotalPages = Math.max(1, Math.ceil(rows.length / resultPageSize))
  const paginatedRows = rows.slice((resultPage - 1) * resultPageSize, resultPage * resultPageSize)
  const diagnostics = message ? getErrorDiagnostics(message) : null
  const dbType = selectedConn?.db_type || 'mysql'
  const isPg = dbType === 'postgresql' || dbType === 'postgres'
  const querySnippets = [
    { label: 'SELECT 模板', sql: `SELECT *
FROM your_table
LIMIT 100` },
    { label: '表结构', sql: isPg ? "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name = 'your_table'" : 'DESCRIBE your_table' },
    { label: '执行计划', sql: 'EXPLAIN SELECT * FROM your_table WHERE id = 1' },
    { label: '表列表', sql: isPg ? "SELECT tablename FROM pg_tables WHERE schemaname = 'public'" : 'SHOW TABLES' },
  ]

  const loadConnections = () => {
    if (externalConnections) {
      setConnections(externalConnections)
      if (!connectionId && externalConnections[0]) {
        setConnectionId(externalConnections[0].id)
        setDatabaseName(externalConnections[0].database_name || '')
        updateSharedContext({ connectionId: externalConnections[0].id, databaseName: externalConnections[0].database_name || '' })
      }
      return
    }
    setConnectionsLoading(true)
    maintenance.connections.list().then((res: any) => {
      const list = pickData<any[]>(res, [])
      setConnections(list)
      if (!connectionId && list[0]) {
        setConnectionId(list[0].id)
        setDatabaseName(list[0].database_name || '')
        updateSharedContext({ connectionId: list[0].id, databaseName: list[0].database_name || '' })
      }
      setMessage('')
    }).catch((e) => {
      setMessage(getErrorMessage(e, '加载数据库连接失败，请检查后端 /api/v2/maintenance/connections'))
    }).finally(() => setConnectionsLoading(false))
  }

  const loadHistory = () => {
    maintenance.query.history(connectionId || undefined, 30, 0)
      .then((res: any) => {
        const data = pickData<any>(res, [])
        setHistory(Array.isArray(data) ? data : (data.items || []))
      })
      .catch(() => {})
  }

  useEffect(() => { loadConnections() }, [])
  useEffect(() => { if (connectionId) loadHistory() }, [connectionId])
  useEffect(() => {
    if (selectedConn?.database_name) {
      setDatabaseName(selectedConn.database_name)
      updateSharedContext({ connectionId: selectedConn.id, databaseName: selectedConn.database_name })
    }
  }, [selectedConn?.id])

  const doPreview = async () => {
    if (!connectionId || !sql.trim()) return null
    setLoading(true)
    setMessage('')
    setResult(null)
    try {
      const res: any = await maintenance.query.preview(connectionId, { sql, database_name: databaseName, limit, timeout_seconds: timeoutSeconds })
      const data = pickData<any>(res, null)
      setPreview(data)
      return data
    } catch (e: any) {
      setMessage(getErrorMessage(e, 'SQL 预览失败'))
      return null
    } finally {
      setLoading(false)
    }
  }

  const doExecute = async () => {
    if (!connectionId || !sql.trim()) return
    setLoading(true)
    setMessage('')
    try {
      const res: any = await maintenance.query.execute(connectionId, { sql, database_name: databaseName, limit, timeout_seconds: timeoutSeconds })
      const data = pickData<any>(res, null)
      setResult(data)
      setPreview(data)
      loadHistory()
    } catch (e: any) {
      setMessage(getErrorMessage(e, 'SQL 执行失败'))
    } finally {
      setLoading(false)
    }
  }

  const applyHistory = (item: any) => {
    setSql(item.sql_text || '')
    setDatabaseName(item.database_name || databaseName)
    setPreview(null)
    setResult(null)
  }

  const handleExportCSV = () => {
    const csv = ['\uFEFF' + cols.join(','), ...rows.map(r => cols.map(c => JSON.stringify(r[c] ?? '')).join(','))].join('\n')
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `query_result_${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className={`sql-workbench page-enter ${embedded ? 'sql-workbench--embedded' : ''}`}>
      {!embedded && <section className="sql-hero sql-hero--compact glass-panel">
        <div>
          <span className="eyebrow">Read-only Database Console</span>
          <h1>数据库 SQL 查询</h1>
          <p>选择连接 → 编写 SQL → 预览校验 → 执行查询。只读语句会自动加行数限制并写入审计历史。</p>
        </div>
        <div className="sql-hero-metrics" aria-label="查询工作台状态">
          <div className="metric-pill">
            <span>{connections.length}</span>
            <small>连接</small>
          </div>
          <div className="metric-pill">
            <span>{limit}</span>
            <small>最大行数</small>
          </div>
          <div className={`metric-pill ${readonlySafe ? 'metric-pill--safe' : 'metric-pill--risk'}`}>
            <span>{readonlySafe ? 'SAFE' : 'CHECK'}</span>
            <small>只读校验</small>
          </div>
        </div>
      </section>}

      {message && diagnostics && (
        <div className={`alert-card alert-card--${diagnostics.tone}`} role="alert">
          <strong>{diagnostics.title}</strong>
          <span>{message}</span>
          <small>{diagnostics.cause}</small>
          <small>{diagnostics.action}</small>
        </div>
      )}

      <div className="sql-layout">
        <main className="sql-main-column">
          <section className="glass-card sql-connection-card">
            <div className="section-title-row">
              <div>
                <h2>连接上下文</h2>
                <p>选择目标连接、数据库和返回上限。</p>
              </div>
              <button className="btn btn-subtle" onClick={loadConnections} disabled={connectionsLoading}>
                {connectionsLoading ? '刷新中...' : '刷新连接'}
              </button>
            </div>
            <div className="sql-form-grid">
              <label className="field-label">数据库连接
                <select value={connectionId} onChange={(e) => { const next = e.target.value; const conn = connections.find((c) => c.id === next); setConnectionId(next); if (conn?.database_name) setDatabaseName(conn.database_name); updateSharedContext({ connectionId: next, databaseName: conn?.database_name || databaseName }) }}>
                  <option value="">请选择连接</option>
                  {connections.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.environment} · {c.host}</option>)}
                </select>
              </label>
              <label className="field-label">数据库名
                <input value={databaseName} onChange={(e) => { setDatabaseName(e.target.value); updateSharedContext({ databaseName: e.target.value }) }} placeholder="默认使用连接配置中的库名" />
              </label>
              <label className="field-label">最大行数
                <input type="number" min={1} max={1000} value={limit} onChange={(e) => { const next = Number(e.target.value); setLimit(next); updateSharedContext({ maxRows: next }) }} />
              </label>
              <label className="field-label">超时秒数
                <input type="number" min={1} max={120} value={timeoutSeconds} onChange={(e) => { const next = Number(e.target.value); setTimeoutSeconds(next); updateSharedContext({ timeoutSeconds: next }) }} />
              </label>
            </div>
            {selectedConn && (
              <div className="sql-context-strip">
                <span>{selectedConn.environment}</span>
                <code>{selectedConn.host}:{selectedConn.port}</code>
                <span>{selectedConn.db_type || 'mysql'}</span>
                {selectedConn.use_ssh_tunnel && <span>SSH 代理</span>}
              </div>
            )}
          </section>

          <section className="glass-card sql-editor-card">
            <div className="section-title-row">
              <div>
                <h2>SQL Editor</h2>
                <p>建议先预览校验，再执行查询。</p>
              </div>
              <span className={`status-badge ${readonlySafe ? 'status-badge--success' : 'status-badge--danger'}`}>
                {readonlySafe ? '只读语句' : '需检查语句'}
              </span>
            </div>
            <div className="sql-snippet-row">
              {querySnippets.map(item => (
                <button key={item.label} type="button" className="sql-snippet-btn" onClick={() => { setSql(item.sql); setPreview(null); setResult(null) }}>
                  {item.label}
                </button>
              ))}
              <button type="button" className="sql-snippet-btn sql-snippet-btn--muted" onClick={() => navigator.clipboard?.writeText(sql)}>复制 SQL</button>
              <button type="button" className="sql-snippet-btn sql-snippet-btn--muted" onClick={() => { setSql(''); setPreview(null); setResult(null) }}>清空</button>
            </div>
            <textarea
              className="sql-editor"
              value={sql}
              spellCheck={false}
              onChange={(e) => { setSql(e.target.value); setPreview(null); setResult(null) }}
              aria-label="SQL 编辑器"
            />
            <div className="sql-actions">
              <button className="btn btn-subtle" onClick={doPreview} disabled={loading || !connectionId || !sql.trim()}>
                预览校验
              </button>
              <button className="btn btn-primary" onClick={doExecute} disabled={loading || !connectionId || !sql.trim()}>
                {loading ? '执行中...' : '执行查询'}
              </button>
            </div>
          </section>

          {preview && (
            <section className="glass-card sql-preview-card page-enter">
              <div className="section-title-row">
                <div>
                  <h2>执行预览</h2>
                  <p>后端生成的实际执行语句。</p>
                </div>
                <div className="preview-meta">
                  <span>{preview.query_type || '-'}</span>
                  <span>{String(preview.readonly ?? true)}</span>
                  <span>limit {preview.limit || limit}</span>
                  <span>{preview.timeout_seconds || timeoutSeconds}s</span>
                </div>
              </div>
              {(preview.blockers || []).map((w: string) => <div key={w} className="alert-card alert-card--danger">⛔ {w}</div>)}
              {(preview.warnings || preview.risk?.warnings || []).map((w: string) => <div key={w} className="alert-card alert-card--warning">⚠ {w}</div>)}
              {(preview.suggestions || preview.risk?.suggestions || []).length > 0 && (
                <div className="alert-card alert-card--info">
                  <strong>执行建议</strong>
                  <ul style={{ margin: '6px 0 0 18px' }}>
                    {(preview.suggestions || preview.risk?.suggestions || []).map((x: string) => <li key={x}>{x}</li>)}
                  </ul>
                </div>
              )}
              {(preview.protections || []).length > 0 && (
                <div className="sql-context-strip" style={{ marginBottom: 12 }}>
                  {(preview.protections || []).map((x: string) => <span key={x}>{x}</span>)}
                </div>
              )}
              <pre className="sql-preview-code">{preview.executable_sql || sql}</pre>
            </section>
          )}

          {result && (
            <section className="glass-card sql-result-card page-enter">
              <div className="section-title-row">
                <div>
                  <h2>查询结果</h2>
                  <p>{result.row_count ?? rows.length} 行 · {result.duration_ms ?? '-'} ms</p>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  {rows.length > 0 && (
                    <button className="btn btn-subtle" onClick={handleExportCSV}>导出 CSV</button>
                  )}
                </div>
              </div>
              <div className="sql-table-wrap">
                <DataTable<Record<string, any>>
                  rows={paginatedRows}
                  rowKey={(_, index) => String(index)}
                  columns={cols.map((column) => ({ key: column, title: column, render: (row) => <span title={String(row[column] ?? '')}>{String(row[column] ?? '')}</span> }))}
                />
                {cols.length === 0 && <div className="empty-state"><strong>没有返回表格数据</strong><span>查询执行成功，但没有可展示的列。</span></div>}
              </div>
              {rows.length > resultPageSize && (
                <div className="table-pagination">
                  <span className="table-pagination-info">
                    共 {rows.length} 条，第 {resultPage}/{resultTotalPages} 页
                  </span>
                  <div className="table-pagination-actions">
                    <button
                      className="btn btn-subtle"
                      disabled={resultPage <= 1}
                      onClick={() => setResultPage(resultPage - 1)}
                    >
                      上一页
                    </button>
                    <button
                      className="btn btn-subtle"
                      disabled={resultPage >= resultTotalPages}
                      onClick={() => setResultPage(resultPage + 1)}
                    >
                      下一页
                    </button>
                  </div>
                </div>
              )}
            </section>
          )}
        </main>

        <aside className="sql-side-column">
          <section className="glass-card">
            <div className="section-title-row section-title-row--compact">
              <div>
                <h2>当前连接</h2>
                <p>上下文快照</p>
              </div>
            </div>
            {selectedConn ? (
              <div className="connection-summary">
                <div><span>名称</span><strong>{selectedConn.name}</strong></div>
                <div><span>环境</span><strong>{selectedConn.environment}</strong></div>
                <div><span>地址</span><strong>{selectedConn.host}:{selectedConn.port}</strong></div>
                <div><span>类型</span><strong>{selectedConn.db_type || 'mysql'}</strong></div>
              </div>
            ) : <div className="empty-state"><strong>未选择连接</strong><span>请先创建或选择数据库连接。</span></div>}
          </section>

          <section className="glass-card sql-history-card">
            <div className="section-title-row section-title-row--compact">
              <div>
                <h2>最近查询</h2>
                <p>点击可快速回填 SQL。</p>
              </div>
            </div>
            <div className="history-list">
              {history.map((h) => (
                <button key={h.id} className="history-item" onClick={() => applyHistory(h)}>
                  <span className={`status-dot ${h.status === 'success' ? 'online' : 'offline'}`} />
                  <span>
                    <strong>{h.connection_name || '-'}</strong>
                    <small>{h.status} · {h.row_count ?? 0} 行 · {h.duration_ms ?? '-'} ms</small>
                    <code>{h.sql_text}</code>
                  </span>
                </button>
              ))}
              {history.length === 0 && <div className="empty-state"><strong>暂无查询历史</strong><span>执行一次查询后会显示在这里。</span></div>}
            </div>
          </section>
        </aside>
      </div>
    </div>
  )
}
