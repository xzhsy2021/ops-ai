import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { auth } from '../api'
import { useAuthStore } from '../store'
import { ROUTES } from '../routes'

export default function LoginPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()
  const { user, setUser } = useAuthStore()

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

  return (
    <div className="login-page page-enter">
      <section className="login-visual glass-panel" aria-label="OPS 产品介绍">
        <span className="eyebrow">OPS Cloud Console</span>
        <h1>下一代运维控制台</h1>
        <p>统一服务器、发布、文件、流程、审计与 SQL 查询，把高风险操作压缩进可观测、可回滚、可审计的现代化工作台。</p>
        <div className="login-feature-grid">
          <div><strong>Dark First</strong><span>深色优先，长时间使用更舒适</span></div>
          <div><strong>Glass UI</strong><span>玻璃态卡片、微光影、清晰层级</span></div>
          <div><strong>Audit Ready</strong><span>关键操作留痕，降低运维风险</span></div>
        </div>
      </section>

      <section className="login-card glass-card">
        <div className="login-brand">
          <span className="app-brand-mark">O</span>
          <div>
            <strong>欢迎回来</strong>
            <span>登录 OPS 运维平台</span>
          </div>
        </div>

        {error && <div className="alert-card alert-card--danger" role="alert">{error}</div>}

        <form onSubmit={handleSubmit} className="login-form">
          <label className="field-label">
            用户名
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
            密码
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="admin"
              autoComplete="current-password"
            />
          </label>
          <button type="submit" className="btn btn-primary login-submit" disabled={loading}>
            {loading ? '登录中...' : '登录控制台'}
          </button>
        </form>
      </section>
    </div>
  )
}
