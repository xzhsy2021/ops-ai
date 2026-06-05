import WorkbenchClock from "./components/WorkbenchClock"
import { useEffect, useState, lazy, Suspense, useCallback, useMemo } from 'react'
import type { ReactNode } from 'react'
import { Routes, Route, Link, useLocation, Navigate, useNavigate } from 'react-router-dom'
import { Orbit } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useAuthStore, useNotificationStore, useBackendStore } from './store'
import { auth, resetAuthBootstrap, serverManagement, capabilityTools, deployment } from './api'
import { DistStaleBanner } from './components/DistStaleBanner'
import { useRoutePrefetch } from './hooks/useRoutePrefetch'
import ErrorBoundary from './components/ErrorBoundary'
import { NAV_LINKS, ROUTES, type NavItem } from './routes'
import { FRONTEND_BUILD_INFO } from './generated/buildInfo'
import { createPortal } from 'react-dom'
import { ToastContainer } from './components/ToastContainer'
import { KeyboardShortcutsPanel } from './components/KeyboardShortcutsPanel'
import { usePreferenceStore } from './stores/preferenceStore'

const LoginPage = lazy(() => import('./pages/LoginPage'))
const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const DeployPage = lazy(() => import('./pages/DeployPage'))
const ServerListPage = lazy(() => import('./pages/ServerListPage'))
const ServerDetailPage = lazy(() => import('./pages/ServerDetailPage'))
const FileCenterPage = lazy(() => import('./pages/FileCenterPage'))
const PipelinePage = lazy(() => import('./pages/PipelinePage'))
const AdminMaintenancePage = lazy(() => import('./pages/AdminMaintenancePage'))
const TaskCenterPage = lazy(() => import('./pages/TaskCenterPage'))
const AuditLogPage = lazy(() => import('./pages/AuditLogPage'))
const ReportCenterPage = lazy(() => import('./pages/ReportCenterPage'))
const InspectionCenterPage = lazy(() => import('./pages/InspectionCenterPage'))
const DatabaseToolsPage = lazy(() => import('./pages/DatabaseToolsPage'))
const ToolAccessPage = lazy(() => import('./pages/ToolAccessPage'))
const McpToolsPage = lazy(() => import('./pages/McpToolsPage'))
const McpAuditPage = lazy(() => import('./pages/McpAuditPage'))
const AiWorkflowsPage = lazy(() => import('./pages/AiWorkflowsPage'))
const AiAnalysisPage = lazy(() => import('./pages/AiAnalysisPage'))
const AiAnalysisDetailPage = lazy(() => import('./pages/AiAnalysisDetailPage'))
const SystemStatusPage = lazy(() => import('./pages/SystemStatusPage'))
const SystemDiagnosticsPage = lazy(() => import('./pages/SystemDiagnosticsPage'))
const NotFoundPage = lazy(() => import('./pages/NotFoundPage'))
const SystemListPage = lazy(() => import('./pages/SystemListPage'))
const SystemEditPage = lazy(() => import('./pages/SystemEditPage'))
const ServiceEditPage = lazy(() => import('./pages/ServiceEditPage'))

type ThemeMode = 'light' | 'dark'

const THEME_STORAGE_KEY = 'ops-theme'
const SHELL_STORAGE_KEY = 'ops-sidebar-collapsed'
const PERF_STORAGE_KEY = 'ops-performance-mode'



function getInitialTheme(): ThemeMode {
  try {
    const saved = localStorage.getItem(THEME_STORAGE_KEY)
    if (saved === 'light' || saved === 'dark') return saved
  } catch { }
  if (typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: light)').matches) return 'light'
  return 'dark'
}

function getInitialSidebarCollapsed() {
  try { return localStorage.getItem(SHELL_STORAGE_KEY) === '1' } catch { return false }
}

function getInitialPerformanceMode(): 'standard' | 'low-resource' {
  try {
    const v = localStorage.getItem(PERF_STORAGE_KEY)
    return v === 'low-resource' ? 'low-resource' : 'standard'
  } catch {
    return 'standard'
  }
}

const loadingFallback = (
  <div className="page-loading">
    <span className="loading-orb" />
    <div>加载中...</div>
  </div>
)

let _authBootstrapped = false

