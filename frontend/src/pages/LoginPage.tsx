import { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { auth } from '../api'
import { useAuthStore } from '../store'
import { ROUTES } from '../routes'
import { FRONTEND_BUILD_INFO } from '../generated/buildInfo'

function BrandMark({ size = 22 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
      style={{ display: 'block' }}
    >
      <defs>
        <linearGradient id="login-energy-grad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#22E08A" />
          <stop offset="100%" stopColor="#16A96A" />
        </linearGradient>
      </defs>
      <path
        d="M16 4 L27 10.5 L27 21.5 L16 28 L5 21.5 L5 10.5 Z"
        stroke="url(#login-energy-grad)"
        strokeWidth="1.8"
        strokeLinejoin="round"
        opacity="0.95"
      />
      <path
        d="M16 4 L16 28 M5 10.5 L27 21.5 M27 10.5 L5 21.5"
        stroke="url(#login-energy-grad)"
        strokeWidth="1"
        opacity="0.45"
      />
      <circle cx="16" cy="16" r="2.6" fill="#22E08A" />
    </svg>
  )
}

const FEATURE_TERMS = [
  { k: 'WORKBENCH', v: '统一指挥台', desc: 'servers · deploy · audit' },
  { k: 'OBSERVABILITY', v: '可观测的发布与回滚', desc: 'live logs · reports' },
  { k: 'AUDIT CHAIN', v: '每次写操作都留痕', desc: 'immutable · traceable' },
  { k: 'AI / MCP', v: '把 Agent 装进安全笼子', desc: 'scoped · guarded' },
]

const STATUS_NODES = [
  { label: 'API', state: 'ok' },
  { label: 'WORKER', state: 'ok' },
  { label: 'DB', state: 'ok' },
  { label: 'AUDIT', state: 'ok' },
] as const

function useClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), 1000)
    return () => window.clearInterval(t)
  }, [])
  return now
}

function fmtClock(d: Date) {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
function fmtDateTag(d: Date) {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    weekday: 'short',
  }).format(d)
}

export default function LoginPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()
  const { user, setUser } = useAuthStore()
  const now = useClock()

  useEffect(() => {
    if (user) navigate(ROUTES.dashboard, { replace: true })
  }, [user, navigate])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const res: any = await auth.login(username, password)
      const userData = res.data || res
      setUser(userData)
      navigate(ROUTES.dashboard, { replace: true })
    } catch (err: any) {
      setError(typeof err === 'string' ? err : '登录失败')
    } finally {
      setLoading(false)
    }
  }

  const uptime = useMemo(() => {
    // 稳定的"运行时长"展示，基于构建时间起算
    const build = FRONTEND_BUILD_INFO.builtAt
      ? new Date(FRONTEND_BUILD_INFO.builtAt).getTime()
      : Date.now() - 1000 * 60 * 60 * 24 * 9
    const mins = Math.max(1, Math.floor((Date.now() - build) / 60000))
    if (mins < 60) return `${mins}m`
    if (mins < 1440) return `${Math.floor(mins / 60)}h ${mins % 60}m`
    return `${Math.floor(mins / 1440)}d ${Math.floor((mins % 1440) / 60)}h`
  }, [])

  return (
    <div className="login-page page-enter">
      <section className="login-visual" aria-label="OPS 产品介绍">
        <div className="login-visual-grid" aria-hidden="true" />
        <div className="login-visual-scan" aria-hidden="true" />
        <div className="login-visual-orb login-visual-orb--a" aria-hidden="true" />
        <div className="login-visual-orb login-visual-orb--b" aria-hidden="true" />
        <div className="login-visual-noise" aria-hidden="true" />

        <div className="login-visual-top">
          <div className="app-brand login-brand-inline">
            <span className="app-brand-mark" aria-hidden="true">
              <BrandMark size={22} />
            </span>
            <span className="brand-copy">
              <strong style={{ background: 'linear-gradient(135deg, #22E08A 0%, #16A96A 100%)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>AI</strong>
              <small>Command Center</small>
            </span>
          </div>
          <span className="login-build-chip">
            <span className="login-build-dot" aria-hidden="true" />
            v{FRONTEND_BUILD_INFO.version}
          </span>
        </div>

        <div className="login-visual-body">
          <span className="eyebrow">Engineering Operations · Mission Control</span>
          <h1>
            让每一次发布<br />
            都有<em>可观测的</em>轨迹
          </h1>
          <p>
            统一服务器资产、流水线发布、文件 / 终端、SQL 工作台与 AI 工具调用，
            把高风险操作压缩进同一条可回滚、可审计、可解释的现代化工作流。
          </p>

          <div className="login-status-strip" role="status" aria-live="polite">
            {STATUS_NODES.map((n) => (
              <span key={n.label} className="login-status-node">
                <span className={`login-status-led login-status-led--${n.state}`} aria-hidden="true" />
                <span className="login-status-label">{n.label}</span>
              </span>
            ))}
            <span className="login-status-sep" aria-hidden="true" />
            <span className="login-status-meta">
              UPTIME <strong>{uptime}</strong>
            </span>
          </div>
        </div>

        <div className="login-term-grid">
          {FEATURE_TERMS.map((t, i) => (
            <div key={t.k} className="login-term" style={{ animationDelay: `${120 + i * 80}ms` }}>
              <span className="login-term-idx">{String(i + 1).padStart(2, '0')}</span>
              <div className="login-term-body">
                <span>{t.k}</span>
                <strong>{t.v}</strong>
                <small>{t.desc}</small>
              </div>
            </div>
          ))}
        </div>

        <div className="login-visual-footer" aria-hidden="true">
          <span className="login-footer-clock">{fmtClock(now)}</span>
          <span className="login-footer-date">{fmtDateTag(now)}</span>
          <span className="login-footer-sep" />
          <span>SYSTEM_READY</span>
          <span>·</span>
          <span>SESSION_OK</span>
          <span>·</span>
          <span>AUDIT_CHAIN</span>
        </div>
      </section>

      <section className="login-card glass-panel">
        <div className="login-card-head">
          <span className="eyebrow">Sign In · 控制台准入</span>
          <h2>欢迎回来</h2>
          <p>使用内部账号登录 AI · Command Center</p>
        </div>

        {error && <div className="alert-card alert-card--danger" role="alert">{error}</div>}

        <form onSubmit={handleSubmit} className="login-form">
          <label className="field-label">
            <span>用户名</span>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="admin"
              autoComplete="username"
              autoFocus
            />
          </label>
          <label className="field-label">
            <span>密码</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="admin"
              autoComplete="current-password"
            />
          </label>
          <button type="submit" className="btn btn-primary login-submit" disabled={loading}>
            {loading ? (
              <>
                <span className="login-spinner" aria-hidden="true" />
                <span>登录中...</span>
              </>
            ) : (
              <>
                <span>登录控制台</span>
                <span className="login-submit-arrow" aria-hidden="true">→</span>
              </>
            )}
          </button>
        </form>

        <div className="login-card-foot">
          <span className="status-dot online" />
          <span>API &amp; session ready</span>
        </div>
      </section>
    </div>
  )
}
