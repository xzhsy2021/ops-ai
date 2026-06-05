import { useCallback, useEffect, useState } from 'react'
import { capabilityTools } from '../api'
import ToolRiskTag from '../components/ToolRiskTag'

function formatTime(value?: string) {
  if (!value) return '-'
  try { return new Date(value).toLocaleString() } catch { return value }
}

function truncate(str?: string, max = 120) {
  if (!str) return '-'
  return str.length > max ? str.slice(0, max) + '…' : str
}

const PAGE_SIZES = [20, 50, 100]

export default function McpAuditPage() {
  const [items, setItems] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [filterTool, setFilterTool] = useState('')
  const [filterStatus, setFilterStatus] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const res: any = await capabilityTools.calls({
        limit: pageSize,
        tool: filterTool || undefined,
        status: filterStatus || undefined,
      })
      const data = res?.data || res
      const result = data?.items || data?.result || data
      setItems(Array.isArray(result) ? result : [])
    } catch (e: any) {
      setError(e?.message || String(e))
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [pageSize, filterTool, filterStatus])

  useEffect(() => { load() }, [load])

  const totalPages = Math.max(1, Math.ceil(items.length / pageSize))
  const paged = items.slice((page - 1) * pageSize, page * pageSize)

  function toggleExpand(id: string) {
    setExpandedId((prev) => (prev === id ? null : id))
  }

  return (
    <div className="page-shell">
      <div className="page-header">
        <div>
          <h1>MCP 调用审计</h1>
          <p>查看 AI/MCP 工具调用、风险等级、状态和耗时。</p>
        </div>
        <button className="btn-primary" onClick={load} disabled={loading}>
          {loading ? '加载中...' : '刷新'}
        </button>
      </div>

      <div className="toolbar">
        <select value={filterStatus} onChange={(e) => { setFilterStatus(e.target.value); setPage(1) }}>
          <option value="">全部状态</option>
          <option value="success">success</option>
          <option value="blocked">blocked</option>
          <option value="failed">failed</option>
          <option value="pending">pending</option>
        </select>
        <input
          value={filterTool}
          onChange={(e) => setFilterTool(e.target.value)}
          placeholder="工具名称筛选"
          style={{ maxWidth: 220 }}
        />
        <button className="btn" onClick={() => { setPage(1); load() }}>筛选</button>
      </div>

      {error && <div className="alert alert-error" style={{ marginBottom: 12 }}>{error}</div>}

      <div className="card table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th style={{ width: 32 }}></th>
              <th>时间</th>
              <th>工具</th>
              <th>状态</th>
              <th>风险</th>
              <th>来源</th>
              <th>耗时</th>
            </tr>
          </thead>
          <tbody>
            {paged.map((x) => {
              const expanded = expandedId === x.id
              return (
                <>
                  <tr key={x.id} style={{ cursor: 'pointer' }} onClick={() => toggleExpand(x.id)}>
                    <td style={{ textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 }}>
                      {expanded ? '▼' : '▶'}
                    </td>
                    <td>{formatTime(x.created_at)}</td>
                    <td>{x.tool_name}</td>
                    <td>{x.status}</td>
                    <td><ToolRiskTag risk={x.risk_level} /></td>
                    <td>{x.client_name || x.username || x.token_owner || '-'}</td>
                    <td>{x.duration_ms != null ? `${x.duration_ms} ms` : '-'}</td>
                  </tr>
                  {expanded && (
                    <tr key={`${x.id}-detail`}>
                      <td colSpan={7} style={{ background: 'var(--bg-page)', padding: '12px 16px' }}>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 10 }}>
                          <div>
                            <div style={{ color: 'var(--text-muted)', fontSize: 12, marginBottom: 2 }}>输入参数</div>
                            <pre className="code-block" style={{ margin: 0, maxHeight: 120, overflow: 'auto', fontSize: 12 }}>{truncate(x.input_args, 500)}</pre>
                          </div>
                          <div>
                            <div style={{ color: 'var(--text-muted)', fontSize: 12, marginBottom: 2 }}>结果预览</div>
                            <pre className="code-block" style={{ margin: 0, maxHeight: 120, overflow: 'auto', fontSize: 12 }}>{truncate(x.result_preview, 500)}</pre>
                          </div>
                          <div>
                            <div style={{ color: 'var(--text-muted)', fontSize: 12, marginBottom: 2 }}>拦截原因</div>
                            <div>{x.blocked_reason || '-'}</div>
                          </div>
                          <div>
                            <div style={{ color: 'var(--text-muted)', fontSize: 12, marginBottom: 2 }}>策略结果</div>
                            <div>{x.policy_result || '-'}</div>
                          </div>
                          <div>
                            <div style={{ color: 'var(--text-muted)', fontSize: 12, marginBottom: 2 }}>关联计划 ID</div>
                            <div>{x.related_plan_id || '-'}</div>
                          </div>
                          <div>
                            <div style={{ color: 'var(--text-muted)', fontSize: 12, marginBottom: 2 }}>关联发布 ID</div>
                            <div>{x.related_deployment_id || '-'}</div>
                          </div>
                          <div>
                            <div style={{ color: 'var(--text-muted)', fontSize: 12, marginBottom: 2 }}>关联任务 ID</div>
                            <div>{x.related_job_id || '-'}</div>
                          </div>
                          <div>
                            <div style={{ color: 'var(--text-muted)', fontSize: 12, marginBottom: 2 }}>IP 地址</div>
                            <div>{x.ip_address || '-'}</div>
                          </div>
                          <div>
                            <div style={{ color: 'var(--text-muted)', fontSize: 12, marginBottom: 2 }}>User-Agent</div>
                            <div style={{ wordBreak: 'break-all' }}>{x.user_agent || '-'}</div>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              )
            })}
            {!paged.length && !loading && (
              <tr><td colSpan={7} style={{ color: 'var(--text-muted)', textAlign: 'center', padding: 24 }}>暂无调用记录</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {items.length > 0 && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 12, gap: 12, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>每页</span>
            <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1) }}>
              {PAGE_SIZES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>共 {items.length} 条</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button className="btn small" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>上一页</button>
            <span style={{ fontSize: 13 }}>{page} / {totalPages}</span>
            <button className="btn small" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>下一页</button>
          </div>
        </div>
      )}
    </div>
  )
}