type SearchableItem = {
  label: string
  desc: string
  path: string
  icon?: LucideIcon
  section: 'navigation' | 'servers' | 'tools' | 'tasks'
}

function CommandPalette({
  open,
  onClose,
  navItems,
  onToggleTheme,
}: {
  open: boolean
  onClose: () => void
  navItems: NavItem[]
  onToggleTheme: () => void
}) {
  const [keyword, setKeyword] = useState('')
  const [servers, setServers] = useState<any[]>([])
  const [tools, setTools] = useState<any[]>([])
  const [tasks, setTasks] = useState<any[]>([])
  const navigate = useNavigate()
  const addRecentSearch = usePreferenceStore((s) => s.addRecentSearch)

  useEffect(() => {
    if (!open) return
    setKeyword('')
    Promise.allSettled([
      serverManagement.list(true).then((r: any) => (r?.data ?? r)?.servers || r?.data || r || []).catch(() => []),
      capabilityTools.list({ include_disabled: false, include_schema: false, limit: 30 }).then((r: any) => (r?.data ?? r)?.tools || r?.data || r || []).catch(() => []),
      deployment.list({ limit: 20 }).then((r: any) => (r?.data ?? r)?.deployments || r?.data || r || []).catch(() => []),
    ]).then(([s, t, d]) => {
      setServers(s.status === 'fulfilled' ? (Array.isArray(s.value) ? s.value.slice(0, 20) : []) : [])
      setTools(t.status === 'fulfilled' ? (Array.isArray(t.value) ? t.value.slice(0, 20) : []) : [])
      setTasks(d.status === 'fulfilled' ? (Array.isArray(d.value) ? d.value.slice(0, 20) : []) : [])
    })
  }, [open])

  const allItems = useMemo<SearchableItem[]>(() => {
    const results: SearchableItem[] = [
      ...navItems.map((item) => ({ label: item.label, desc: item.desc, path: item.path, icon: item.icon, section: 'navigation' as const })),
      ...servers.map((s) => ({ label: s.name || s.hostname || s.ip || '-', desc: `${s.ip || s.host || '-'} · ${s.status || s.health || '-'}`, path: `${ROUTES.servers}/${encodeURIComponent(s.name || s.hostname || '')}`, section: 'servers' as const })),
      ...tools.map((t) => ({ label: t.name || '-', desc: `${t.category || '-'} · risk:${t.risk || 'low'}`, path: ROUTES.tools, section: 'tools' as const })),
      ...tasks.map((d) => ({ label: `${d.system || '-'} / ${d.service || '-'}`, desc: `${d.status || '-'} · ${d.environment || '-'}`, path: ROUTES.tasks, section: 'tasks' as const })),
    ]
    return results
  }, [navItems, servers, tools, tasks])

  const filtered = useMemo(() => {
    const q = keyword.trim().toLowerCase()
    if (!q) return allItems
    return allItems.filter((item) => `${item.label} ${item.desc} ${item.path}`.toLowerCase().includes(q))
  }, [allItems, keyword])

  const grouped = useMemo(() => {
    const groups: Record<string, SearchableItem[]> = {}
    const order = ['navigation', 'servers', 'tools', 'tasks']
    const labels: Record<string, string> = { navigation: '导航', servers: '服务器', tools: '工具', tasks: '任务' }
    for (const item of filtered) {
      if (!groups[item.section]) groups[item.section] = []
      groups[item.section].push(item)
    }
    return order.filter((k) => groups[k]?.length).map((k) => ({ key: k, label: labels[k] || k, items: groups[k] }))
  }, [filtered])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key === 'Enter') {
        const first = filtered[0]
        if (first) {
          addRecentSearch(keyword)
          navigate(first.path)
          onClose()
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, filtered, navigate, onClose, keyword, addRecentSearch])

  if (!open) return null

  return (
    <div className="command-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="command-panel" role="dialog" aria-label="全局命令面板" aria-modal="true">
        <div className="command-input-row">
          <span>⌘</span>
          <input
            autoFocus
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="搜索页面、服务器、工具、任务..."
          />
          <kbd>Esc</kbd>
        </div>
        {grouped.map((group) => (
          <div key={group.key}>
            <div className="command-section-title">{group.label}</div>
            <div className="command-list">
              {group.items.slice(0, 8).map((item) => {
                const Icon = item.icon
                return (
                <button
                  key={`${group.key}-${item.label}-${item.path}`}
                  className="command-item"
                  onClick={() => { addRecentSearch(keyword); navigate(item.path); onClose() }}
                >
                  <span className="command-item-icon">{Icon ? <Icon size={18} strokeWidth={2.5} /> : <span style={{ width: 18, height: 18, display: 'inline-block', textAlign: 'center', fontSize: 12, fontWeight: 700, color: 'var(--text-muted)' }}>{group.key === 'servers' ? '🖥' : group.key === 'tools' ? '🔧' : group.key === 'tasks' ? '📋' : '→'}</span>}</span>
                  <span>
                    <strong>{item.label}</strong>
                    <small>{item.desc}</small>
                  </span>
                  <em>{item.path}</em>
                </button>
                )
              })}
            </div>
          </div>
        ))}
        {filtered.length === 0 && <div className="command-empty">没有匹配结果</div>}
        <div className="command-section-title">快速操作</div>
        <div className="command-actions">
          <button onClick={onToggleTheme}>切换浅色 / 深色主题</button>
          <button onClick={() => window.location.reload()}>刷新当前页面</button>
        </div>
      </div>
    </div>
  )
}

