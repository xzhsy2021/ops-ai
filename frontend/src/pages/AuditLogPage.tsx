import { useEffect, useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { auditLog, deployment, reports } from '../api'
import { ROUTES } from '../routes'
import { EmptyState, PageHeader, RiskConfirmDialog, FavoriteButton } from '../components/ui'
import { useUrlQueryState } from '../hooks/useUrlQueryState'

function formatTime(value?: string) {
  if (!value) return '-'
  try { return new Date(value).toLocaleString() } catch { return value }
}

function formatDay(value?: string) {
  if (!value) return '-'
  try { return new Date(value).toISOString().slice(0, 10) } catch { return value }
}

// ── 快捷过滤：按动作前缀分组（生产审计分布实测 2026-09-04）──
const QUICK_FILTERS: { label: string; match: string; hint: string }[] = [
  { label: 'AI 工具', match: 'tool.', hint: 'tool.success / blocked / failed / queued' },
  { label: '巡检', match: 'inspection.', hint: 'issue.delete / profile.run' },
  { label: '服务器', match: 'server.', hint: 'update / create / delete / probe' },
  { label: '发布', match: 'deploy', hint: 'deploy.*' },
  { label: '数据库', match: 'db.', hint: 'backup / restore / delete' },
  { label: '系统配置', match: 'system.', hint: 'update / service / environment' },
  { label: '审批', match: 'approval', hint: 'temporary_approval / 审批链' },
  { label: '报告', match: 'report.', hint: 'report.delete' },
]

// ── 分页控件 ──
function Pagination({
  total, pageSize, offset, onOffsetChange, onPageSizeChange,
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

// ── 日志详情弹窗 ──
function LogDetailModal({ item, onClose }: { item: any; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
    }
  }, [onClose])
  let details: any = item.details
  try {
    const t = typeof details === 'string' ? details.trim() : ''
    if (t.startsWith('{') || t.startsWith('[')) details = JSON.stringify(JSON.parse(t), null, 2)
  } catch { /* 保持原文 */ }
  return (
    <div
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 1000, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '40px 16px', overflowY: 'auto' }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="card" style={{ width: '760px', maxWidth: '100%', padding: 20 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 14 }}>
          <div>
            <div style={{ fontSize: 16, fontWeight: 700 }}>{item.action}</div>
            <div style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 2 }}>
              #{item.id} · {formatTime(item.created_at)}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn small" onClick={() => navigator.clipboard?.writeText(JSON.stringify(item, null, 2))}>复制 JSON</button>
            <button className="btn small" onClick={onClose}>关闭 ESC</button>
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 8, marginBottom: 14 }}>
          <div className="mini-card"><div className="muted">对象类型</div><strong>{item.target_type || '-'}</strong></div>
          <div className="mini-card"><div className="muted">对象</div><strong style={{ wordBreak: 'break-all' }}>{item.target_name || '-'}</strong></div>
          <div className="mini-card"><div className="muted">日期</div><strong>{formatDay(item.created_at)}</strong></div>
        </div>
        <div style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 6 }}>详情</div>
        <pre style={{
          margin: 0, padding: 14, background: 'var(--bg-page)', border: '1px solid var(--border-strong)',
          borderRadius: 10, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          fontFamily: 'var(--font-mono, monospace)', fontSize: 12.5, maxHeight: '52vh', overflow: 'auto',
        }}>{details ?? '-'}</pre>
      </div>
    </div>
  )
}

