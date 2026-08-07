import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ROUTES } from '../routes'
import { systemHealth } from '../api'
import { useAuthStore, useBackendStore } from '../store'
import { EmptyState, Skeleton, StatusBadge, FavoriteButton } from '../components/ui'
import { useFavoriteStore } from '../stores/favoriteStore'
import { usePreferenceStore } from '../stores/preferenceStore'

type HealthTone = 'ok' | 'warn' | 'danger' | 'neutral'

type DashboardData = {
  generated_at?: string
  status?: string
  score?: number
  metrics?: Record<string, number>
  health?: any
  servers?: any
  deployments?: { by_status?: Record<string, number>; tasks_by_status?: Record<string, number>; recent?: any[]; running_tasks?: any[] }
  approvals?: { total?: number; cleanup_jobs?: number; tool_plans?: number }
  packages?: { total?: number; protected?: number; latest?: any }
  storage?: any
  runtime?: any
  risks?: Array<{ level: string; title: string; message: string; to: string }>
  quick_actions?: Array<{ title: string; description: string; to: string; level?: string }>
}

type WidgetId = 'health' | 'snapshot' | 'activity' | 'risks' | 'favorites' | 'search' | 'tasks' | 'deploy' | 'storage'
type WidgetSize = 1 | 2 | 3 | 4
type Widget = { id: WidgetId; size: WidgetSize; title: string; subtitle: string; visible: boolean }

const DEFAULT_WIDGETS: Widget[] = [
  { id: 'snapshot',  size: 2, title: '今日快照',     subtitle: '每日必看的运行指标', visible: true },
  { id: 'activity',  size: 2, title: '活动流',       subtitle: '最近部署 / 任务 / 巡检', visible: true },
  { id: 'risks',     size: 1, title: '待处理风险',   subtitle: '需关注事项优先级排序', visible: true },
  { id: 'tasks',     size: 1, title: '运行中任务',   subtitle: '正在执行的发布与巡检', visible: true },
  { id: 'health',    size: 1, title: '健康检查',     subtitle: '备份 / 磁盘 / SSH 池', visible: true },
  { id: 'favorites', size: 1, title: '收藏',         subtitle: '你置顶的页面与操作', visible: true },
  { id: 'search',    size: 1, title: '搜索历史',     subtitle: '最近的导航搜索词', visible: true },
  { id: 'deploy',    size: 1, title: '发布概览',     subtitle: '按状态分布的发布数据', visible: true },
  { id: 'storage',   size: 1, title: '存储',         subtitle: '本地 + 远程 文件占用', visible: true },
]

const STORAGE_KEY = 'dashboard:widget-order:v1'

function loadWidgetOrder(): Widget[] {
  if (typeof window === 'undefined') return DEFAULT_WIDGETS
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_WIDGETS
    const parsed = JSON.parse(raw) as Widget[]
    if (!Array.isArray(parsed)) return DEFAULT_WIDGETS
    // 补齐缺失的（按默认顺序）
    const ids = new Set(parsed.map((w) => w.id))
    const merged = [...parsed]
    DEFAULT_WIDGETS.forEach((w) => { if (!ids.has(w.id)) merged.push(w) })
    return merged.map((w) => ({ ...w, size: w.size || 1, visible: w.visible !== false }))
  } catch {
    return DEFAULT_WIDGETS
  }
}

function saveWidgetOrder(widgets: Widget[]) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(widgets))
  } catch {
    // 静默
  }
}

