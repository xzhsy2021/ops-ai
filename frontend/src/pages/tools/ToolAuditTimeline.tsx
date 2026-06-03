import { useState } from 'react'

function riskLabel(risk?: string) {
  const map: Record<string, string> = {
    critical: '严重风险', high: '高风险', medium: '中风险',
    low: '低风险', read: '只读', safe: '安全',
  }
  return map[risk || ''] || risk || '未分级'
}

type AuditEntry = {
  id?: string
  tool?: string
  created_at?: string
  caller?: string
  source?: string
  risk?: string
  duration_ms?: number
  status?: string
  args_summary?: string
  result_summary?: string
}

export function ToolAuditTimeline({
  entries,
  loading,
}: {
  entries: AuditEntry[]
  loading?: boolean
}) {
  const [riskFilter, setRiskFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [toolFilter, setToolFilter] = useState('')

  const filtered = entries.filter((e) => {
    if (riskFilter && e.risk !== riskFilter) return false
    if (statusFilter && e.status !== statusFilter) return false
    if (toolFilter.trim()) {
      return (e.tool || '').toLowerCase().includes(toolFilter.trim().toLowerCase())
    }
    return true
  })

  return (
    <div className="card" style={{ display: 'grid', gap: 12 }}>
      <div className="card-header">
        <div>
          <h2>审计时间线</h2>
          <span>{entries.length} 条记录，当前筛选 {filtered.length} 条</span>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <input
          value={toolFilter}
          onChange={(e) => setToolFilter(e.target.value)}
          placeholder="按工具名筛选..."
          style={{ flex: 1, minWidth: 140 }}
        />
        <select value={riskFilter} onChange={(e) => setRiskFilter(e.target.value)}>
          <option value="">全部风险</option>
          <option value="critical">严重风险</option>
          <option value="high">高风险</option>
          <option value="medium">中风险</option>
          <option value="low">低风险</option>
        </select>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">全部状态</option>
          <option value="success">成功</option>
          <option value="error">失败</option>
          <option value="cancelled">已取消</option>
        </select>
      </div>
      <div className="timeline-list">
        {loading && entries.length === 0 && <div style={{ padding: 24, color: 'var(--text-muted)', textAlign: 'center' }}>加载中...</div>}
        {!loading && filtered.length === 0 && <div style={{ padding: 24, color: 'var(--text-muted)', textAlign: 'center' }}>暂无审计记录</div>}
        {filtered.map((entry, i) => (
          <div key={entry.id || i} className="timeline-item">
            <strong>{entry.tool || '-'}</strong>
            <small>
              <span>{entry.created_at ? new Date(entry.created_at).toLocaleString() : '-'}</span>
              <span>触发者：{entry.caller || '-'}</span>
              <span>来源：{entry.source || '-'}</span>
              <span className={`tag ${entry.risk === 'high' || entry.risk === 'critical' ? 'tag-danger' : entry.risk === 'medium' ? 'tag-warning' : 'tag-success'}`}>
                {riskLabel(entry.risk)}
              </span>
              <span>{entry.duration_ms != null ? `${entry.duration_ms}ms` : ''}</span>
              <span className={`tag ${entry.status === 'success' ? 'tag-success' : 'tag-danger'}`}>
                {entry.status || '-'}
              </span>
            </small>
            {entry.args_summary && <p>参数：{entry.args_summary}</p>}
            {entry.result_summary && <p>结果：{entry.result_summary}</p>}
          </div>
        ))}
      </div>
    </div>
  )
}