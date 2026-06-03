import React from 'react'
import { ROUTES } from '../routes'

type Props = {
  children: React.ReactNode
  title?: string
}

type State = {
  hasError: boolean
  message: string
  stack: string
}

export default class ErrorBoundary extends React.Component<Props, State> {
  state: State = { hasError: false, message: '', stack: '' }

  static getDerivedStateFromError(error: unknown): State {
    const err = error instanceof Error ? error : new Error(String(error))
    return { hasError: true, message: err.message || '未知错误', stack: err.stack || '' }
  }

  componentDidCatch(error: unknown, info: React.ErrorInfo) {
    // Keep the original console signal for local troubleshooting.
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  copyError = async () => {
    const text = `${this.state.message}\n\n${this.state.stack}`.trim()
    try {
      await navigator.clipboard?.writeText(text)
    } catch {
      // Clipboard may be unavailable in some local browsers. Keep the button harmless.
    }
  }

  render() {
    if (!this.state.hasError) return this.props.children
    return (
      <div className="error-boundary-card" role="alert">
        <div>
          <span className="status-badge status-badge--danger">页面异常</span>
          <h2>{this.props.title || '页面加载失败'}</h2>
          <p>当前页面组件渲染失败，系统其他页面不受影响。可以刷新页面、返回工作台，或打开诊断页面检查路由和构建产物。</p>
        </div>
        <pre>{this.state.message}</pre>
        <div className="error-boundary-actions">
          <button className="btn btn-primary" onClick={() => window.location.reload()}>刷新页面</button>
          <button className="btn btn-subtle" onClick={() => { window.location.href = ROUTES.dashboard }}>返回工作台</button>
          <button className="btn btn-subtle" onClick={() => { window.location.href = ROUTES.diagnostics }}>打开诊断</button>
          <button className="btn btn-subtle" onClick={this.copyError}>复制错误</button>
        </div>
      </div>
    )
  }
}
