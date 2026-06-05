import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ROUTES } from '../routes'
import { systemHealth } from '../api'
import { useAuthStore, useBackendStore } from '../store'
import { EmptyState, Skeleton, StatusBadge, PageHeader, FavoriteButton } from '../components/ui'
import { useFavoriteStore } from '../stores/favoriteStore'
import { usePreferenceStore } from '../stores/preferenceStore'

type HealthTone = 'success' | 'warning' | 'danger' | 'neutral'

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

function metricTone(value: number, dangerAt = 1): HealthTone {
  return value >= dangerAt ? 'warning' : 'neutral'
}

function DashboardMetric({ label, value, hint, to, tone = 'neutral' }: { label: string; value: number | string; hint: string; to: string; tone?: HealthTone }) {
  return (
    <Link to={to} className={`dashboard-metric dashboard-metric--${tone}`}>
      <div className="dashboard-metric-glow" />
      <div className="dashboard-metric-topline">
        <span>{label}</span>
        <em>↗</em>
      </div>
      <strong>{value}</strong>
      <small>{hint}</small>
    </Link>
  )
}

function fmtTime(value?: string) {
  if (!value) return '-'
  return String(value).replace('T', ' ').slice(0, 19)
}

function useCurrentTime() {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 30000)
    return () => window.clearInterval(timer)
  }, [])

  return now
}

function DashboardClock({ now }: { now: Date }) {
  const timeText = new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(now)
  const dateText = new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    weekday: 'long',
  }).format(now)
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Local Time'

  return (
    <div className="dashboard-clock-card" aria-label={`当前时间 ${dateText} ${timeText}`}>
      <span className="dashboard-clock-label">当前时间</span>
      <strong>{timeText}</strong>
      <small>{dateText}</small>
      <em>{timeZone}</em>
    </div>
  )
}

function RecentDeployments({ loading, deployments }: { loading: boolean; deployments: any[] }) {
  return (
    <section className="glass-card dashboard-panel">
      <div className="section-title-row">
        <div>
          <span className="eyebrow">发布动态</span>
          <h2>最近部署</h2>
          <p>展示最近发布结果、环境、服务器数量和触发时间。</p>
        </div>
        <Link className="btn btn-subtle" to={ROUTES.deploy}>查看全部</Link>
      </div>
      {loading && <Skeleton type="table-row" count={3} />}
      {!loading && deployments.length === 0 && <EmptyState title="暂无部署记录" description="完成一次发布后会在这里显示。" />}
      <div className="activity-list">
        {deployments.slice(0, 7).map((d) => (
          <div key={d.id} className="activity-item">
            <div className="activity-dot" />
            <div>
              <strong>{d.system || '-'} / {d.service || '-'}</strong>
              <small>{d.environment || '-'} · {fmtTime(d.started_at)} · {(d.servers || []).length || 0} 台</small>
            </div>
            <StatusBadge value={d.status} />
          </div>
        ))}
      </div>
    </section>
  )
}

function RiskCenter({ risks }: { risks: DashboardData['risks'] }) {
  const visible = risks || []
  const hasRisk = visible.some((item) => item.level !== 'low')
  const byLevel = useMemo(() => {
    const high = visible.filter((r) => r.level === 'high' || r.level === 'critical')
    const medium = visible.filter((r) => r.level === 'medium' || r.level === 'warn')
    const low = visible.filter((r) => r.level === 'low' || r.level === 'info' || !high.includes(r) && !medium.includes(r))
    return { high, medium, low }
  }, [visible])
  return (
    <section className="glass-card dashboard-panel">
      <div className="section-title-row">
        <div>
          <span className="eyebrow">待处理</span>
          <h2>待处理事项</h2>
          <p>待审批发布、高风险 MCP 调用、失败发布、清理异常和备份问题会优先出现在这里。</p>
        </div>
        <span className={`status-badge ${hasRisk ? 'status-badge--warning' : 'status-badge--success'}`}>{hasRisk ? '需要关注' : '全部正常'}</span>
      </div>
      <div className="risk-list">
        {visible.length === 0 && <EmptyState title="暂无待处理事项" description="系统运行正常，无需处理。" />}
        {byLevel.high.length > 0 && (
          <>
            <div className="risk-section-header" style={{ color: 'var(--danger)', fontSize: 12, fontWeight: 600, marginBottom: 4 }}>⚠ 高风险</div>
            {byLevel.high.map((risk, index) => (
              <Link key={`high-${risk.title}-${index}`} to={risk.to || ROUTES.system} className={`risk-panel risk-panel--${risk.level || 'high'}`}>
                <strong>{risk.title}</strong>
                <span>{risk.message}</span>
              </Link>
            ))}
          </>
        )}
        {byLevel.medium.length > 0 && (
          <>
            <div className="risk-section-header" style={{ color: 'var(--warning)', fontSize: 12, fontWeight: 600, marginBottom: 4 }}>⚡ 中风险</div>
            {byLevel.medium.map((risk, index) => (
              <Link key={`med-${risk.title}-${index}`} to={risk.to || ROUTES.system} className={`risk-panel risk-panel--${risk.level || 'medium'}`}>
                <strong>{risk.title}</strong>
                <span>{risk.message}</span>
              </Link>
            ))}
          </>
        )}
        {byLevel.low.length > 0 && (
          <>
            <div className="risk-section-header" style={{ color: 'var(--text-secondary)', fontSize: 12, fontWeight: 600, marginBottom: 4 }}>✓ 低风险</div>
            {byLevel.low.map((risk, index) => (
              <Link key={`low-${risk.title}-${index}`} to={risk.to || ROUTES.system} className={`risk-panel risk-panel--${risk.level || 'low'}`}>
                <strong>{risk.title}</strong>
                <span>{risk.message}</span>
              </Link>
            ))}
          </>
        )}
      </div>
    </section>
  )
}

