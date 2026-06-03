import { useState, type CSSProperties } from 'react'

import type { DeploymentHistoryFilters as HistoryFilters, DeploymentRecord } from '../../types/deploy'

interface DeploymentHistoryTableProps {
  deployments: DeploymentRecord[]
  pagination?: { limit?: number; offset?: number; total?: number; has_more?: boolean }
  onQuery?: (filters: HistoryFilters) => void
  onRollback: (deploymentId: string) => void
  onViewLogs: (deployment: DeploymentRecord) => void
  onViewReport?: (deployment: DeploymentRecord) => void
  onReuse?: (deployment: DeploymentRecord) => void
}

function statusStyle(status: string): CSSProperties {
  return {
    color: status === 'success' ? 'var(--success)' : status === 'failed' ? 'var(--danger)' : 'var(--warning)',
    background: status === 'success' ? 'var(--success-surface)' : status === 'failed' ? 'var(--danger-surface)' : 'var(--warning-surface)',
    padding: '2px 8px',
    borderRadius: '4px',
    whiteSpace: 'nowrap',
  }
}

export default function DeploymentHistoryTable({ deployments, pagination, onQuery, onRollback, onViewLogs, onViewReport, onReuse }: DeploymentHistoryTableProps) {
  const [filters, setFilters] = useState<HistoryFilters>({ limit: 50, offset: 0 })
  const updateFilter = (patch: Partial<HistoryFilters>) => setFilters((prev) => ({ ...prev, ...patch, offset: patch.offset ?? 0 }))
  const query = (patch?: Partial<HistoryFilters>) => {
    const next = { ...filters, ...(patch || {}) }
    setFilters(next)
    onQuery?.(next)
  }
  const offset = pagination?.offset ?? filters.offset ?? 0
  const limit = pagination?.limit ?? filters.limit ?? 50
  const total = pagination?.total ?? deployments.length
  const canPrev = offset > 0
  const canNext = Boolean(pagination?.has_more)

  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', alignItems: 'center', marginBottom: '12px', flexWrap: 'wrap' }}>
        <div>
          <h2 style={{ margin: 0 }}>部署历史</h2>
          <p style={{ margin: '6px 0 0', color: 'var(--text-muted)', fontSize: '13px' }}>支持按应用、环境、状态和操作者筛选；操作按钮只复用参数，不会直接重新执行。</p>
        </div>
        <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>共 {total} 条，当前 {offset + 1}-{Math.min(offset + deployments.length, total)}</div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '8px', marginBottom: '12px' }}>
        <input placeholder="搜索 ID/包/服务器" value={filters.q || ''} onChange={(e) => updateFilter({ q: e.target.value })} />
        <input placeholder="系统" value={filters.system || ''} onChange={(e) => updateFilter({ system: e.target.value })} />
        <input placeholder="服务" value={filters.service || ''} onChange={(e) => updateFilter({ service: e.target.value })} />
        <input placeholder="环境" value={filters.environment || ''} onChange={(e) => updateFilter({ environment: e.target.value })} />
        <select value={filters.status || ''} onChange={(e) => updateFilter({ status: e.target.value })}>
          <option value="">全部状态</option>
          <option value="success">成功</option>
          <option value="failed">失败</option>
          <option value="running">运行中</option>
          <option value="pending">排队中</option>
          <option value="cancelled">已取消</option>
          <option value="canceled">已取消</option>
        </select>
        <input placeholder="操作者" value={filters.created_by || ''} onChange={(e) => updateFilter({ created_by: e.target.value })} />
        <select value={filters.limit || 50} onChange={(e) => updateFilter({ limit: Number(e.target.value) })}>
          <option value={20}>20 条</option>
          <option value={50}>50 条</option>
          <option value={100}>100 条</option>
        </select>
        <button className="btn btn-primary" onClick={() => query({ offset: 0 })}>查询</button>
      </div>

      <div style={{ overflow: 'auto', maxHeight: '60vh' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border-strong)', textAlign: 'left' }}>
              <th style={{ padding: '8px', color: 'var(--text-secondary)' }}>ID</th>
              <th style={{ padding: '8px', color: 'var(--text-secondary)' }}>系统/服务</th>
              <th style={{ padding: '8px', color: 'var(--text-secondary)' }}>环境</th>
              <th style={{ padding: '8px', color: 'var(--text-secondary)' }}>状态</th>
              <th style={{ padding: '8px', color: 'var(--text-secondary)' }}>包/版本</th>
              <th style={{ padding: '8px', color: 'var(--text-secondary)' }}>服务器</th>
              <th style={{ padding: '8px', color: 'var(--text-secondary)' }}>操作人</th>
              <th style={{ padding: '8px', color: 'var(--text-secondary)' }}>时间</th>
              <th style={{ padding: '8px', color: 'var(--text-secondary)' }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {deployments.map((d) => (
              <tr key={d.id} style={{ borderBottom: '1px solid var(--bg-surface)' }}>
                <td style={{ padding: '8px', fontFamily: 'monospace', color: 'var(--text-muted)' }}>{d.id?.slice(0, 8)}</td>
                <td style={{ padding: '8px' }}><strong>{d.system || '-'}</strong><div style={{ color: 'var(--text-muted)' }}>{d.service || '-'}</div></td>
                <td style={{ padding: '8px' }}>
                  <span style={{ color: d.environment?.toLowerCase() === 'prod' || d.environment?.toLowerCase() === 'production' ? 'var(--danger-solid)' : 'var(--text-secondary)', fontWeight: (d.environment?.toLowerCase() === 'prod' || d.environment?.toLowerCase() === 'production') ? 'bold' : 'normal' }}>{d.environment || '-'}</span>
                </td>
                <td style={{ padding: '8px' }}><span style={statusStyle(d.status)}>{d.status}</span></td>
                <td style={{ padding: '8px', color: 'var(--text-secondary)', maxWidth: '180px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.version || '-'}</td>
                <td style={{ padding: '8px', color: 'var(--text-muted)', maxWidth: '220px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.servers || '-'}</td>
                <td style={{ padding: '8px', color: 'var(--text-secondary)' }}>{d.created_by || '-'}</td>
                <td style={{ padding: '8px', color: 'var(--text-muted)', fontSize: '12px' }}>{d.started_at?.slice(0, 19) || '-'}</td>
                <td style={{ padding: '8px' }}>
                  <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
                    {d.can_rollback && <button className="btn" onClick={() => onRollback(d.id)} style={{ background: 'var(--warning-surface)', color: 'var(--warning)', padding: '2px 8px', fontSize: '12px' }}>回滚</button>}
                    <button className="btn" onClick={() => onViewLogs(d)} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)', padding: '2px 8px', fontSize: '12px' }}>详情</button>
                    {onViewReport && <button className="btn" onClick={() => onViewReport(d)} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)', padding: '2px 8px', fontSize: '12px' }}>报告</button>}
                    {onReuse && <button className="btn" onClick={() => onReuse(d)} style={{ background: 'var(--action-soft)', color: 'var(--text-primary)', padding: '2px 8px', fontSize: '12px' }}>复用</button>}
                  </div>
                </td>
              </tr>
            ))}
            {deployments.length === 0 && (
              <tr><td colSpan={9} style={{ padding: '16px', textAlign: 'center', color: 'var(--text-muted)' }}>暂无部署记录</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '12px', gap: '8px', alignItems: 'center' }}>
        <button className="btn" disabled={!canPrev} onClick={() => query({ offset: Math.max(0, offset - limit) })}>上一页</button>
        <span style={{ color: 'var(--text-muted)', fontSize: '13px' }}>offset {offset} / limit {limit}</span>
        <button className="btn" disabled={!canNext} onClick={() => query({ offset: offset + limit })}>下一页</button>
      </div>
    </div>
  )
}