// ── 主页面 ──
export default function AuditLogPage() {
  const [activeTab, setActiveTab] = useState<'logs' | 'chains' | 'retention'>('logs')

  // 日志查询工作台
  const [items, setItems] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [pageSize, setPageSize] = useState(100)
  const [queryState, setQueryState] = useUrlQueryState({ action: '' })
  const urlAction = queryState.action
  const [actionInput, setActionInput] = useState(urlAction)
  const [appliedAction, setAppliedAction] = useState(urlAction)
  const [quickFilter, setQuickFilter] = useState('')
  const [targetInput, setTargetInput] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<any>(null)

  // 链路回放
  const [chains, setChains] = useState<any[]>([])
  const [chainLoading, setChainLoading] = useState(false)
  const [selectedChain, setSelectedChain] = useState<any>(null)
  const [chainKind, setChainKind] = useState('')
  const [chainError, setChainError] = useState('')

  // 清理策略
  const [retention, setRetention] = useState<any>({})
  const [preview, setPreview] = useState<any>(null)
  const [retentionMsg, setRetentionMsg] = useState('')
  const [cleanupDialogOpen, setCleanupDialogOpen] = useState(false)

  const effAction = useMemo(() => {
    // 快捷过滤 + 手输关键字取并集：同时设置时手输优先
    if (appliedAction) return appliedAction
    if (quickFilter) return quickFilter
    return ''
  }, [appliedAction, quickFilter])

  async function load() {
    setLoading(true)
    setError('')
    try {
      const res: any = await auditLog.list({ limit: pageSize, offset, action: effAction || undefined })
      setItems(res.data?.items || [])
      setTotal(Number(res.data?.total || 0))
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setLoading(false)
    }
  }

  // 客户端二次过滤（对象名 / 日期范围——后端未支持，前端补足）
  const filteredItems = useMemo(() => {
    let rows = items
    if (targetInput.trim()) {
      const q = targetInput.trim().toLowerCase()
      rows = rows.filter((r) =>
        (r.target_name || '').toLowerCase().includes(q) || (r.target_type || '').toLowerCase().includes(q))
    }
    if (startDate) rows = rows.filter((r) => formatDay(r.created_at) >= startDate)
    if (endDate) rows = rows.filter((r) => formatDay(r.created_at) <= endDate)
    return rows
  }, [items, targetInput, startDate, endDate])

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

  // 初始加载
  useEffect(() => { loadRetention(); loadOperationChains() }, [])
  useEffect(() => { load() }, [offset, pageSize, effAction])

  // URL action 同步（外部跳转带 ?action= 进来）
  useEffect(() => {
    if (urlAction && urlAction !== appliedAction) {
      setAppliedAction(urlAction)
      setActionInput(urlAction)
      setOffset(0)
    }
  }, [urlAction])

  const applyFilters = () => {
    setQueryState({ action: actionInput })
    setAppliedAction(actionInput)
    setQuickFilter('')
    setOffset(0)
  }

  const applyQuickFilter = (match: string) => {
    const next = quickFilter === match ? '' : match
    setQuickFilter(next)
    setActionInput('')
    setAppliedAction('')
    setQueryState({ action: '' })
    setOffset(0)
  }

  const clearAll = () => {
    setQuickFilter('')
    setActionInput('')
    setAppliedAction('')
    setQueryState({ action: '' })
    setTargetInput('')
    setStartDate('')
    setEndDate('')
    setOffset(0)
  }

  const inputStyle: React.CSSProperties = {
    padding: '7px 10px', background: 'var(--bg-surface)', border: '1px solid var(--border-strong)',
    borderRadius: 6, color: 'var(--text-primary)', fontSize: 13, width: '100%',
  }

  const hasAnyFilter = !!(quickFilter || appliedAction || targetInput || startDate || endDate)

  return (
    <div className="page-container">
      <PageHeader
        title="审计日志"
        description={`共 ${total} 条记录${effAction ? ` · 筛选: ${effAction}` : ''}`}
        actions={
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <FavoriteButton url={ROUTES.audit} label="审计日志" category="audit" />
            <Link className="btn btn-subtle" to={ROUTES.tasks}>任务中心</Link>
            <a className="btn" href={auditLog.exportUrl({ limit: 1000, action: effAction || undefined })} target="_blank" rel="noreferrer">导出 CSV</a>
            <button className="btn primary" onClick={load} disabled={loading}>{loading ? '加载中...' : '刷新'}</button>
          </div>
        }
      />

      {/* 标签栏 */}
      <div className="file-tab-bar" role="tablist">
        <button className={activeTab === 'logs' ? 'is-active' : ''} onClick={() => setActiveTab('logs')}>日志查询</button>
        <button className={activeTab === 'chains' ? 'is-active' : ''} onClick={() => setActiveTab('chains')}>操作链路回放</button>
        <button className={activeTab === 'retention' ? 'is-active' : ''} onClick={() => setActiveTab('retention')}>清理策略</button>
      </div>

      {/* ══ 标签1：日志查询工作台 ══ */}
      {activeTab === 'logs' && (
        <>
          <div className="card" style={{ marginBottom: 14 }}>
            {/* 快捷过滤 chips */}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
              <span style={{ fontSize: 12, color: 'var(--text-muted)', alignSelf: 'center' }}>快捷：</span>
              {QUICK_FILTERS.map((f) => (
                <button key={f.match} type="button" title={f.hint}
                  onClick={() => applyQuickFilter(f.match)}
                  style={{
                    padding: '4px 12px', borderRadius: 14, fontSize: 12.5, cursor: 'pointer',
                    border: `1px solid ${quickFilter === f.match ? 'var(--brand)' : 'var(--border-strong)'}`,
                    background: quickFilter === f.match ? 'var(--action-bg)' : 'var(--bg-surface)',
                    color: quickFilter === f.match ? 'var(--brand)' : 'var(--text-secondary)',
                    fontWeight: quickFilter === f.match ? 600 : 400,
                  }}>
                  {f.label}
                </button>
              ))}
            </div>
            {/* 查询行 */}
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
              <div style={{ flex: '1 1 200px' }}>
                <label style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>动作关键字</label>
                <input
                  style={inputStyle}
                  value={actionInput}
                  onChange={(e) => setActionInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') applyFilters() }}
                  placeholder="如 tool.success / server.update（回车应用）"
                />
              </div>
              <div style={{ flex: '1 1 200px' }}>
                <label style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>对象 / 类型包含</label>
                <input
                  style={inputStyle}
                  value={targetInput}
                  onChange={(e) => setTargetInput(e.target.value)}
                  placeholder="如 crypto-trader / server（本页过滤）"
                />
              </div>
              <div style={{ flex: '0 1 150px' }}>
                <label style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>起始日期</label>
                <input type="date" style={inputStyle} value={startDate} onChange={(e) => setStartDate(e.target.value)} />
              </div>
              <div style={{ flex: '0 1 150px' }}>
                <label style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>结束日期</label>
                <input type="date" style={inputStyle} value={endDate} onChange={(e) => setEndDate(e.target.value)} />
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn primary" onClick={applyFilters}>查询</button>
                {hasAnyFilter && <button className="btn" onClick={clearAll}>清除</button>}
              </div>
            </div>
            {effAction && (
              <div style={{ marginTop: 10, fontSize: 12.5, color: 'var(--text-muted)' }}>
                当前动作筛选 <code style={{ background: 'var(--bg-page)', padding: '2px 6px', borderRadius: 4 }}>{effAction}</code>
                {targetInput && <> · 对象含 <code style={{ background: 'var(--bg-page)', padding: '2px 6px', borderRadius: 4 }}>{targetInput}</code></>}
                {(startDate || endDate) && <> · 日期 {startDate || '…'} ~ {endDate || '…'}</>}
                <span style={{ color: 'var(--warning, #b26a00)' }}>（对象/日期为本页内过滤，翻页仅作用于动作筛选）</span>
              </div>
            )}
          </div>

          {error && <div className="alert alert-error" style={{ marginBottom: 12 }}>{error}</div>}

          <Pagination
            total={total}
            pageSize={pageSize}
            offset={offset}
            onOffsetChange={setOffset}
            onPageSizeChange={(next) => { setPageSize(next); setOffset(0) }}
          />

          {!filteredItems.length && !loading ? (
            <EmptyState title="无匹配审计记录" description={hasAnyFilter ? '试试放宽筛选条件' : undefined} />
          ) : (
            <div className="card table-card">
              <table className="data-table">
                <thead>
                  <tr>
                    <th style={{ width: 160 }}>时间</th>
                    <th style={{ width: 220 }}>动作</th>
                    <th style={{ width: 120 }}>对象类型</th>
                    <th>对象</th>
                    <th style={{ width: 70 }}>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredItems.map((item) => (
                    <tr key={item.id} style={{ cursor: 'pointer' }} onClick={() => setSelected(item)}>
                      <td style={{ whiteSpace: 'nowrap' }}>{formatTime(item.created_at)}</td>
                      <td><span className="ellipsis" style={{ maxWidth: 220, fontFamily: 'var(--font-mono, monospace)', fontSize: 12.5 }} title={item.action}>{item.action}</span></td>
                      <td>{item.target_type || '-'}</td>
                      <td><span className="ellipsis" style={{ maxWidth: 320 }} title={item.target_name || '-'}>{item.target_name || '-'}</span></td>
                      <td><button className="btn small" onClick={(e) => { e.stopPropagation(); setSelected(item) }}>详情</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 8 }}>
            点击行或「详情」查看完整记录（含 JSON 格式化详情）；<code>复制 JSON</code> 可直接贴给 agent 排查。
          </div>
        </>
      )}

      {/* ══ 标签2：操作链路回放 ══ */}
      {activeTab === 'chains' && (
        <div className="card" style={{ borderColor: 'var(--primary-soft)' }}>
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
            <div className="table-card" style={{ overflow: 'auto', maxHeight: 420 }}>
              <table className="data-table">
                <thead><tr><th style={{ width: 140 }}>时间</th><th style={{ minWidth: 220 }}>链路</th><th style={{ width: 80 }}>状态</th><th style={{ width: 80 }}>风险</th><th style={{ minWidth: 180 }}>目标</th><th style={{ width: 80 }}>操作</th></tr></thead>
                <tbody>
                  {chains.map((chain) => (
                    <tr key={chain.chain_id} style={{ cursor: 'pointer' }} onClick={() => openOperationChain(chain.chain_id)}>
                      <td><span className="ellipsis" style={{ maxWidth: 130 }} title={formatTime(chain.created_at)}>{formatTime(chain.created_at)}</span></td>
                      <td><div className="ellipsis" style={{ fontWeight: 700, maxWidth: 300 }} title={chain.title}>{chain.title}</div><div style={{ color: 'var(--text-muted)', fontSize: 12, fontFamily: 'var(--font-mono, monospace)' }} className="ellipsis">{chain.chain_id}</div></td>
                      <td>{chain.status || '-'}</td>
                      <td><span className={`badge ${['high', 'critical'].includes(chain.risk_level) ? 'danger' : chain.risk_level === 'medium' ? 'warning' : ''}`}>{chain.risk_level || 'low'}</span></td>
                      <td><span className="ellipsis" style={{ maxWidth: 240 }} title={chain.target || '-'}>{chain.target || '-'}</span></td>
                      <td><button className="btn small" onClick={(e) => { e.stopPropagation(); openOperationChain(chain.chain_id) }}>回放</button></td>
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
      )}

      {/* ══ 标签3：清理策略 ══ */}
      {activeTab === 'retention' && (
        <div className="card" style={{ borderColor: 'var(--primary-soft)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', marginBottom: 10 }}>
            <div>
              <h3 style={{ margin: 0 }}>审计清理策略</h3>
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
      )}

      {/* 日志详情弹窗 */}
      {selected && (
        <LogDetailModal item={selected} onClose={() => setSelected(null)} />
      )}

      {/* 清理确认弹窗 */}
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
