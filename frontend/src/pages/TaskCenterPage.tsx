import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { deployment, taskCenter } from '../api'
import { ROUTES } from '../routes'
import { EmptyState, PageHeader, StatusBadge, FavoriteButton } from '../components/ui'
import { useSmartPolling } from '../hooks/useSmartPolling'

type TaskItem = {
  kind: string
  id: string
  title: string
  status: string
  operator: string
  target: string
  started_at?: string
  finished_at?: string
  duration_ms?: number
  detail?: Record<string, any>
}

const KIND_LABEL: Record<string, string> = {
  deploy: '发布',
  sql: 'SQL',
  db: '数据库',
  cleanup: '清理',
  tool: '工具任务',
  mcp: 'MCP',
  file: '文件',
}

function formatTime(value?: string) {
  if (!value) return '-'
  try { return new Date(value).toLocaleString() } catch { return value }
}

function formatDuration(value?: number) {
  if (!value && value !== 0) return '-'
  if (value < 1000) return `${value}ms`
  const seconds = Math.round(value / 100) / 10
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return `${minutes}m ${rest}s`
}

function isLiveStatus(value?: string) {
  const status = String(value || '').toLowerCase()
  return ['queued', 'pending', 'pending_approval', 'draft', 'submitted', 'running', 'executing'].includes(status)
}

function timeSummary(item: TaskItem) {
  const start = formatTime(item.started_at)
  const finish = item.finished_at ? formatTime(item.finished_at) : (isLiveStatus(item.status) ? '进行中' : '-')
  return { start, finish, duration: formatDuration(item.duration_ms) }
}