function App() {
  const { user, logout } = useAuthStore()
  const messages = useNotificationStore((s) => s.messages)
  const backendOnline = useBackendStore((s) => s.online)
  const setBackendOnline = useBackendStore((s) => s.setOnline)
  const location = useLocation()
  const [checking, setChecking] = useState(!_authBootstrapped)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [theme, setTheme] = useState<ThemeMode>(getInitialTheme)
  useRoutePrefetch()
  const [sidebarCollapsed, setSidebarCollapsed] = useState(getInitialSidebarCollapsed)
  const [performanceMode, setPerformanceMode] = useState<'standard' | 'low-resource'>(getInitialPerformanceMode)
  const [commandOpen, setCommandOpen] = useState(false)
  const [shortcutsOpen, setShortcutsOpen] = useState(false)
  const [zenMode, setZenMode] = useState(false)

  const currentRole = useMemo(() => {
    if (user?.is_admin) return 'admin'
    if (user?.role === 'admin') return 'admin'
    if (user?.role === 'operator' || user?.role === 'developer') return 'operator'
    return 'readonly'
  }, [user?.is_admin, user?.role])

  const hasMinRole = useCallback((minRole?: 'readonly' | 'operator' | 'admin') => {
    const order = { readonly: 0, operator: 1, admin: 2 }
    return order[currentRole] >= order[minRole || 'readonly']
  }, [currentRole])

  const availableNavItems = useMemo(
    () => NAV_LINKS.filter((link) => hasMinRole(link.minRole)),
    [hasMinRole]
  )

  const activeNavItem = useMemo(() => {
    return [...availableNavItems]
      .sort((a, b) => b.path.length - a.path.length)
      .find((link) => location.pathname === link.path || location.pathname.startsWith(link.path + '/')) || availableNavItems[0]
  }, [availableNavItems, location.pathname])

  const isServerWorkbench = location.pathname.startsWith(`${ROUTES.servers}/`)

  const toggleTheme = useCallback(() => {
    setTheme((prev) => prev === 'dark' ? 'light' : 'dark')
  }, [])

  const togglePerformance = useCallback(() => {
    setPerformanceMode((prev) => prev === 'standard' ? 'low-resource' : 'standard')
  }, [])

  const checkBackend = useCallback(async () => {
    try {
      await auth.me()
      setBackendOnline(true)
    } catch (e) {
      if (e === 'backend_offline') {
        setBackendOnline(false)
      } else if (e === 'not_authenticated') {
        setBackendOnline(true)
      }
    }
  }, [setBackendOnline])

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    document.documentElement.classList.toggle('dark', theme === 'dark')
    try { localStorage.setItem(THEME_STORAGE_KEY, theme) } catch { }
  }, [theme])

  useEffect(() => {
    document.documentElement.dataset.performance = performanceMode
    try { localStorage.setItem(PERF_STORAGE_KEY, performanceMode) } catch { }
  }, [performanceMode])

  useEffect(() => {
    document.documentElement.dataset.zen = zenMode ? 'true' : 'false'
  }, [zenMode])

  useEffect(() => {
    try { localStorage.setItem(SHELL_STORAGE_KEY, sidebarCollapsed ? '1' : '0') } catch { }
  }, [sidebarCollapsed])

  useEffect(() => {
    window.__OPS_FRONTEND_READY__ = true
    window.__OPS_FRONTEND_BUILD__ = FRONTEND_BUILD_INFO
  }, [])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault()
        setCommandOpen(true)
      }
      if (e.key === '?' && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault()
        setShortcutsOpen((prev) => !prev)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  useEffect(() => {
    if (location.pathname === ROUTES.login) {
      useAuthStore.getState().setUser(null)
      return
    }
    if (_authBootstrapped) return
    let cancelled = false
    resetAuthBootstrap()
    auth.me().then((res: any) => {
      const userData = res.data || res
      if (!cancelled) {
        _authBootstrapped = true
        if (userData?.username) {
          useAuthStore.getState().setUser(userData)
        } else {
          useAuthStore.getState().setUser(null)
        }
        setBackendOnline(true)
        setChecking(false)
      }
    }).catch((e) => {
      if (!cancelled) {
        _authBootstrapped = true
        useAuthStore.getState().setUser(null)
        if (e === 'backend_offline') {
          setBackendOnline(false)
        }
        setChecking(false)
      }
    })
    return () => { cancelled = true }
  }, [location.pathname, setBackendOnline])

  useEffect(() => {
    if (!backendOnline) {
      const timer = setInterval(checkBackend, 10000)
      return () => clearInterval(timer)
    }
  }, [backendOnline, checkBackend])

  useEffect(() => {
    setMobileMenuOpen(false)
  }, [location.pathname])

  const handleLogout = async () => {
    try { await auth.logout() } catch (_) { }
    logout()
    _authBootstrapped = false
    window.location.href = ROUTES.login
  }

  if (checking && location.pathname !== ROUTES.login) {
    return <div className="page-loading page-loading--full"><span className="loading-orb" /><div>验证中...</div></div>
  }

  const requireAuth = (element: ReactNode) => {
    if (!user) return <Navigate to={ROUTES.login} replace />
    return element
  }

  if (!user && location.pathname === ROUTES.login) {
    return (
      <div className="app-shell app-shell--login">
        <Suspense fallback={loadingFallback}><LoginPage /></Suspense>
      </div>
    )
  }

  return (
    <div className={`app-shell ${sidebarCollapsed ? 'app-shell--collapsed' : ''} ${isServerWorkbench ? 'app-shell--server' : ''}`}>
      <div className="shell-ambient shell-ambient--cyan" />
      <div className="shell-ambient shell-ambient--violet" />

      {!backendOnline && user && (
        <div className="backend-offline-bar" role="status">
          <strong>Backend offline</strong>
          <span>后端服务不可用，部分功能受限，正在尝试重连...</span>
          <button onClick={checkBackend}>立即重试</button>
        </div>
      )}

      {user && (
        <>
          <aside className={`app-sidebar ${mobileMenuOpen ? 'open' : ''}`}>
            <div className="sidebar-brand-row">
              <Link to={ROUTES.dashboard} className="app-brand" aria-label="返回工作台">
                <span className="app-brand-mark" aria-hidden="true">
                  <Orbit size={34} strokeWidth={2.8} />
                </span>
                <span className="brand-copy">
                  <strong>OPS</strong>
                  <small>Command Center</small>
                </span>
              </Link>
              <button className="sidebar-collapse-btn" onClick={() => setSidebarCollapsed(!sidebarCollapsed)} aria-label="折叠侧边栏">
                {sidebarCollapsed ? '›' : '‹'}
              </button>
            </div>

            <div className="sidebar-section-label">Workspace</div>
            <nav className="sidebar-nav" aria-label="主导航">
              {availableNavItems.map((link) => {
                const isActive = location.pathname === link.path || location.pathname.startsWith(link.path + '/')
                const Icon = link.icon
                return (
                  <Link
                    key={link.path}
                    to={link.path}
                    className={`sidebar-link ${isActive ? 'active' : ''}`}
                    title={sidebarCollapsed ? link.label : undefined}
                  >
                    <span className="sidebar-link-icon">
                      <Icon size={sidebarCollapsed ? 28 : 24} strokeWidth={sidebarCollapsed ? 2.9 : 2.65} absoluteStrokeWidth />
                    </span>
                    <span className="sidebar-link-copy">
                      <strong>{link.label}</strong>
                      <small>{link.desc}</small>
                    </span>
                  </Link>
                )
              })}
            </nav>

            <div className="sidebar-health-card">
              <span className={`status-dot ${backendOnline ? 'online' : 'offline'}`} />
              <div>
                <strong>{backendOnline ? 'System Online' : 'Backend Offline'}</strong>
                <small>{backendOnline ? 'API / Session Ready' : '等待后端恢复'}</small>
              </div>
            </div>
          </aside>

          <div className="mobile-scrim" onClick={() => setMobileMenuOpen(false)} />

          <section className="app-workspace">
            <header className="app-topbar">
              <div className="topbar-left">
                <button className="app-mobile-menu-btn" onClick={() => setMobileMenuOpen(!mobileMenuOpen)} aria-label="打开导航菜单">
                  {mobileMenuOpen ? '✕' : '☰'}
                </button>
                <div className="topbar-title">
                  <span className="breadcrumb">OPS / {activeNavItem?.label || '工作台'}</span>
                  <strong>{activeNavItem?.desc || 'Engineering Command Center'}</strong>
                </div>
              </div>

              <div className="topbar-actions">
                <WorkbenchClock />
                <span className="release-freeze-badge" title={`当前封板基线 ${FRONTEND_BUILD_INFO.version}`}>团队稳定版</span>
                <button className="command-trigger" onClick={() => setCommandOpen(true)} aria-label="打开命令面板">
                  <span>搜索或跳转</span>
                  <kbd>Ctrl K</kbd>
                </button>
                <button className="theme-toggle" onClick={toggleTheme} title={theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'} aria-label={theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'}>
                  <span>{theme === 'dark' ? '☀️' : '🌙'}</span>
                  <span className="mobile-hide">{theme === 'dark' ? '浅色' : '深色'}</span>
                </button>
                <button
                  className={`performance-toggle${performanceMode === 'low-resource' ? ' performance-toggle--low' : ''}`}
                  onClick={togglePerformance}
                  title={performanceMode === 'standard' ? '切换到低资源模式' : '切换到标准模式'}
                >
                  <span className="mobile-hide">{performanceMode === 'standard' ? '性能：标准' : '性能：低资源'}</span>
                  <span>{performanceMode === 'standard' ? '⚡' : '🔋'}</span>
                </button>
                <button
                  className={`zen-mode-trigger${zenMode ? ' performance-toggle--low' : ''}`}
                  onClick={() => setZenMode((prev) => !prev)}
                  title={zenMode ? '退出专注模式' : '进入专注模式'}
                >
                  <span className="mobile-hide">{zenMode ? '专注中' : '专注'}</span>
                  <span>{zenMode ? '🧘' : '👁'}</span>
                </button>
                <button
                  className="performance-toggle"
                  onClick={() => setShortcutsOpen(true)}
                  title="键盘快捷键"
                >
                  <span>⌨</span>
                  <span className="mobile-hide">快捷键</span>
                </button>
                <div className="user-chip">
                  <span className={`status-dot ${backendOnline ? 'online' : 'offline'}`} />
                  <span className="mobile-hide">{user.username} {currentRole === 'admin' ? '(admin)' : currentRole === 'operator' ? '(operator)' : '(readonly)'}</span>
                </div>
                <button className="btn btn-subtle" onClick={handleLogout}>退出</button>
              </div>
            </header>

            {messages.length > 0 && (
              <div className="notification-stack">
                {messages.map((m) => (
                  <div key={m.id} className={`notification-toast notification-toast--${m.type}`}>
                    {m.text}
                  </div>
                ))}
              </div>
            )}

            <main className={`app-main${isServerWorkbench ? ' app-main--full' : ''}`}>
              <DistStaleBanner />
              <ErrorBoundary key={location.pathname} title={`${activeNavItem?.label || '页面'}加载失败`}>
                <Suspense fallback={loadingFallback}>
                <Routes>
                  <Route path={ROUTES.login} element={<LoginPage />} />
                  <Route path={ROUTES.dashboard} element={requireAuth(<DashboardPage />)} />
                  <Route path={ROUTES.dashboardAlias} element={<Navigate to={ROUTES.dashboard} replace />} />
                  <Route path={ROUTES.systems} element={requireAuth(<SystemListPage />)} />
                  <Route path="/systems/create" element={requireAuth(<SystemEditPage />)} />
                  <Route path="/systems/:name/edit" element={requireAuth(<SystemEditPage />)} />
                  <Route path="/systems/:systemName/services/create" element={requireAuth(<ServiceEditPage />)} />
                  <Route path="/systems/:systemName/services/:serviceName/edit" element={requireAuth(<ServiceEditPage />)} />
                  <Route path={ROUTES.deploy} element={requireAuth(<DeployPage />)} />
                  <Route path={ROUTES.tasks} element={requireAuth(<TaskCenterPage />)} />
                  <Route path={ROUTES.taskCenterAlias} element={<Navigate to={ROUTES.tasks} replace />} />
                  <Route path={ROUTES.system} element={requireAuth(<SystemStatusPage />)} />
                  <Route path={ROUTES.systemStatus} element={requireAuth(<SystemStatusPage />)} />
                  <Route path={ROUTES.diagnostics} element={requireAuth(hasMinRole('readonly') ? <SystemDiagnosticsPage /> : <NotFoundPage />)} />
                  <Route path={ROUTES.servers} element={requireAuth(<ServerListPage />)} />
                  <Route path={`${ROUTES.servers}/:name`} element={requireAuth(<ServerDetailPage />)} />
                  <Route path={ROUTES.files} element={requireAuth(<FileCenterPage />)} />
                  <Route path={ROUTES.pipelines} element={requireAuth(<PipelinePage />)} />
                  <Route path={ROUTES.maintenance} element={requireAuth(hasMinRole('admin') ? <AdminMaintenancePage /> : <NotFoundPage />)} />
                  <Route path={ROUTES.sqlQuery} element={<Navigate to={`${ROUTES.database}?tab=query`} replace />} />
                  <Route path={ROUTES.audit} element={requireAuth(hasMinRole('admin') ? <AuditLogPage /> : <NotFoundPage />)} />
                  <Route path={ROUTES.reports} element={requireAuth(hasMinRole('readonly') ? <ReportCenterPage /> : <NotFoundPage />)} />
                  <Route path={ROUTES.inspection} element={requireAuth(hasMinRole('readonly') ? <InspectionCenterPage /> : <NotFoundPage />)} />
                  <Route path={ROUTES.database} element={requireAuth(hasMinRole('readonly') ? <DatabaseToolsPage /> : <NotFoundPage />)} />
                  <Route path={ROUTES.tools} element={requireAuth(hasMinRole('readonly') ? <ToolAccessPage /> : <NotFoundPage />)} />
                  <Route path={ROUTES.mcpTools} element={requireAuth(hasMinRole('readonly') ? <McpToolsPage /> : <NotFoundPage />)} />
                  <Route path={ROUTES.mcpAudit} element={requireAuth(hasMinRole('admin') ? <McpAuditPage /> : <NotFoundPage />)} />
                  <Route path={ROUTES.aiWorkflows} element={requireAuth(hasMinRole('readonly') ? <AiWorkflowsPage /> : <NotFoundPage />)} />
                  <Route path={ROUTES.aiAnalysis} element={requireAuth(hasMinRole('readonly') ? <AiAnalysisPage /> : <NotFoundPage />)} />
                  <Route path={`${ROUTES.aiAnalysis}/:id`} element={requireAuth(hasMinRole('readonly') ? <AiAnalysisDetailPage /> : <NotFoundPage />)} />
                  <Route path="*" element={requireAuth(<NotFoundPage />)} />
                </Routes>
                </Suspense>
              </ErrorBoundary>
            </main>
          </section>
        </>
      )}

      <CommandPalette open={commandOpen} onClose={() => setCommandOpen(false)} navItems={availableNavItems} onToggleTheme={toggleTheme} />
      {createPortal(<ToastContainer />, document.body)}
      <KeyboardShortcutsPanel open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />
    </div>
  )
}

export default App