function OperationsSnapshot({ data }: { data: DashboardData | null }) {
  const backup = data?.health?.backups || {}
  const disk = data?.health?.disk || {}
  const worker = data?.health?.deploy_worker || {}
  const latestPackage = data?.packages?.latest
  const sshPool = data?.runtime?.ssh_pool || {}
  const items = [
    { label: '备份', value: backup.latest_at || backup.message || '暂无备份', status: backup.status || 'warn', to: ROUTES.maintenance },
    { label: '磁盘', value: disk.percent != null ? `${disk.percent}% 已用 · ${disk.free_gb ?? '-'}GB 可用` : disk.message || '-', status: disk.status || 'neutral', to: ROUTES.system },
    { label: '发布 Worker', value: worker.message || (worker.running ? '运行中' : '未运行'), status: worker.status || (worker.running ? 'ok' : 'warning'), to: ROUTES.deploy },
    { label: 'SSH 连接池', value: `${sshPool.active_connections ?? 0} 个连接`, status: sshPool.error ? 'warning' : 'ok', to: ROUTES.servers },
    { label: '最新发布包', value: latestPackage?.package_name || '暂无发布包', status: latestPackage ? 'ok' : 'neutral', to: ROUTES.files },
  ]
  return (
    <section className="glass-card quick-command-card dashboard-snapshot-card">
      <div className="section-title-row">
        <div>
          <span className="eyebrow">每日快照</span>
          <h2>今日运维快照</h2>
          <p>适合本地/小团队每天打开后快速判断是否需要处理。</p>
        </div>
        <span className="dashboard-refresh-time">更新于 {fmtTime(data?.generated_at)}</span>
      </div>
      <div className="dashboard-snapshot-grid">
        {items.map((item) => (
          <Link key={item.label} className="dashboard-snapshot-item" to={item.to}>
            <span>{item.label}</span>
            <strong>{item.value}</strong>
            <StatusBadge value={item.status} />
          </Link>
        ))}
      </div>
    </section>
  )
}