export default function TaskCenterPage() {
  const [items, setItems] = useState<TaskItem[]>([])
  const [loading, setLoading] = useState(false)
  const [kind, setKind] = useState('')
  const [status, setStatus] = useState('')
  const [selected, setSelected] = useState<any>(null)
  const [error, setError] = useState('')
  const [actionMsg, setActionMsg] = useState('')

  const stats = useMemo(() => {
    const result = { total: items.length, running: 0, failed: 0, pending: 0 }
    items.forEach((item) => {
      const s = (item.status || '').toLowerCase()
      if (['running', 'executing'].includes(s)) result.running += 1
      if (['failed', 'error'].includes(s)) result.failed += 1
      if (['queued', 'pending', 'pending_approval', 'draft', 'submitted'].includes(s)) result.pending += 1
    })
    return result
  }, [items])

  const sortedItems = useMemo(() => {
    const priority = (item: TaskItem) => {
      const s = String(item.status || '').toLowerCase()
      if (['running', 'executing'].includes(s)) return 0
      if (['queued', 'pending', 'pending_approval', 'draft', 'submitted'].includes(s)) return 1
      if (['failed', 'error'].includes(s)) return 2
      return 3
    }
    const timestamp = (item: TaskItem) => new Date(item.started_at || item.finished_at || '').getTime() || 0
    return [...items].sort((a, b) => priority(a) - priority(b) || timestamp(b) - timestamp(a))
  }, [items])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const res: any = await taskCenter.list({ kind: kind || undefined, status: status || undefined, limit: 200 })
      setItems(res.data?.items || [])
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setLoading(false)
    }
  }, [kind, status])

  async function retryDeploy(item: TaskItem) {
    if (item.kind !== 'deploy') return
    const confirmProduction = item.detail?.environment && ['prod', 'production', '生产'].includes(String(item.detail.environment).toLowerCase())
    try {
      await deployment.retry(item.id, confirmProduction ? { confirm_production: true } : {})
      setActionMsg('已发起重试任务')
      await load()
    } catch (e: any) {
      setActionMsg(e?.message || String(e))
    }
  }

  async function openDetail(item: TaskItem) {
    try {
      const res: any = await taskCenter.detail(item.kind, item.id)
      setSelected(res.data)
    } catch (e: any) {
      setSelected({ error: e?.message || String(e) })
    }
  }

  const hasLiveItems = stats.running > 0 || stats.pending > 0
  const { start: startPolling, stop: stopPolling } = useSmartPolling(load, {
    activeMs: 5000,
    hiddenMs: 30000,
    idleMs: 30000,
    maxBackoffMs: 60000,
  })

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (hasLiveItems) startPolling({ activeMs: 5000, hiddenMs: 30000 })
    else stopPolling()
    return () => stopPolling()
  }, [hasLiveItems, startPolling, stopPolling])

  return (
    <div className="page-container">
      <PageHeader
        title="任务中心"
        description="统一查看发布、SQL、清理与 MCP/工具任务，跟踪高风险操作的进度、结果和审计记录。"
        actions={<div className="task-header-actions" style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}><FavoriteButton url={ROUTES.tasks} label="任务中心" category="tasks" /><Link className="btn btn-subtle" to={ROUTES.audit}>审计日志</Link><Link className="btn btn-subtle" to={ROUTES.reports}>报告中心</Link><span className={`auto-refresh-pill ${hasLiveItems ? 'active' : ''}`}>{hasLiveItems ? '自动刷新中' : '空闲'}</span><button className="btn primary" onClick={load} disabled={loading}>{loading ? '刷新中...' : '刷新'}</button></div>}
      />

      <div className="grid-4" style={{ marginBottom: 16 }}>
        <div className="stat-card"><div className="stat-label">总任务</div><div className="stat-value">{stats.total}</div></div>
        <div className="stat-card"><div className="stat-label">运行中</div><div className="stat-value">{stats.running}</div></div>
        <div className="stat-card"><div className="stat-label">失败</div><div className="stat-value">{stats.failed}</div></div>
        <div className="stat-card"><div className="stat-label">待处理</div><div className="stat-value">{stats.pending}</div></div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="form-grid">
          <label>类型
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">全部</option>
              <option value="deploy">发布</option>
              <option value="sql">SQL</option>
              <option value="db">数据库</option>
              <option value="cleanup">清理</option>
              <option value="tool">工具任务</option>
              <option value="mcp">MCP</option>
              <option value="file">文件</option>
            </select>
          </label>
          <label>状态
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">全部</option>
              <option value="queued">排队中</option>
              <option value="running">运行中</option>
              <option value="success">成功</option>
              <option value="failed">失败</option>
              <option value="pending_approval">待审批</option>
            </select>
          </label>
          <div style={{ display: 'flex', alignItems: 'end' }}><button className="btn" onClick={load}>筛选</button></div>
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}
      {actionMsg && <div className="alert">{actionMsg}</div>}
      {!items.length && !loading ? <EmptyState title="暂无任务" description="执行发布、SQL、清理或 MCP/工具任务后会在这里统一展示。" /> : (
        <div className="card table-card">
          <table className="data-table">
            <thead><tr><th>类型</th><th>标题</th><th>状态</th><th>风险</th><th>目标</th><th>操作人</th><th>时间</th><th>操作</th></tr></thead>
            <tbody>
              {sortedItems.map((item) => {
                const times = timeSummary(item)
                return (
                <tr key={`${item.kind}-${item.id}`} className={[isLiveStatus(item.status) ? 'task-row-live' : '', ['failed', 'error'].includes(String(item.status || '').toLowerCase()) ? 'task-row-failed' : ''].filter(Boolean).join(' ') || undefined}>
                  <td>{KIND_LABEL[item.kind] || item.kind}</td>
                  <td>{item.title}</td>
                  <td><StatusBadge value={item.status} /></td>
                  <td>{item.detail?.risk?.risk_level || item.detail?.risk_level || item.detail?.source_tool || '-'}</td>
                  <td>{item.target}</td>
                  <td>{item.operator}</td>
                  <td>
                    <div className="task-time-cell">
                      <strong>{times.start}</strong>
                      <span>{times.finish}</span>
                      <em>{times.duration}</em>
                    </div>
                  </td>
                  <td style={{ display: 'flex', gap: 8 }}>
                    <button className="btn small" onClick={() => openDetail(item)}>详情</button>
                    {item.kind === 'deploy' && ['failed', 'success', 'cancelled'].includes(item.status) && <button className="btn small" onClick={() => retryDeploy(item)}>重试</button>}
                    {item.kind === 'deploy' && <a className="btn small" href={deployment.reportTextUrl(item.id)} target="_blank" rel="noreferrer">报告</a>}
                  </td>
                </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {selected && (
        <div className="card" style={{ marginTop: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
            <h3>任务详情</h3>
            <button className="btn small" onClick={() => setSelected(null)}>关闭</button>
          </div>
          {selected.error ? <div className="alert alert-error">{selected.error}</div> : (
            <div>
              {selected.kind === 'tool' && selected.item && (
                <div className="grid-4" style={{ marginBottom: 12 }}>
                  <div className="stat-card"><div className="stat-label">工具</div><div className="stat-value" style={{ fontSize: 14 }}>{selected.item.detail?.source_tool || '-'}</div></div>
                  <div className="stat-card"><div className="stat-label">进度</div><div className="stat-value">{selected.item.detail?.progress ?? 0}%</div></div>
                  <div className="stat-card"><div className="stat-label">风险</div><div className="stat-value" style={{ fontSize: 14 }}>{selected.item.detail?.risk_level || '-'}</div></div>
                  <div className="stat-card"><div className="stat-label">审计</div><div className="stat-value" style={{ fontSize: 14 }}>{selected.item.detail?.audit_id || '-'}</div></div>
                </div>
              )}
              <pre className="code-block">{JSON.stringify(selected, null, 2)}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
