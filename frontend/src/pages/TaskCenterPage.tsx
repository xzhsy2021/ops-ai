import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { deployment, taskCenter } from '../api'
import { ROUTES } from '../routes'
import { EmptyState, PageHeader, StatusBadge, FavoriteButton, RiskConfirmDialog } from '../components/ui'
import { RiskBadge } from '../components/agent'
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

function taskKey(item: Pick<TaskItem, 'kind' | 'id'>) {
  return `${item.kind}:${item.id}`
}

function getTaskRiskLevel(item: TaskItem): string {
  const detail = item.detail || {}
  return detail?.risk?.risk_level || detail?.risk_level || ''
}

function getTaskFailureReason(item: TaskItem): string {
  if (!['failed', 'error'].includes(String(item.status || '').toLowerCase())) return ''
  const detail = item.detail || {}
  return detail?.error_message || detail?.failure_reason || ''
}

function timeSummary(item: TaskItem) {
  const start = formatTime(item.started_at)
  const finish = item.finished_at ? formatTime(item.finished_at) : (isLiveStatus(item.status) ? '进行中' : '-')
  return { start, finish, duration: formatDuration(item.duration_ms) }
}

function TaskPagination({
  total,
  pageSize,
  offset,
  onOffsetChange,
  onPageSizeChange,
}: {
  total: number
  pageSize: number
  offset: number
  onOffsetChange: (next: number) => void
  onPageSizeChange: (next: number) => void
}) {
  const safeTotal = Math.max(0, Number(total || 0))
  const safePageSize = Math.max(1, Number(pageSize || 20))
  const safeOffset = Math.max(0, Number(offset || 0))
  const page = Math.floor(safeOffset / safePageSize) + 1
  const pages = Math.max(1, Math.ceil(safeTotal / safePageSize))
  const from = safeTotal === 0 ? 0 : safeOffset + 1
  const to = Math.min(safeOffset + safePageSize, safeTotal)

  return (
    <div className="pagination-bar">
      <span className="pagination-info">第 {page}/{pages} 页 · 显示 {from}-{to} / 共 {safeTotal} 条</span>
      <div className="pagination-controls">
        <label className="pagination-size-label">每页
          <select value={safePageSize} onChange={(e) => onPageSizeChange(Number(e.target.value))}>
            {[10, 20, 50, 100].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <button className="pagination-btn" disabled={safeOffset <= 0} onClick={() => onOffsetChange(0)}>首页</button>
        <button className="pagination-btn" disabled={safeOffset <= 0} onClick={() => onOffsetChange(Math.max(0, safeOffset - safePageSize))}>上一页</button>
        <button className="pagination-btn" disabled={safeOffset + safePageSize >= safeTotal} onClick={() => onOffsetChange(safeOffset + safePageSize)}>下一页</button>
        <button className="pagination-btn" disabled={safeOffset + safePageSize >= safeTotal} onClick={() => onOffsetChange(Math.max(0, (pages - 1) * safePageSize))}>末页</button>
      </div>
    </div>
  )
}

export default function TaskCenterPage() {
  const [items, setItems] = useState<TaskItem[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [pageSize, setPageSize] = useState(20)
  const [loading, setLoading] = useState(false)
  const [kind, setKind] = useState('')
  const [status, setStatus] = useState('')
  const [riskLevel, setRiskLevel] = useState('')
  const [selected, setSelected] = useState<any>(null)
  const [error, setError] = useState('')
  const [actionMsg, setActionMsg] = useState('')
  const [selectedTaskKeys, setSelectedTaskKeys] = useState<string[]>([])
  const [pendingTaskDeleteItems, setPendingTaskDeleteItems] = useState<TaskItem[]>([])
  const [deletingTasks, setDeletingTasks] = useState(false)

  const stats = useMemo(() => {
    const result = { total, running: 0, failed: 0, pending: 0 }
    items.forEach((item) => {
      const s = (item.status || '').toLowerCase()
      if (['running', 'executing'].includes(s)) result.running += 1
      if (['failed', 'error'].includes(s)) result.failed += 1
      if (['queued', 'pending', 'pending_approval', 'draft', 'submitted'].includes(s)) result.pending += 1
    })
    return result
  }, [items, total])

  const filteredItems = useMemo(() => {
    if (!riskLevel) return items
    return items.filter((item) => getTaskRiskLevel(item) === riskLevel)
  }, [items, riskLevel])

  const sortedItems = useMemo(() => {
    const priority = (item: TaskItem) => {
      const s = String(item.status || '').toLowerCase()
      if (['running', 'executing'].includes(s)) return 0
      if (['queued', 'pending', 'pending_approval', 'draft', 'submitted'].includes(s)) return 1
      if (['failed', 'error'].includes(s)) return 2
      return 3
    }
    const timestamp = (item: TaskItem) => new Date(item.started_at || item.finished_at || '').getTime() || 0
    return [...filteredItems].sort((a, b) => priority(a) - priority(b) || timestamp(b) - timestamp(a))
  }, [filteredItems])

  const selectedTaskKeySet = useMemo(() => new Set(selectedTaskKeys), [selectedTaskKeys])
  const selectableItems = useMemo(() => sortedItems.filter((item) => !isLiveStatus(item.status)), [sortedItems])
  const allPageTasksSelected = selectableItems.length > 0 && selectableItems.every((item) => selectedTaskKeySet.has(taskKey(item)))

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const res: any = await taskCenter.list({ kind: kind || undefined, status: status || undefined, limit: pageSize, offset })
      const nextItems = res.data?.items || []
      setItems(nextItems)
      setTotal(Number(res.data?.total || 0))
      const visibleKeys = new Set(nextItems.map((item: TaskItem) => taskKey(item)))
      setSelectedTaskKeys((prev) => prev.filter((key) => visibleKeys.has(key)))
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setLoading(false)
    }
  }, [kind, status, pageSize, offset])

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
      // axios 响应拦截器已 unwrap 到 res.data，所以 res 形如 {success, message, data: {kind, id, detail, request, result, ...}}
      const res: any = await taskCenter.detail(item.kind, item.id)
      const payload = res?.data ?? res
      setSelected(payload)
    } catch (e: any) {
      setSelected({ error: e?.message || String(e) })
    }
  }

  function toggleTaskSelection(item: TaskItem, checked: boolean) {
    const key = taskKey(item)
    setSelectedTaskKeys((prev) => {
      const next = new Set(prev)
      if (checked) next.add(key)
      else next.delete(key)
      return Array.from(next)
    })
  }

  function togglePageTaskSelection(checked: boolean) {
    const pageKeys = selectableItems.map(taskKey)
    setSelectedTaskKeys((prev) => {
      const next = new Set(prev)
      pageKeys.forEach((key) => {
        if (checked) next.add(key)
        else next.delete(key)
      })
      return Array.from(next)
    })
  }

  function openDeleteSelectedTasks() {
    const selectedItems = sortedItems.filter((item) => selectedTaskKeySet.has(taskKey(item)) && !isLiveStatus(item.status))
    if (selectedItems.length) setPendingTaskDeleteItems(selectedItems)
  }

  async function confirmDeleteTasks() {
    if (!pendingTaskDeleteItems.length) return
    setDeletingTasks(true)
    try {
      const itemsToDelete = pendingTaskDeleteItems.map((item) => ({ kind: item.kind, id: item.id }))
      const confirm_text = `DELETE TASKS ${itemsToDelete.length}`
      if (itemsToDelete.length === 1) {
        const only = itemsToDelete[0]
        await taskCenter.delete(only.kind, only.id, { confirm_text })
      } else {
        await taskCenter.deleteMany({ items: itemsToDelete, confirm_text })
      }
      const deletedKeys = new Set(itemsToDelete.map((item) => taskKey(item)))
      setSelectedTaskKeys((prev) => prev.filter((key) => !deletedKeys.has(key)))
      setPendingTaskDeleteItems([])
      setSelected((current: any) => {
        if (!current?.kind || !current?.id) return current
        return deletedKeys.has(taskKey(current)) ? null : current
      })
      setActionMsg(`已删除 ${itemsToDelete.length} 个任务`)
      await load()
    } catch (e: any) {
      setActionMsg(typeof e === 'string' ? e : e?.message || '删除任务失败')
    } finally {
      setDeletingTasks(false)
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

      <div className="task-center-toolbar">
        <div className="task-center-summary">
          <span><strong>{stats.total}</strong> 总任务</span>
          <span><strong>{stats.running}</strong> 本页运行</span>
          <span><strong>{stats.failed}</strong> 本页失败</span>
          <span><strong>{stats.pending}</strong> 本页待处理</span>
        </div>
        <div className="task-center-filters">
          <button className="btn btn-danger" disabled={!selectedTaskKeys.length} onClick={openDeleteSelectedTasks}>批量删除 ({selectedTaskKeys.length})</button>
          <label>类型
            <select value={kind} onChange={(e) => { setKind(e.target.value); setOffset(0) }}>
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
            <select value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0) }}>
              <option value="">全部</option>
              <option value="queued">排队中</option>
              <option value="running">运行中</option>
              <option value="success">成功</option>
              <option value="failed">失败</option>
              <option value="pending_approval">待审批</option>
            </select>
          </label>
          <label>风险
            <select value={riskLevel} onChange={(e) => { setRiskLevel(e.target.value); setOffset(0) }}>
              <option value="">全部</option>
              <option value="high">高风险</option>
              <option value="medium">中风险</option>
              <option value="low">低风险</option>
            </select>
          </label>
          <button className="btn" onClick={load}>筛选</button>
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}
      {actionMsg && <div className="alert">{actionMsg}</div>}
      {!items.length && !loading ? <EmptyState title="暂无任务" description="执行发布、SQL、清理或 MCP/工具任务后会在这里统一展示。" /> : (
        <div className="card table-card task-center-content" style={{ overflow: 'auto', padding: 0, maxHeight: 'calc(100vh - 280px)' }}>
          <table className="data-table">
            <thead><tr><th><input type="checkbox" aria-label="选择当前页任务" checked={allPageTasksSelected} disabled={!selectableItems.length} onChange={(e) => togglePageTaskSelection(e.target.checked)} /></th><th style={{ width: 80 }}>类型</th><th style={{ minWidth: 180 }}>标题</th><th style={{ width: 80 }}>状态</th><th style={{ width: 80 }}>风险</th><th style={{ minWidth: 180 }}>目标</th><th style={{ width: 100 }}>操作人</th><th style={{ width: 140 }}>时间</th><th style={{ minWidth: 220 }}>操作</th></tr></thead>
            <tbody>
              {sortedItems.map((item) => {
                const times = timeSummary(item)
                return (
                <tr key={`${item.kind}-${item.id}`} className={[isLiveStatus(item.status) ? 'task-row-live' : '', ['failed', 'error'].includes(String(item.status || '').toLowerCase()) ? 'task-row-failed' : ''].filter(Boolean).join(' ') || undefined}>
                  <td><input type="checkbox" aria-label={`选择任务 ${item.title}`} checked={selectedTaskKeySet.has(taskKey(item))} disabled={isLiveStatus(item.status)} onChange={(e) => toggleTaskSelection(item, e.target.checked)} /></td>
                  <td>{KIND_LABEL[item.kind] || item.kind}</td>
                  <td>
                    <div className="ellipsis" style={{ maxWidth: 260 }} title={item.title}>{item.title}</div>
                    {getTaskFailureReason(item) && (
                      <div className="task-failure-reason" title={getTaskFailureReason(item)}>
                        {getTaskFailureReason(item).length > 60
                          ? getTaskFailureReason(item).slice(0, 60) + '…'
                          : getTaskFailureReason(item)}
                      </div>
                    )}
                  </td>
                  <td><StatusBadge value={item.status} /></td>
                  <td>{(() => {
                    const rl = getTaskRiskLevel(item)
                    if (rl === 'high') return <RiskBadge level="alert" label="高风险" size="sm" />
                    if (rl === 'medium') return <RiskBadge level="warn" label="中风险" size="sm" />
                    if (rl === 'low') return <RiskBadge level="info" label="低风险" size="sm" />
                    return <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>-</span>
                  })()}</td>
                  <td><span className="ellipsis" style={{ maxWidth: 240 }} title={item.target || '-'}>{item.target || '-'}</span></td>
                  <td>{item.operator}</td>
                  <td>
                    <div className="task-time-cell">
                      <strong>{times.start}</strong>
                      <span>{times.finish}</span>
                      <em>{times.duration}</em>
                    </div>
                  </td>
                  <td className="task-actions-cell">
                    <button className="btn small" onClick={() => openDetail(item)}>详情</button>
                    {item.kind === 'deploy' && ['failed', 'success', 'cancelled'].includes(item.status) && <button className="btn small" onClick={() => retryDeploy(item)}>重试</button>}
                    {item.kind === 'deploy' && <a className="btn small" href={deployment.reportTextUrl(item.id)} target="_blank" rel="noreferrer">报告</a>}
                    <button className="btn small btn-danger" disabled={isLiveStatus(item.status)} onClick={() => setPendingTaskDeleteItems([item])}>删除</button>
                  </td>
                </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <TaskPagination
        total={total}
        pageSize={pageSize}
        offset={offset}
        onOffsetChange={setOffset}
        onPageSizeChange={(next) => { setPageSize(next); setOffset(0) }}
      />

      <RiskConfirmDialog
        open={pendingTaskDeleteItems.length > 0}
        title={pendingTaskDeleteItems.length > 1 ? '确认批量删除任务' : '确认删除任务'}
        description="将删除任务中心记录及其关联运行数据。运行中、排队中或待审批任务不可删除。"
        target={pendingTaskDeleteItems.length > 1 ? `已选择 ${pendingTaskDeleteItems.length} 个任务` : (pendingTaskDeleteItems[0] ? `${KIND_LABEL[pendingTaskDeleteItems[0].kind] || pendingTaskDeleteItems[0].kind} / ${pendingTaskDeleteItems[0].title}` : '-')}
        confirmText={`DELETE TASKS ${pendingTaskDeleteItems.length}`}
        value=""
        onValueChange={() => {}}
        onCancel={() => { if (!deletingTasks) setPendingTaskDeleteItems([]) }}
        onConfirm={confirmDeleteTasks}
        riskLevel="high"
        details={[
          { label: '数量', value: String(pendingTaskDeleteItems.length) },
          { label: '类型', value: Array.from(new Set(pendingTaskDeleteItems.map((item) => KIND_LABEL[item.kind] || item.kind))).join(', ') || '-' },
          { label: '状态', value: Array.from(new Set(pendingTaskDeleteItems.map((item) => item.status || '-'))).join(', ') || '-' },
          { label: '目标', value: pendingTaskDeleteItems.map((item) => item.target || item.id).slice(0, 5).join(', ') || '-' },
        ]}
        confirmButtonLabel={deletingTasks ? '删除中...' : '确认删除'}
        confirmDisabled={deletingTasks}
        confirmMode="one-click"
      />

      {selected && (
        <div className="task-detail-overlay" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) setSelected(null) }}>
          <div className="task-detail-modal" role="dialog" aria-modal="true" aria-label="任务详情">
            <div className="task-detail-header">
              <h3>任务详情</h3>
              <button className="btn small" onClick={() => setSelected(null)}>关闭</button>
            </div>
            <div className="task-detail-body">
              {selected.error ? <div className="alert alert-error">{selected.error}</div> : (
                <div>
                  <div className="grid-4" style={{ marginBottom: 12 }}>
                    <div className="stat-card"><div className="stat-label">类型</div><div className="stat-value" style={{ fontSize: 14 }}>{KIND_LABEL[selected.kind] || selected.kind || '-'}</div></div>
                    <div className="stat-card"><div className="stat-label">状态</div><div className="stat-value" style={{ fontSize: 14 }}>{selected.status || '-'}</div></div>
                    <div className="stat-card"><div className="stat-label">操作人</div><div className="stat-value" style={{ fontSize: 14 }}>{selected.operator || '-'}</div></div>
                    <div className="stat-card"><div className="stat-label">目标</div><div className="stat-value" style={{ fontSize: 14 }}>{selected.target || '-'}</div></div>
                  </div>
                  {selected.kind === 'tool' && (
                    <div className="grid-4" style={{ marginBottom: 12 }}>
                      <div className="stat-card"><div className="stat-label">工具</div><div className="stat-value" style={{ fontSize: 14 }}>{selected.source_tool || '-'}</div></div>
                      <div className="stat-card"><div className="stat-label">进度</div><div className="stat-value">{selected.progress ?? 0}%</div></div>
                      <div className="stat-card"><div className="stat-label">风险</div><div className="stat-value" style={{ fontSize: 14 }}>{selected.risk_level || '-'}</div></div>
                      <div className="stat-card"><div className="stat-label">审计</div><div className="stat-value" style={{ fontSize: 14 }}>{selected.audit_id || '-'}</div></div>
                    </div>
                  )}
                  {selected.error_message && <div className="alert alert-error">错误：{selected.error_message}</div>}
                  <pre className="code-block">{JSON.stringify(selected, null, 2)}</pre>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
