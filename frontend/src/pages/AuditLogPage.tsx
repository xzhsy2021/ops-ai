import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { auditLog, deployment, reports } from '../api'
import { ROUTES } from '../routes'
import { EmptyState, PageHeader, RiskConfirmDialog, FavoriteButton } from '../components/ui'
import { useUrlQueryState } from '../hooks/useUrlQueryState'

function formatTime(value?: string) {
  if (!value) return '-'
  try { return new Date(value).toLocaleString() } catch { return value }
}

function AuditPageControls({
  total,
  pageSize,
  offset,
  onOffsetChange,
  onPageSizeChange,
}: {
  total: number
  pageSize: number
  offset: number
  onOffsetChange: (next: number) => void
  onPageSizeChange: (next: number) => void
}) {
  const safeTotal = Math.max(0, Number(total || 0))
  const safePageSize = Math.max(1, Number(pageSize || 100))
  const safeOffset = Math.max(0, Number(offset || 0))
  const page = Math.floor(safeOffset / safePageSize) + 1
  const pages = Math.max(1, Math.ceil(safeTotal / safePageSize))
  return (
    <div className="pagination-bar">
      <div className="pagination-info">第 {page}/{pages} 页 · 共 {safeTotal} 条</div>
      <div className="pagination-controls">
        <label className="pagination-size-label">每页
          <select value={safePageSize} onChange={(e) => onPageSizeChange(Number(e.target.value))}>
            {[50, 100, 200, 500].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <button className="pagination-btn" disabled={safeOffset <= 0} onClick={() => onOffsetChange(0)}>首页</button>
        <button className="pagination-btn" disabled={safeOffset <= 0} onClick={() => onOffsetChange(Math.max(0, safeOffset - safePageSize))}>上一页</button>
        <button className="pagination-btn" disabled={safeOffset + safePageSize >= safeTotal} onClick={() => onOffsetChange(safeOffset + safePageSize)}>下一页</button>
        <button className="pagination-btn" disabled={safeOffset + safePageSize >= safeTotal} onClick={() => onOffsetChange(Math.max(0, (pages - 1) * safePageSize))}>末页</button>
      </div>
    </div>
  )
}

export default function AuditLogPage() {
  const [items, setItems] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [pageSize, setPageSize] = useState(100)
  const [queryState, setQueryState] = useUrlQueryState({ action: '' })
  const action = queryState.action
  const setAction = (v: string) => setQueryState({ action: v })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [retention, setRetention] = useState<any>({})
  const [preview, setPreview] = useState<any>(null)
  const [retentionMsg, setRetentionMsg] = useState('')
  const [chains, setChains] = useState<any[]>([])
  const [chainLoading, setChainLoading] = useState(false)
  const [selectedChain, setSelectedChain] = useState<any>(null)
  const [chainKind, setChainKind] = useState('')
  const [chainError, setChainError] = useState('')
  const [cleanupDialogOpen, setCleanupDialogOpen] = useState(false)

  async function load() {
    setLoading(true)
    setError('')
    try {
      const res: any = await auditLog.list({ limit: pageSize, offset, action: action || undefined })
      setItems(res.data?.items || [])
      setTotal(Number(res.data?.total || 0))
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setLoading(false)
    }
  }


  async function loadOperationChains() {
    setChainLoading(true)
    setChainError('')
    try {
      const res: any = await auditLog.operationChains({ limit: 80, kind: chainKind || undefined })
      setChains(res.data?.items || [])
    } catch (e: any) {
      setChainError(e?.message || String(e))
    } finally {
      setChainLoading(false)
    }
  }

  async function openOperationChain(chainId: string) {
    setChainError('')
    try {
      const res: any = await auditLog.operationChain(chainId)
      setSelectedChain(res.data)
    } catch (e: any) {
      setChainError(e?.message || String(e))
    }
  }

  async function loadRetention() {
    try {
      const res: any = await deployment.retention()
      setRetention(res.data?.policy || {})
      setPreview(res.data?.preview || null)
    } catch {}
  }

  async function saveRetention() {
    const res: any = await deployment.updateRetention(retention)
    setRetention(res.data?.policy || retention)
    setPreview(res.data?.preview || null)
    setRetentionMsg('审计清理策略已保存')
  }

  async function previewCleanup() {
    const res: any = await deployment.previewRetention()
    setPreview(res.data)
    setRetentionMsg('已生成清理预览，未删除任何数据')
  }

  const cleanupCount = Number(preview?.candidate_counts?.audit_logs || 0) + Number(preview?.candidate_counts?.audit_records || 0) + Number(preview?.candidate_counts?.tool_call_logs || 0) + Number(preview?.candidate_counts?.tool_plans || 0)

  async function runCleanup() {
    const res: any = await deployment.cleanupRetention({ dry_run: false })
    setPreview(res.data)
    setRetentionMsg('清理完成')
    load()
  }

  async function confirmCleanup() {
    setCleanupDialogOpen(false)
    await runCleanup()
  }


  useEffect(() => { loadRetention(); loadOperationChains() }, [])
  useEffect(() => { load() }, [offset, pageSize, action])

  return (
    <div className="page-container">
      <PageHeader title="审计日志" description="集中查看高风险操作、发布、SQL 查询、密钥和维护任务的审计记录。" actions={<div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}><FavoriteButton url={ROUTES.audit} label="审计日志" category="audit" /><Link className="btn btn-subtle" to={ROUTES.tasks}>任务中心</Link><Link className="btn btn-subtle" to={ROUTES.reports}>报告中心</Link><a className="btn" href={auditLog.exportUrl({ limit: 1000, action: action || undefined })} target="_blank" rel="noreferrer">导出 CSV</a><button className="btn primary" onClick={load} disabled={loading}>{loading ? '加载中...' : '刷新'}</button></div>} />
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="form-grid">
          <label>动作关键字
            <input value={action} onChange={(e) => { setAction(e.target.value); setOffset(0) }} placeholder="如 deploy / sql / maintenance" />
          </label>
          <div style={{ display: 'flex', alignItems: 'end' }}><button className="btn" onClick={load}>筛选</button></div>
        </div>
      </div>



      <div className="card" style={{ marginBottom: 16, borderColor: 'var(--primary-soft)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', marginBottom: 10 }}>
          <div>
            <h3 style={{ margin: 0 }}>MCP / AI 操作链路回放</h3>
            <p style={{ margin: '4px 0 0', color: 'var(--text-muted)', fontSize: 13 }}>把外部 Agent 请求、工具调用、风险策略、统一任务、发布计划、发布单和审计记录串成只读证据链；适合排查“外部 Agent 请求了什么、MCP 调用了什么、任务最终怎样”。</p>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <select value={chainKind} onChange={(e) => setChainKind(e.target.value)}>
              <option value="">全部链路</option>
              <option value="tool_call">工具调用</option>
              <option value="job">任务</option>
              <option value="plan">计划</option>
              <option value="deployment">发布</option>
            </select>
            <button className="btn" onClick={loadOperationChains} disabled={chainLoading}>{chainLoading ? '加载中...' : '刷新链路'}</button>
          </div>
        </div>
        {chainError && <div className="alert alert-error" style={{ marginBottom: 10 }}>{chainError}</div>}
        <div style={{ display: 'grid', gridTemplateColumns: selectedChain ? 'minmax(360px, 0.95fr) minmax(420px, 1.05fr)' : '1fr', gap: 12 }}>
          <div className="table-card" style={{ overflow: 'auto', maxHeight: 360 }}>
            <table className="data-table">
              <thead><tr><th>时间</th><th>链路</th><th>状态</th><th>风险</th><th>目标</th><th>操作</th></tr></thead>
              <tbody>
                {chains.map((chain) => (
                  <tr key={chain.chain_id}>
                    <td>{formatTime(chain.created_at)}</td>
                    <td><div style={{ fontWeight: 700 }}>{chain.title}</div><div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{chain.chain_id}</div></td>
                    <td>{chain.status || '-'}</td>
                    <td><span className={`badge ${['high', 'critical'].includes(chain.risk_level) ? 'danger' : chain.risk_level === 'medium' ? 'warning' : ''}`}>{chain.risk_level || 'low'}</span></td>
                    <td>{chain.target || '-'}</td>
                    <td><button className="btn small" onClick={() => openOperationChain(chain.chain_id)}>回放</button></td>
                  </tr>
                ))}
                {!chains.length && !chainLoading && <tr><td colSpan={6} style={{ color: 'var(--text-muted)' }}>暂无操作链路</td></tr>}
              </tbody>
            </table>
          </div>
          {selectedChain && (
            <div style={{ background: 'var(--bg-page)', border: '1px solid var(--border-strong)', borderRadius: 12, padding: 12, maxHeight: 520, overflow: 'auto' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'flex-start', marginBottom: 10 }}>
                <div>
                  <div style={{ fontWeight: 800 }}>链路回放：{selectedChain.chain_id}</div>
                  <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>节点 {selectedChain.summary?.node_count || 0} · 时间线 {selectedChain.summary?.timeline_count || 0} · 操作人 {selectedChain.summary?.operator || '-'}</div>
                </div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}><a className="btn small" href={reports.operationChainExportUrl(selectedChain.chain_id, 'json')} target="_blank" rel="noreferrer">导出报告</a><button className="btn small" onClick={() => navigator.clipboard?.writeText(JSON.stringify(selectedChain, null, 2))}>复制 JSON</button></div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 8, marginBottom: 12 }}>
                <div className="mini-card"><div className="muted">状态</div><strong>{selectedChain.summary?.status || '-'}</strong></div>
                <div className="mini-card"><div className="muted">最高风险</div><strong>{selectedChain.summary?.risk_level || '-'}</strong></div>
                <div className="mini-card"><div className="muted">工具调用</div><strong>{selectedChain.summary?.tool_call_count || 0}</strong></div>
                <div className="mini-card"><div className="muted">任务</div><strong>{selectedChain.summary?.job_count || 0}</strong></div>
              </div>
              <div style={{ display: 'grid', gap: 8 }}>
                {(selectedChain.timeline || []).map((event: any, idx: number) => (
                  <div key={`${event.node_id}-${idx}`} style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 10, background: 'var(--bg-card)' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                      <strong>{event.title}</strong>
                      <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>{formatTime(event.time)}</span>
                    </div>
                    <div style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 2 }}>{event.type} · {event.status || '-'} {event.risk_level ? `· ${event.risk_level}` : ''}</div>
                    {event.detail && <div style={{ marginTop: 6, whiteSpace: 'pre-wrap' }}>{event.detail}</div>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16, borderColor: 'var(--primary-soft)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', marginBottom: 10 }}>
          <div>
            <h3 style={{ margin: 0 }}>审计日志清理策略</h3>
            <p style={{ margin: '4px 0 0', color: 'var(--text-muted)', fontSize: 13 }}>普通审计、高风险审计、AI 工具调用和操作计划按统一保留策略清理；清理动作本身也会写入审计。</p>
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button className="btn" onClick={previewCleanup}>清理预览</button>
            <button className="btn primary" onClick={saveRetention}>保存策略</button>
            <button className="btn" onClick={() => setCleanupDialogOpen(true)} style={{ background: 'var(--danger-surface)', color: 'var(--danger)' }}>按策略清理</button>
          </div>
        </div>
        {retentionMsg && <div className="alert alert-success" style={{ marginBottom: 10 }}>{retentionMsg}</div>}
        <div className="form-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))' }}>
          <label>普通审计保留天数
            <input type="number" min={0} value={retention.audit_keep_days ?? ''} onChange={(e) => setRetention((p: any) => ({ ...p, audit_keep_days: Number(e.target.value || 0) }))} />
          </label>
          <label>高风险审计保留天数
            <input type="number" min={0} value={retention.audit_high_risk_keep_days ?? ''} onChange={(e) => setRetention((p: any) => ({ ...p, audit_high_risk_keep_days: Number(e.target.value || 0) }))} />
          </label>
          <label>审计最大条数
            <input type="number" min={0} value={retention.audit_keep_max ?? ''} onChange={(e) => setRetention((p: any) => ({ ...p, audit_keep_max: Number(e.target.value || 0) }))} />
          </label>
          <label>AI 工具调用保留天数
            <input type="number" min={0} value={retention.tool_call_keep_days ?? ''} onChange={(e) => setRetention((p: any) => ({ ...p, tool_call_keep_days: Number(e.target.value || 0) }))} />
          </label>
        </div>
        {preview?.candidate_counts && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 8, marginTop: 10 }}>
            {[['审计日志', 'audit_logs'], ['兼容审计', 'audit_records'], ['AI 工具调用', 'tool_call_logs'], ['AI 工具计划', 'tool_plans']].map(([label, key]) => (
              <div key={key} style={{ background: 'var(--bg-page)', border: '1px solid var(--border-strong)', borderRadius: 10, padding: 10 }}>
                <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{label}</div>
                <div style={{ fontWeight: 800, fontSize: 20 }}>{preview.candidate_counts[key] || 0}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      {error && <div className="alert alert-error">{error}</div>}
      {!items.length && !loading ? <EmptyState title="暂无审计记录" /> : (
        <div className="card table-card">
          <table className="data-table">
            <thead><tr><th>时间</th><th>动作</th><th>对象类型</th><th>对象</th><th>详情</th></tr></thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>{formatTime(item.created_at)}</td>
                  <td>{item.action}</td>
                  <td>{item.target_type || '-'}</td>
                  <td>{item.target_name || '-'}</td>
                  <td style={{ maxWidth: 520, whiteSpace: 'pre-wrap' }}>{item.details || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <AuditPageControls
        total={total}
        pageSize={pageSize}
        offset={offset}
        onOffsetChange={setOffset}
        onPageSizeChange={(next) => { setPageSize(next); setOffset(0) }}
      />

      <RiskConfirmDialog
        open={cleanupDialogOpen}
        title="确认清理审计与 AI 工具审计"
        description="将按当前策略清理审计日志、AI 工具调用和操作计划。清理动作本身会继续写入审计。"
        target={`预计清理 ${cleanupCount} 条记录`}
        confirmText={`CLEANUP AUDIT ${cleanupCount}`}
        value=""
        onValueChange={() => {}}
        onCancel={() => setCleanupDialogOpen(false)}
        onConfirm={confirmCleanup}
        riskLevel={cleanupCount > 0 ? 'high' : 'medium'}
        details={[
          { label: '审计日志', value: preview?.candidate_counts?.audit_logs || 0 },
          { label: '兼容审计', value: preview?.candidate_counts?.audit_records || 0 },
          { label: 'AI 工具调用', value: preview?.candidate_counts?.tool_call_logs || 0 },
          { label: 'AI 工具计划', value: preview?.candidate_counts?.tool_plans || 0 },
        ]}
        confirmButtonLabel="确认清理"
        confirmMode="one-click"
      />
    </div>
  )
}