function QuickActions({ actions }: { actions?: DashboardData['quick_actions'] }) {
  const favorites = useFavoriteStore((s) => s.favorites)
  const pinned = favorites.filter((f) => f.pinned)
  const recentSearches = usePreferenceStore((s) => s.recentSearches)
  const fallback = [
    { title: '巡检中心', description: '服务器巡检、项目巡检和风险闭环', to: ROUTES.inspection },
    { title: '发起发布', description: '预检、确认、部署日志', to: ROUTES.deploy },
    { title: '连接服务器', description: '只读巡检、终端和文件', to: ROUTES.servers },
    { title: 'AI 工具中心', description: '浏览和调试 MCP 工具', to: ROUTES.tools },
    { title: '数据库工作台', description: '查询、导出和数据维护', to: ROUTES.database },
    { title: '管理文件', description: '发布包和远程上传', to: ROUTES.files },
    { title: '系统诊断', description: '健康检查和诊断包', to: ROUTES.diagnostics },
  ]
  const source = actions?.length ? actions : fallback
  return (
    <section className="glass-card quick-command-card">
      <div className="section-title-row">
        <div>
          <span className="eyebrow">快捷入口</span>
          <h2>高频操作</h2>
          <p>把日常动作做成低摩擦、可追踪、可撤销的入口。</p>
        </div>
      </div>
      <div className="quick-actions quick-actions--modern">
        {source.map((action) => (
          <Link key={action.title} className="quick-action" to={action.to}>
            <strong>{action.title}</strong><span>{action.description}</span><em>↗</em>
          </Link>
        ))}
      </div>
      {pinned.length > 0 && (
        <div style={{ marginTop: '20px' }}>
          <div className="section-title-row">
            <div>
              <span className="eyebrow">收藏夹</span>
              <h2>已置顶</h2>
              <p>你标记为重要的页面和操作。</p>
            </div>
          </div>
          <div className="quick-actions quick-actions--modern">
            {pinned.map((fav) => (
              <Link key={fav.id} className="quick-action" to={fav.url}>
                <strong>{fav.label}</strong><span>{fav.description || ''}</span><em>📌</em>
              </Link>
            ))}
          </div>
        </div>
      )}
      {recentSearches.length > 0 && (
        <div style={{ marginTop: '20px' }}>
          <div className="section-title-row">
            <div>
              <span className="eyebrow">最近搜索</span>
              <h2>搜索历史</h2>
              <p>最近在导航搜索栏搜索过的关键词。</p>
            </div>
          </div>
          <div className="recent-searches-list">
            {recentSearches.slice(0, 5).map((q, i) => (
              <span key={i} className="recent-search-tag">{q}</span>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

export default function DashboardPage() {
  const { user } = useAuthStore()
  const backendOnline = useBackendStore((s) => s.online)
  const [dashboard, setDashboard] = useState<DashboardData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let mounted = true
    setLoading(true)
    systemHealth.dashboard()
      .then((res: any) => {
        if (!mounted) return
        setDashboard(res.data || null)
        setError('')
      })
      .catch((err: any) => {
        if (!mounted) return
        setError(String(err || '加载工作台失败'))
      })
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

  return (
    <div className="dashboard-command-center page-enter">
      <PageHeader
        title={`${greeting}, ${user?.username || 'admin'}`}
        description="统一查看系统健康、发布状态、服务器资产、备份、磁盘和待确认操作。小团队场景下，首页优先回答今天有没有必须处理的事。"
        badge={<StatusBadge value={backendOnline ? 'online' : 'offline'} />}
        actions={
          <>
            <FavoriteButton url={ROUTES.dashboard} label="工作台" category="dashboard" />
            <Link className="btn btn-primary" to={ROUTES.deploy}>发起发布</Link>
            <Link className="btn btn-subtle" to={ROUTES.system}>查看系统状态</Link>
            {error && <span className="dashboard-inline-error">{error}</span>}
          </>
        }
      />
      <div className="dashboard-hero-side-fixed">
        <DashboardClock now={now} />
        <div className="health-orb" aria-label="系统健康评分">
          <div className="health-orb-ring" />
          <strong>{score}%</strong>
          <span>{statusText}</span>
          <small>{backendOnline ? '接口可用' : '后端离线'}</small>
        </div>
      </div>

      <section className="dashboard-metrics-grid">
        <DashboardMetric label="系统数" value={metrics.systems ?? '-'} hint="系统与服务配置" to={ROUTES.systems} />
        <DashboardMetric label="服务器数" value={metrics.servers ?? '-'} hint={`${dashboard?.servers?.warnings ?? 0} 个配置提醒`} to={ROUTES.servers} tone={(dashboard?.servers?.errors || 0) > 0 ? 'danger' : (dashboard?.servers?.warnings || 0) > 0 ? 'warning' : 'success'} />
        <DashboardMetric label="运行中事项" value={metrics.running_work ?? 0} hint="发布任务与队列" to={ROUTES.tasks} tone={metricTone(metrics.running_work || 0)} />
        <DashboardMetric label="失败任务" value={dashboard?.deployments?.tasks_by_status?.failed ?? 0} hint="需要关注的任务" to={ROUTES.tasks} tone={(dashboard?.deployments?.tasks_by_status?.failed || 0) > 0 ? 'danger' : 'success'} />
        <DashboardMetric label="MCP 高风险" value={metrics.mcp_high_risk_calls ?? 0} hint="AI 工具高风险调用" to={ROUTES.tools} tone={metricTone(metrics.mcp_high_risk_calls || 0)} />
        <DashboardMetric label="待确认" value={metrics.pending_approvals ?? 0} hint="维护任务 / 工具计划" to={ROUTES.tasks} tone={metricTone(metrics.pending_approvals || 0)} />
      </section>

      <OperationsSnapshot data={dashboard} />
      <QuickActions actions={dashboard?.quick_actions} />

      <div className="dashboard-two-column">
        <RecentDeployments loading={loading} deployments={recentDeployments} />
        <RiskCenter risks={dashboard?.risks} />
      </div>
    </div>
  )
}
