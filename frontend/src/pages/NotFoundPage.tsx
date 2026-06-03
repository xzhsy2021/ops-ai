import { Link, useLocation } from 'react-router-dom'
import { ROUTES } from '../routes'

export default function NotFoundPage() {
  const location = useLocation()
  return (
    <div className="not-found-page">
      <div className="not-found-card">
        <span className="status-badge status-badge--warning">404</span>
        <h1>页面不存在</h1>
        <p>当前路径没有匹配到前端页面。请返回工作台，或打开诊断页面检查前端路由与构建产物是否一致。</p>
        <pre>{location.pathname}{location.search}</pre>
        <div className="not-found-actions">
          <Link className="btn btn-primary" to={ROUTES.dashboard}>返回工作台</Link>
          <Link className="btn btn-subtle" to={ROUTES.system}>系统状态</Link>
          <Link className="btn btn-subtle" to={ROUTES.diagnostics}>打开诊断</Link>
        </div>
      </div>
    </div>
  )
}
