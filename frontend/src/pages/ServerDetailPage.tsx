import { Suspense, lazy, useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import OverviewTab from '../components/server-workbench/OverviewTab'
import FilesTab from '../components/server-workbench/FilesTab'
import { serverWorkbench } from '../api'
import { ConfirmDialog } from '../components/ui'

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
  const [historyOffset, setHistoryOffset] = useState(0)
  const [historyPageSize, setHistoryPageSize] = useState(50)
  const [selectedLogIds, setSelectedLogIds] = useState<string[]>([])
  const [deleteTarget, setDeleteTarget] = useState<any | null>(null)
  const [batchDeleteOpen, setBatchDeleteOpen] = useState(false)

  const load = async (nextOffset = historyOffset, nextPageSize = historyPageSize) => {
    setLoading(true)
    try {
      const res: any = await serverWorkbench.execHistory(name, nextPageSize, nextOffset, riskFilter || undefined)
      const nextEntries = res.data?.entries || []
      setEntries(nextEntries)
      setTotal(res.data?.total || 0)
      const visibleIds = new Set(nextEntries.map((item: any) => String(item.id || '')).filter(Boolean))
      setSelectedLogIds((prev) => prev.filter((id) => visibleIds.has(id)))
    } catch { }
    setLoading(false)
  }

  useEffect(() => { load() }, [name, riskFilter, historyOffset, historyPageSize])

  const riskBadge = (level: string) => {
    if (level === 'blocked') return 'exec-risk-badge exec-risk-badge--blocked'
    if (level === 'dangerous') return 'exec-risk-badge exec-risk-badge--dangerous'
    return 'exec-risk-badge exec-risk-badge--safe'
  }

  const logPageIds = entries.map((item: any) => String(item.id || '')).filter(Boolean)
  const allLogsSelected = logPageIds.length > 0 && logPageIds.every((id) => selectedLogIds.includes(id))
  const historyPage = Math.floor(historyOffset / historyPageSize) + 1
  const historyPages = Math.max(1, Math.ceil(total / historyPageSize))

  const toggleLogSelection = (id: string, checked: boolean) => {
    setSelectedLogIds((prev) => checked ? Array.from(new Set([...prev, id])) : prev.filter((item) => item !== id))
  }

  const deleteSelectedLogs = async (ids: string[]) => {
    const uniqueIds = Array.from(new Set(ids.filter(Boolean)))
    if (uniqueIds.length === 0) return
    try {
      await serverWorkbench.deleteExecHistories({ log_ids: uniqueIds })
      setSelectedLogIds([])
      setDeleteTarget(null)
      setBatchDeleteOpen(false)
      const remaining = Math.max(0, total - uniqueIds.length)
      const maxOffset = Math.max(0, Math.floor(Math.max(remaining - 1, 0) / historyPageSize) * historyPageSize)
      const nextOffset = Math.min(historyOffset, maxOffset)
      if (nextOffset !== historyOffset) setHistoryOffset(nextOffset)
      else await load(nextOffset, historyPageSize)
    } catch { }
  }

  return (
    <div style={{ display: 'grid', gap: '16px' }}>
      <div className="exec-history-toolbar">
        <span className="exec-history-count">命令执行历史 ({total}) - 第 {historyPage}/{historyPages} 页</span>
        <label className="exec-history-count"><input type="checkbox" checked={allLogsSelected} disabled={logPageIds.length === 0} onChange={(e) => setSelectedLogIds(e.target.checked ? logPageIds : [])} /> 选择本页</label>
        <select value={riskFilter} onChange={(e) => { setRiskFilter(e.target.value); setHistoryOffset(0); setSelectedLogIds([]) }} className="exec-history-select">
          <option value="">全部</option>
          <option value="safe">安全</option>
          <option value="dangerous">危险</option>
          <option value="blocked">已拦截</option>
        </select>
        <select value={historyPageSize} onChange={(e) => { setHistoryPageSize(Number(e.target.value)); setHistoryOffset(0); setSelectedLogIds([]) }} className="exec-history-select">
          {[20, 50, 100].map((size) => <option key={size} value={size}>{size} 条/页</option>)}
        </select>
        <button className="btn btn-danger btn-sm" disabled={selectedLogIds.length === 0} onClick={() => setBatchDeleteOpen(true)}>批量删除 ({selectedLogIds.length})</button>
        <button className="btn btn-sm" onClick={() => load()}>刷新</button>
      </div>
      <div className="card" style={{ padding: 0, overflow: 'auto' }}>
        {loading ? (
          <div className="exec-history-empty">加载中...</div>
        ) : (
          <table className="exec-history-table">
            <thead>
              <tr>
                <th><input type="checkbox" checked={allLogsSelected} disabled={logPageIds.length === 0} onChange={(e) => setSelectedLogIds(e.target.checked ? logPageIds : [])} /></th>
                <th>时间</th><th>用户</th><th>命令</th><th>退出码</th><th>耗时</th><th>风险</th><th>操作</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e: any) => (
                <tr key={e.id} className="exec-history-row">
                  <td><input type="checkbox" checked={selectedLogIds.includes(String(e.id))} onChange={(event) => toggleLogSelection(String(e.id), event.target.checked)} /></td>
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
                  <td>
                    <button className="btn btn-danger btn-sm" onClick={() => setDeleteTarget(e)}>删除</button>
                  </td>
                </tr>
              ))}
              {entries.length === 0 && (
                <tr><td colSpan={8} className="exec-history-empty">暂无执行记录</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>
      <div className="pagination-footer">
        <span className="pagination-page">显示 {total === 0 ? 0 : historyOffset + 1}-{Math.min(historyOffset + historyPageSize, total)} / {total}</span>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="pagination-btn" disabled={historyOffset === 0} onClick={() => setHistoryOffset(Math.max(0, historyOffset - historyPageSize))}>上一页</button>
          <button className="pagination-btn" disabled={historyOffset + historyPageSize >= total} onClick={() => setHistoryOffset(historyOffset + historyPageSize)}>下一页</button>
        </div>
      </div>
      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="删除命令执行历史"
        description={`确认删除 ${deleteTarget?.server_name || name} 的这条命令历史？只删除审计展示记录，不影响服务器状态。`}
        confirmLabel="删除"
        danger
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => deleteSelectedLogs([String(deleteTarget?.id || '')])}
      />
      <ConfirmDialog
        open={batchDeleteOpen}
        title="批量删除命令执行历史"
        description={`确认删除选中的 ${selectedLogIds.length} 条命令执行历史？只删除审计展示记录，不影响服务器状态。`}
        confirmLabel="批量删除"
        danger
        onCancel={() => setBatchDeleteOpen(false)}
        onConfirm={() => deleteSelectedLogs(selectedLogIds)}
      />
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
