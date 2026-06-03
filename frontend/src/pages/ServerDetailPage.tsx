import { Suspense, lazy, useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import OverviewTab from '../components/server-workbench/OverviewTab'
import FilesTab from '../components/server-workbench/FilesTab'
import { serverWorkbench } from '../api'

type Tab = 'overview' | 'terminal' | 'files' | 'history'

const TerminalTab = lazy(() => import('../components/server-workbench/TerminalTab'))

const tabs: { key: Tab; label: string }[] = [
  { key: 'overview', label: '概览' },
  { key: 'terminal', label: '终端' },
  { key: 'files', label: '文件' },
  { key: 'history', label: '历史' },
]

function ExecHistoryTab({ name }: { name: string }) {
  const [entries, setEntries] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [total, setTotal] = useState(0)
  const [riskFilter, setRiskFilter] = useState('')

  const load = async () => {
    setLoading(true)
    try {
      const res: any = await serverWorkbench.execHistory(name, 50, 0, riskFilter || undefined)
      setEntries(res.data?.entries || [])
      setTotal(res.data?.total || 0)
    } catch { }
    setLoading(false)
  }

  useEffect(() => { load() }, [name, riskFilter])

  const riskBadge = (level: string) => {
    if (level === 'blocked') return 'exec-risk-badge exec-risk-badge--blocked'
    if (level === 'dangerous') return 'exec-risk-badge exec-risk-badge--dangerous'
    return 'exec-risk-badge exec-risk-badge--safe'
  }

  return (
    <div style={{ display: 'grid', gap: '16px' }}>
      <div className="exec-history-toolbar">
        <span className="exec-history-count">命令执行历史 ({total})</span>
        <select value={riskFilter} onChange={(e) => setRiskFilter(e.target.value)} className="exec-history-select">
          <option value="">全部</option>
          <option value="safe">安全</option>
          <option value="dangerous">危险</option>
          <option value="blocked">已拦截</option>
        </select>
        <button className="btn btn-sm" onClick={load}>刷新</button>
      </div>
      <div className="card" style={{ padding: 0, overflow: 'auto' }}>
        {loading ? (
          <div className="exec-history-empty">加载中...</div>
        ) : (
          <table className="exec-history-table">
            <thead>
              <tr>
                <th>时间</th><th>用户</th><th>命令</th><th>退出码</th><th>耗时</th><th>风险</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e: any) => (
                <tr key={e.id} className="exec-history-row">
                  <td className="exec-history-time">
                    {e.created_at ? new Date(e.created_at).toLocaleString() : '-'}
                  </td>
                  <td className="exec-history-user">{e.username}</td>
                  <td className="exec-history-cmd" title={e.command}>
                    {e.command}
                  </td>
                  <td className={e.exit_code === 0 ? 'exec-history-exit exec-history-exit--ok' : 'exec-history-exit exec-history-exit--fail'}>
                    {e.exit_code}
                  </td>
                  <td className="exec-history-duration">
                    {e.duration_ms ? `${e.duration_ms}ms` : '-'}
                  </td>
                  <td>
                    <span className={riskBadge(e.risk_level)}>{e.risk_level}</span>
                  </td>
                </tr>
              ))}
              {entries.length === 0 && (
                <tr><td colSpan={6} className="exec-history-empty">暂无执行记录</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

export default function ServerDetailPage() {
  const { name } = useParams<{ name: string }>()
  const isStandalone = new URLSearchParams(window.location.search).has('standalone') || window.opener !== null
  const [activeTab, setActiveTab] = useState<Tab>(isStandalone ? 'terminal' : 'overview')
  const [terminalMounted, setTerminalMounted] = useState(isStandalone)

  useEffect(() => {
    if (isStandalone) {
      document.title = `${name} - 终端`
    }
  }, [isStandalone, name])

  const handleTabChange = (tab: Tab) => {
    setActiveTab(tab)
    if (tab === 'terminal') setTerminalMounted(true)
  }

  return (
    <div className={`server-workbench-shell ${isStandalone ? 'server-workbench-shell--standalone' : ''}`}>
      <header className="server-workbench-header glass-panel">
        <div className="server-title-block">
          <span className="eyebrow">Remote Session</span>
          <h1>{name}</h1>
          <p>{isStandalone ? '独立终端窗口' : '服务器概览、终端、文件与命令历史统一工作台'}</p>
        </div>
        <div className="server-workbench-actions">
          <span className="status-badge status-badge--neutral">SSH</span>
          <span className="status-badge status-badge--success">Audited</span>
          {isStandalone && <button className="btn btn-subtle" onClick={() => window.close()}>关闭</button>}
        </div>
      </header>

      <nav className="server-tabbar" aria-label="服务器工作台标签">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => handleTabChange(t.key)}
            className={`server-tab ${activeTab === t.key ? 'active' : ''}`}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <section className="server-workbench-body">
        {activeTab === 'overview' && (
          <div className="server-tab-scroll"><div className="server-tab-container"><OverviewTab name={name!} /></div></div>
        )}
        {activeTab === 'terminal' && terminalMounted && (
          <div className="server-tab-terminal"><Suspense fallback={<div className="card" style={{ margin: 16 }}>终端资源加载中...</div>}><TerminalTab name={name!} active={activeTab === 'terminal'} /></Suspense></div>
        )}
        {activeTab === 'files' && (
          <div className="server-tab-scroll"><div className="server-tab-container"><FilesTab name={name!} /></div></div>
        )}
        {activeTab === 'history' && (
          <div className="server-tab-scroll"><div className="server-tab-container"><ExecHistoryTab name={name!} /></div></div>
        )}
      </section>
    </div>
  )
}