function fmtClock(date: Date) {
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(date)
}
function fmtDate(date: Date) {
  return new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'long' }).format(date)
}
function DashboardClock({ now }: { now: Date }) {
  return (
    <div className="dashboard-clock-card cc-clock" aria-label={`当前时间 ${fmtDate(now)} ${fmtClock(now)}`}>
      <strong>{fmtClock(now)}</strong>
      <small>{fmtDate(now)}</small>
      <em>{Intl.DateTimeFormat().resolvedOptions().timeZone || 'Local'}</em>
    </div>
  )
}
function fmtShortTime(value?: string) {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value.slice(11, 19) || value
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
}
function fmtRelative(value?: string) {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const diff = (Date.now() - d.getTime()) / 1000
  if (diff < 60) return `${Math.max(1, Math.floor(diff))}s 前`
  if (diff < 3600) return `${Math.floor(diff / 60)}m 前`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h 前`
  return `${Math.floor(diff / 86400)}d 前`
}

function scoreTone(score: number, backendOnline: boolean): HealthTone {
  if (!backendOnline) return 'danger'
  if (score >= 90) return 'ok'
  if (score >= 70) return 'warn'
  return 'danger'
}

function metricTone(value: number, threshold = 1): HealthTone {
  return value >= threshold ? 'warn' : 'neutral'
}

function useCurrentTime() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  return now
}

type WidgetProps = {
  widget: Widget
  onDragStart: (id: WidgetId) => void
  onDragOver: (id: WidgetId) => void
  onDrop: (id: WidgetId) => void
  onToggle: (id: WidgetId) => void
  draggingId: WidgetId | null
  dropTargetId: WidgetId | null
}

function WidgetShell({ widget, onDragStart, onDragOver, onDrop, onToggle, draggingId, dropTargetId, children, trailing }: WidgetProps & { children: React.ReactNode; trailing?: React.ReactNode }) {
  const isDragging = draggingId === widget.id
  const isDropTarget = dropTargetId === widget.id && draggingId !== null && draggingId !== widget.id
  return (
    <section
      className={`cc-widget cc-widget--${widget.size} glass-panel${isDragging ? ' is-dragging' : ''}${isDropTarget ? ' is-drop-target' : ''}`}
      data-widget-id={widget.id}
      onDragOver={(e) => { e.preventDefault(); onDragOver(widget.id) }}
      onDrop={(e) => { e.preventDefault(); onDrop(widget.id) }}
    >
      <header className="cc-widget-head">
        <span
          className="cc-widget-handle"
          draggable
          onDragStart={(e) => {
            e.dataTransfer.effectAllowed = 'move'
            onDragStart(widget.id)
          }}
          onDragEnd={() => onDragStart(null as any)}
          title="拖拽以调整顺序"
          aria-label="拖拽调整位置"
        >
          ⠿
        </span>
        <h3 className="cc-widget-title">
          {widget.title}
          <small>{widget.subtitle}</small>
        </h3>
        <div className="cc-widget-actions">
          {trailing}
          <button
            className="cc-icon-btn"
            onClick={() => onToggle(widget.id)}
            title="隐藏此模块"
            aria-label="隐藏此模块"
            style={{ width: 28, height: 28, padding: 0, fontSize: 14 }}
          >
            ×
          </button>
        </div>
      </header>
      <div className="cc-widget-body">{children}</div>
    </section>
  )
}

function HealthWidget({ data, loading, error, backendOnline }: { data: DashboardData | null; loading: boolean; error: string; backendOnline: boolean }) {
  const backup = data?.health?.backups || {}
  const disk = data?.health?.disk || {}
  const worker = data?.health?.deploy_worker || {}
  const sshPool = data?.runtime?.ssh_pool || {}
  const items = [
    { label: '最新备份', value: backup.latest_at ? fmtShortTime(backup.latest_at) : (backup.message || '—'), status: backup.status || 'warn' },
    { label: '磁盘', value: disk.percent != null ? `${disk.percent}% / ${disk.free_gb ?? '-'}GB` : '—', status: disk.status || 'neutral' },
    { label: '发布 Worker', value: worker.running ? '运行中' : (worker.message || '未运行'), status: worker.running ? 'ok' : 'warning' },
    { label: 'SSH 池', value: `${sshPool.active_connections ?? 0} 连接`, status: sshPool.error ? 'warning' : 'ok' },
  ]
  if (loading) return <Skeleton type="block" count={3} />
  if (error) return <EmptyState title="数据加载失败" description={error} />
  if (!backendOnline) return <EmptyState title="后端离线" description="健康数据不可用，请检查后端服务。" />
  return (
    <div className="cc-mini-list">
      {items.map((item) => (
        <Link key={item.label} to={ROUTES.maintenance} className="cc-mini-list-item" style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', borderRadius: 8, textDecoration: 'none', color: 'var(--text-secondary)' }}>
          <strong style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-display)', fontSize: 12, fontWeight: 500 }}>{item.label}</strong>
          <span style={{ marginLeft: 'auto', color: 'var(--text-faint)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>{item.value}</span>
          <StatusBadge value={item.status} />
        </Link>
      ))}
    </div>
  )
}

function ActivityWidget({ data, loading }: { data: DashboardData | null; loading: boolean }) {
  const events = useMemo(() => {
    const list: Array<{ id: string; level: HealthTone; title: string; subtitle: string; time?: string; to: string }> = []
    const recent = data?.deployments?.recent || []
    recent.slice(0, 6).forEach((d: any) => {
      const level: HealthTone = d.status === 'success' ? 'ok' : d.status === 'failed' ? 'danger' : d.status === 'running' ? 'warn' : 'neutral'
      list.push({
        id: `dep-${d.id}`,
        level,
        title: `${d.system || '—'} / ${d.service || '—'}`,
        subtitle: `${d.environment || '—'} · ${(d.servers || []).length || 0} 台`,
        time: d.started_at,
        to: ROUTES.deploy,
      })
    })
    const tasks = data?.deployments?.running_tasks || []
    tasks.slice(0, 4).forEach((t: any) => {
      list.push({
        id: `task-${t.id}`,
        level: 'warn',
        title: t.label || t.id || '运行中任务',
        subtitle: t.environment || t.system || '—',
        time: t.started_at,
        to: ROUTES.tasks,
      })
    })
    return list
  }, [data])
  if (loading) return <Skeleton type="block" count={3} />
  if (events.length === 0) return <EmptyState title="暂无活动" description="完成一次发布或任务后会在这里显示。" />
  return (
    <div className="cc-activity">
      {events.map((e) => (
        <Link key={e.id} to={e.to} className="cc-activity-row">
          <span className={`cc-activity-dot cc-activity-dot--${e.level === 'ok' ? 'success' : e.level === 'warn' ? 'warning' : e.level === 'danger' ? 'danger' : 'muted'}`} />
          <span />
          <span className="cc-activity-body">
            <strong>{e.title}</strong>
            <span>{e.subtitle}</span>
          </span>
          <span className="cc-activity-time">{fmtRelative(e.time)}</span>
        </Link>
      ))}
    </div>
  )
}

function RisksWidget({ risks }: { risks: DashboardData['risks'] }) {
  if (!risks || risks.length === 0) return <EmptyState title="全部正常" description="系统运行良好，无待处理事项。" />
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {risks.slice(0, 5).map((r, i) => {
        const tone = r.level === 'high' || r.level === 'critical' ? 'high' : r.level === 'medium' || r.level === 'warn' ? 'medium' : 'low'
        const icon = tone === 'high' ? '!' : tone === 'medium' ? '⚠' : '✓'
        return (
          <Link key={`${r.title}-${i}`} to={r.to || ROUTES.system} className={`cc-risk-card cc-risk-card--${tone}`}>
            <span className="cc-risk-card__icon">{icon}</span>
            <span className="cc-risk-card__body">
              <strong>{r.title}</strong>
              <span>{r.message}</span>
            </span>
            <span className="cc-risk-card__arrow">→</span>
          </Link>
        )
      })}
    </div>
  )
}

function TasksWidget({ data }: { data: DashboardData | null }) {
  const tasks = data?.deployments?.running_tasks || []
  if (tasks.length === 0) return <EmptyState title="无运行中任务" description="发起新部署或巡检后会出现在这里。" />
  return (
    <table className="cc-queue-table">
      <thead>
        <tr><th>任务</th><th>环境</th><th>状态</th><th className="num">启动</th></tr>
      </thead>
      <tbody>
        {tasks.slice(0, 6).map((t: any) => (
          <tr key={t.id}>
            <td><strong>{t.label || t.id || '—'}</strong></td>
            <td>{t.environment || '—'}</td>
            <td><StatusBadge value={t.status || 'running'} /></td>
            <td className="num">{fmtRelative(t.started_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function DeployWidget({ data }: { data: DashboardData | null }) {
  const byStatus = data?.deployments?.by_status || {}
  const total = Object.values(byStatus).reduce<number>((sum, n: any) => sum + Number(n || 0), 0)
  if (total === 0) return <EmptyState title="无发布数据" description="完成一次发布会显示状态分布。" />
  const entries = Object.entries(byStatus)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <strong style={{ color: 'var(--text-strong)', fontFamily: 'var(--font-display)', fontSize: 22, fontWeight: 600 }}>{total}</strong>
        <span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.08em' }}>最近发布总数</span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {entries.map(([status, count]: [string, any]) => (
          <span key={status} className="cc-chip" style={{ gap: 6 }}>
            <StatusBadge value={status} />
            <strong>{count}</strong>
          </span>
        ))}
      </div>
    </div>
  )
}

function SiteStatusPanel({
  score,
  metrics,
  dashboard,
  backendOnline,
  loading,
  recentDeployments,
}: {
  score: number
  metrics: Record<string, number>
  dashboard: DashboardData | null
  backendOnline: boolean
  loading: boolean
  recentDeployments: any[]
}) {
  const failed = dashboard?.deployments?.tasks_by_status?.failed ?? 0
  const running = metrics.running_work ?? 0
  const blocked = dashboard?.deployments?.tasks_by_status?.blocked ?? 0
  const riskCount = dashboard?.risks?.length ?? 0
  const highRiskCount = (dashboard?.risks || []).filter((r) => r.level === 'high' || r.level === 'critical').length
  const tone = scoreTone(score, backendOnline)

  if (loading) {
    return (
      <div className="site-status-panel">
        <div className="site-status-main"><Skeleton type="block" count={1} /></div>
        <div className="site-status-metrics"><Skeleton type="block" count={4} /></div>
      </div>
    )
  }

  return (
    <div className="site-status-panel">
      <div className="site-status-main">
        <div className="site-status-orb">
          <div className={`site-status-orb-ring site-status-orb-ring--${tone}`} />
          <div className="site-status-orb-inner">
            <strong>{score}<small>%</small></strong>
            <span>{backendOnline ? 'ONLINE' : 'OFFLINE'}</span>
          </div>
        </div>
        <div className="site-status-summary">
          <div className="site-status-title">站点态势</div>
          <div className="site-status-desc">
            {dashboard?.generated_at
              ? `更新于 ${new Date(dashboard.generated_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`
              : '暂无数据'}
          </div>
          <div className="site-status-tags">
            {highRiskCount > 0 && <span className="site-status-tag site-status-tag--danger">{highRiskCount} 个高风险</span>}
            {riskCount > 0 && <span className="site-status-tag site-status-tag--warn">{riskCount} 个待处理</span>}
            {!backendOnline && <span className="site-status-tag site-status-tag--danger">后端离线</span>}
            {backendOnline && riskCount === 0 && <span className="site-status-tag site-status-tag--ok">一切正常</span>}
          </div>
        </div>
      </div>
      <div className="site-status-metrics">
        <Link to={ROUTES.tasks} className="site-status-metric">
          <span className="site-status-metric-icon site-status-metric-icon--run">▶</span>
          <div>
            <strong>{running}</strong>
            <span>运行中</span>
          </div>
        </Link>
        <Link to={ROUTES.tasks} className="site-status-metric">
          <span className="site-status-metric-icon site-status-metric-icon--fail">✕</span>
          <div>
            <strong>{failed}</strong>
            <span>失败</span>
          </div>
        </Link>
        <Link to={ROUTES.tasks} className="site-status-metric">
          <span className="site-status-metric-icon site-status-metric-icon--block">⊘</span>
          <div>
            <strong>{blocked}</strong>
            <span>阻塞</span>
          </div>
        </Link>
        <Link to={ROUTES.tools} className="site-status-metric">
          <span className="site-status-metric-icon site-status-metric-icon--risk">⚠</span>
          <div>
            <strong>{highRiskCount}</strong>
            <span>高风险</span>
          </div>
        </Link>
      </div>
      {recentDeployments.length > 0 && (
        <div className="site-status-events">
          {recentDeployments.slice(0, 4).map((d: any) => {
            const evtTone = d.status === 'success' ? 'ok' : d.status === 'failed' ? 'danger' : 'warn'
            return (
              <Link key={d.id} to={ROUTES.deploy} className="site-status-event">
                <span className={`site-status-event-dot site-status-event-dot--${evtTone}`} />
                <span className="site-status-event-label">{d.system || '—'} / {d.service || '—'}</span>
                <span className="site-status-event-meta">{d.environment || '—'}</span>
                <span className="site-status-event-time">{d.started_at ? fmtShortTime(d.started_at) : '—'}</span>
              </Link>
            )
          })}
        </div>
      )}
    </div>
  )
}

function StorageWidget({ data }: { data: DashboardData | null }) {
  const storage = data?.storage || {}
  const local = storage.local || storage.local_summary || {}
  const remote = storage.remote || storage.remote_summary || {}
  const localKb = local.used_kb || local.used || 0
  const remoteKb = remote.used_kb || remote.used || 0
  if (!localKb && !remoteKb) return <EmptyState title="无存储数据" description="运行一次诊断后会显示本地和远程存储占用。" />
  const fmt = (kb: number) => {
    if (kb > 1024 * 1024) return `${(kb / 1024 / 1024).toFixed(1)} GB`
    if (kb > 1024) return `${(kb / 1024).toFixed(1)} MB`
    return `${kb} KB`
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
          <span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: 10.5, letterSpacing: '0.08em' }}>LOCAL</span>
          <strong style={{ color: 'var(--text-strong)', fontFamily: 'var(--font-display)', fontSize: 13 }}>{fmt(localKb)}</strong>
        </div>
        <div style={{ height: 6, borderRadius: 999, background: 'var(--bg-surface-2)', overflow: 'hidden' }}>
          <div style={{ width: '60%', height: '100%', background: 'var(--gradient-cyber)' }} />
        </div>
      </div>
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
          <span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: 10.5, letterSpacing: '0.08em' }}>REMOTE</span>
          <strong style={{ color: 'var(--text-strong)', fontFamily: 'var(--font-display)', fontSize: 13 }}>{fmt(remoteKb)}</strong>
        </div>
        <div style={{ height: 6, borderRadius: 999, background: 'var(--bg-surface-2)', overflow: 'hidden' }}>
          <div style={{ width: '40%', height: '100%', background: 'var(--gradient-cyber)' }} />
        </div>
      </div>
    </div>
  )
}

function SnapshotWidget({ data, loading }: { data: DashboardData | null; loading: boolean }) {
  const backup = data?.health?.backups || {}
  const disk = data?.health?.disk || {}
  const worker = data?.health?.deploy_worker || {}
  const sshPool = data?.runtime?.ssh_pool || {}
  const latestPackage = data?.packages?.latest
  const items = [
    { label: '备份',     value: backup.latest_at || backup.message || '暂无备份', status: backup.status || 'warn' },
    { label: '磁盘',     value: disk.percent != null ? `${disk.percent}% 已用 · ${disk.free_gb ?? '-'}GB 可用` : '—', status: disk.status || 'neutral' },
    { label: 'Worker',   value: worker.running ? '运行中' : (worker.message || '未运行'), status: worker.running ? 'ok' : 'warning' },
    { label: 'SSH 池',   value: `${sshPool.active_connections ?? 0} 个连接`, status: sshPool.error ? 'warning' : 'ok' },
    { label: '发布包',   value: latestPackage?.package_name || '暂无', status: latestPackage ? 'ok' : 'neutral' },
  ]
  if (loading) return <Skeleton type="block" count={3} />
  return (
    <table className="cc-queue-table">
      <thead>
        <tr><th>指标</th><th>状态</th><th>当前</th></tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.label}>
            <td><strong>{item.label}</strong></td>
            <td><StatusBadge value={item.status} /></td>
            <td>{item.value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function FavoritesWidget() {
  const favorites = useFavoriteStore((s) => s.favorites)
  const pinned = favorites.filter((f) => f.pinned)
  if (pinned.length === 0) return <EmptyState title="无收藏" description="在右上角点击 ⭐ 收藏当前页面或操作，会显示在这里。" />
  return (
    <div className="cc-mini-list">
      {pinned.slice(0, 6).map((f) => (
        <Link key={f.id} to={f.url}>
          <strong>{f.label}</strong>
          <span>{f.category?.toUpperCase() || 'PAGE'}</span>
        </Link>
      ))}
    </div>
  )
}

function SearchHistoryWidget() {
  const recent = usePreferenceStore((s) => s.recentSearches)
  if (!recent || recent.length === 0) return <EmptyState title="无搜索历史" description="使用顶部搜索栏后会形成搜索历史。" />
  return (
    <div className="cc-search-pills">
      {recent.slice(0, 8).map((q, i) => (
        <span key={`${q}-${i}`} className="cc-search-pill">{q}</span>
      ))}
    </div>
  )
}

export default function DashboardPage() {
  const { user } = useAuthStore()
  const backendOnline = useBackendStore((s) => s.online)
  const [dashboard, setDashboard] = useState<DashboardData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [widgets, setWidgets] = useState<Widget[]>(loadWidgetOrder)
  const [draggingId, setDraggingId] = useState<WidgetId | null>(null)
  const [dropTargetId, setDropTargetId] = useState<WidgetId | null>(null)
  const [view, setView] = useState<'full' | 'compact'>('full')
  const gridRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => { saveWidgetOrder(widgets) }, [widgets])

  useEffect(() => {
    let mounted = true
    setLoading(true)
    systemHealth.dashboard()
      .then((res: any) => { if (!mounted) return; setDashboard(res.data || null); setError('') })
      .catch((err: any) => { if (!mounted) return; setError(String(err || '加载工作台失败')) })
      .finally(() => mounted && setLoading(false))
    return () => { mounted = false }
  }, [])

  const metrics = dashboard?.metrics || {}
  const recentDeployments = useMemo(() => dashboard?.deployments?.recent || [], [dashboard])
  const now = useCurrentTime()
  const score = backendOnline ? (dashboard?.score ?? 92) : 60
  const hour = now.getHours()
  const greeting = hour < 12 ? '上午好' : hour < 18 ? '下午好' : '晚上好'
  const statusText = dashboard?.status === 'critical' ? '需要处理' : dashboard?.status === 'attention' ? '需要关注' : backendOnline ? '运行正常' : '后端离线'
  const tone = scoreTone(score, backendOnline)

  // 拖拽
  const handleDragStart = (id: WidgetId) => setDraggingId(id)
  const handleDragOver = (id: WidgetId) => { if (draggingId && draggingId !== id) setDropTargetId(id) }
  const handleDrop = (targetId: WidgetId) => {
    if (!draggingId || draggingId === targetId) { setDraggingId(null); setDropTargetId(null); return }
    setWidgets((prev) => {
      const from = prev.findIndex((w) => w.id === draggingId)
      const to = prev.findIndex((w) => w.id === targetId)
      if (from < 0 || to < 0) return prev
      const next = [...prev]
      const [moved] = next.splice(from, 1)
      next.splice(to, 0, moved)
      return next
    })
    setDraggingId(null)
    setDropTargetId(null)
  }
  const handleToggle = (id: WidgetId) => setWidgets((prev) => prev.map((w) => (w.id === id ? { ...w, visible: false } : w)))
  const handleReset = () => setWidgets(DEFAULT_WIDGETS)

  const visibleWidgets = widgets.filter((w) => w.visible)
  const hiddenCount = widgets.length - visibleWidgets.length

  // 渲染 widget body（按 id 分发）
  const renderWidgetBody = (widget: Widget) => {
    switch (widget.id) {
      case 'snapshot':  return <SnapshotWidget data={dashboard} loading={loading} />
      case 'activity':  return <ActivityWidget data={dashboard} loading={loading} />
      case 'risks':     return <RisksWidget risks={dashboard?.risks} />
      case 'tasks':     return <TasksWidget data={dashboard} />
      case 'health':    return <HealthWidget data={dashboard} loading={loading} error={error} backendOnline={backendOnline} />
      case 'favorites': return <FavoritesWidget />
      case 'search':    return <SearchHistoryWidget />
      case 'deploy':    return <DeployWidget data={dashboard} />
      case 'storage':   return <StorageWidget data={dashboard} />
      default: return null
    }
  }

  return (
    <div className="cc-command">
      {/* 顶部 Hero + 侧边健康面板 */}
      <div className="cc-command-hero">
        <div>
          <div className="cc-command-hero-eyebrow">COMMAND · {backendOnline ? 'ONLINE' : 'OFFLINE'}</div>
          <h1 className="cc-command-hero-title">
            {greeting}, <em className="energy-gradient" style={{ fontStyle: 'normal' }}>{user?.username || 'admin'}</em>
          </h1>
          <p className="cc-command-hero-desc">
            统一查看系统健康、发布状态、服务器资产、备份、磁盘和待确认操作。小团队场景下，首页优先回答今天有没有必须处理的事。
          </p>
          <DashboardClock now={now} />
          <div className="cc-command-hero-stats">
            <Link to={ROUTES.systems} className="cc-hero-stat-mini">
              <strong>{metrics.systems ?? '—'}</strong><span>系统数</span>
            </Link>
            <Link to={ROUTES.servers} className="cc-hero-stat-mini">
              <strong>{metrics.servers ?? '—'}</strong><span>服务器</span>
            </Link>
            <Link to={ROUTES.tasks} className={`cc-hero-stat-mini cc-hero-stat-mini--${metricTone(metrics.running_work || 0) === 'warn' ? 'warn' : 'info'}`}>
              <strong>{metrics.running_work ?? 0}</strong><span>运行中</span>
            </Link>
            <Link to={ROUTES.tasks} className={`cc-hero-stat-mini cc-hero-stat-mini--${(dashboard?.deployments?.tasks_by_status?.failed || 0) > 0 ? 'danger' : 'ok'}`}>
              <strong>{dashboard?.deployments?.tasks_by_status?.failed ?? 0}</strong><span>失败任务</span>
            </Link>
            <Link to={ROUTES.tools} className={`cc-hero-stat-mini cc-hero-stat-mini--${metricTone(metrics.mcp_high_risk_calls || 0) === 'warn' ? 'warn' : 'info'}`}>
              <strong>{metrics.mcp_high_risk_calls ?? 0}</strong><span>MCP 高风险</span>
            </Link>
            <Link to={ROUTES.tasks} className={`cc-hero-stat-mini cc-hero-stat-mini--${metricTone(metrics.pending_approvals || 0) === 'warn' ? 'warn' : 'info'}`}>
              <strong>{metrics.pending_approvals ?? 0}</strong><span>待确认</span>
            </Link>
          </div>
          <div className="cc-command-hero-actions">
            <FavoriteButton url={ROUTES.dashboard} label="工作台" category="dashboard" />
            <Link className="btn btn-primary" to={ROUTES.deploy}>发起发布</Link>
            <Link className="btn btn-subtle" to={ROUTES.system}>系统状态</Link>
            <Link className="btn btn-subtle" to={ROUTES.inspection}>巡检中心</Link>
            <div className="cc-view-switcher" role="tablist">
              <button className={view === 'full' ? 'is-active' : ''} onClick={() => setView('full')}>完整 <small>{visibleWidgets.length}</small></button>
              <button className={view === 'compact' ? 'is-active' : ''} onClick={() => setView('compact')}>紧凑</button>
            </div>
            {hiddenCount > 0 && (
              <button className="btn btn-subtle" onClick={handleReset} title={`已隐藏 ${hiddenCount} 个模块，点击重置为默认布局`}>
                还原布局（{hiddenCount}）
              </button>
            )}
          </div>
        </div>
        <div className="cc-command-side">
          <div
            className={`cc-orb cc-orb--${tone}`}
            style={{ ['--orb-pct' as any]: score } as React.CSSProperties}
            aria-label={`系统健康评分 ${score}%`}
          >
            <div className="cc-orb-ring" />
            <div className="cc-orb-inner">
              <strong>{score}%</strong>
              <span>{statusText}</span>
              <small>更新于 {fmtShortTime(dashboard?.generated_at)}</small>
            </div>
          </div>
        </div>
      </div>

      {/* 站点态势面板 */}
      <SiteStatusPanel
        score={score}
        metrics={metrics}
        dashboard={dashboard}
        backendOnline={backendOnline}
        loading={loading}
        recentDeployments={recentDeployments}
      />

      {/* KPI 指标卡条 */}
      <div className="wx-dash-kpi wx-kpi-row" aria-label="运行快照">
        <div className="wx-kpi"><span className="label">健康评分</span><span className="value">{score}</span></div>
        <div className="wx-kpi"><span className="label">服务器</span><span className="value">{metrics.servers ?? 0}</span></div>
        <div className={`wx-kpi ${(metrics.running_work || 0) > 0 ? 'tone-run' : ''}`}><span className="label">运行中</span><span className="value">{metrics.running_work ?? 0}</span></div>
        <div className={`wx-kpi ${(dashboard?.deployments?.tasks_by_status?.failed || 0) > 0 ? 'tone-fail' : ''}`}><span className="label">失败任务</span><span className="value">{dashboard?.deployments?.tasks_by_status?.failed ?? 0}</span></div>
      </div>

      {/* 4 宫格快捷卡 */}
      <div className="cc-quickgrid">
        <Link to={ROUTES.inspection} className="cc-quickgrid-card cc-quickgrid-card--info">
          <span className="cc-quickgrid-icon">◉</span>
          <strong>巡检中心</strong>
          <span>9 类目只读巡检 · 报告 + 风险闭环</span>
          <em>↗</em>
        </Link>
        <Link to={ROUTES.deploy} className="cc-quickgrid-card">
          <span className="cc-quickgrid-icon">▲</span>
          <strong>发起发布</strong>
          <span>预检 · 确认 · 部署 · 回滚</span>
          <em>↗</em>
        </Link>
        <Link to={ROUTES.servers} className="cc-quickgrid-card">
          <span className="cc-quickgrid-icon">⬡</span>
          <strong>服务器</strong>
          <span>{metrics.servers ?? 0} 台 · {dashboard?.servers?.warnings ?? 0} 配置提醒</span>
          <em>↗</em>
        </Link>
        <Link to={ROUTES.tools} className="cc-quickgrid-card cc-quickgrid-card--info">
          <span className="cc-quickgrid-icon">⌘</span>
          <strong>AI 工具中心</strong>
          <span>{metrics.mcp_high_risk_calls ?? 0} 高风险 · 浏览与调试 MCP</span>
          <em>↗</em>
        </Link>
      </div>

      {/* 可拖拽 widget 网格 */}
      {view === 'full' && (
        <div
          ref={gridRef}
          className={`cc-widget-grid${draggingId ? ' is-dragging' : ''}`}
          aria-label="可拖拽模块区"
        >
          {visibleWidgets.map((widget) => (
            <WidgetShell
              key={widget.id}
              widget={widget}
              onDragStart={handleDragStart}
              onDragOver={handleDragOver}
              onDrop={handleDrop}
              onToggle={handleToggle}
              draggingId={draggingId}
              dropTargetId={dropTargetId}
              trailing={
                widget.id === 'activity' && recentDeployments.length > 0
                  ? <Link to={ROUTES.deploy} className="cc-icon-btn" style={{ width: 28, height: 28, padding: 0, fontSize: 12 }} title="查看全部部署">↗</Link>
                  : null
              }
            >
              {renderWidgetBody(widget)}
            </WidgetShell>
          ))}
        </div>
      )}

      {view === 'compact' && (
        <div className="cc-widget-grid" aria-label="紧凑视图">
          {visibleWidgets.slice(0, 6).map((widget) => (
            <section key={widget.id} className={`cc-widget cc-widget--${Math.max(1, widget.size) as 1}`}>
              <header className="cc-widget-head">
                <h3 className="cc-widget-title">{widget.title}<small>{widget.subtitle}</small></h3>
              </header>
              <div className="cc-widget-body">{renderWidgetBody(widget)}</div>
            </section>
          ))}
        </div>
      )}
    </div>
  )
}
