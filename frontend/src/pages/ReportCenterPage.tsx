import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { reports } from '../api'
import { ROUTES } from '../routes'
import { ConfirmDialog, EmptyState, PageHeader, FavoriteButton } from '../components/ui'

function formatTime(value?: string) {
  if (!value) return '-'
  try { return new Date(value).toLocaleString() } catch { return value }
}

function formatBytes(value?: number) {
  const n = Number(value || 0)
  if (!n) return '0 B'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function PageControls({
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
  const safePageSize = Math.max(1, Number(pageSize || 20))
  const safeOffset = Math.max(0, Number(offset || 0))
  const page = Math.floor(safeOffset / safePageSize) + 1
  const pages = Math.max(1, Math.ceil(safeTotal / safePageSize))
  const from = safeTotal === 0 ? 0 : safeOffset + 1
  const to = Math.min(safeOffset + safePageSize, safeTotal)

  return (
    <div className="pagination-bar">
      <div className="pagination-info">第 {page}/{pages} 页 · 显示 {from}-{to} / 共 {safeTotal} 条</div>
      <div className="pagination-controls">
        <label className="pagination-size-label">每页
          <select value={safePageSize} onChange={(e) => onPageSizeChange(Number(e.target.value))}>
            {[10, 20, 50, 100].map((n) => <option key={n} value={n}>{n}</option>)}
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

const TYPE_LABELS: Record<string, string> = {
  diagnostics: '系统诊断',
  operation_chain: '操作链路',
  operation_chains_index: '链路索引',
  deployment: '发布报告',
  inspection: '巡检报告',
}

export default function ReportCenterPage() {
  const [items, setItems] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [pageSize, setPageSize] = useState(20)
  const [summary, setSummary] = useState<any>({})
  const [types, setTypes] = useState<Record<string, any>>({})
  const [reportType, setReportType] = useState('')
  const [generateType, setGenerateType] = useState('diagnostics')
  const [targetId, setTargetId] = useState('')
  const [format, setFormat] = useState('json')
  const [focus, setFocus] = useState('general')
  const [includeRaw, setIncludeRaw] = useState(false)
  const [loading, setLoading] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [editTarget, setEditTarget] = useState<any>(null)
  const [editTitle, setEditTitle] = useState('')
  const [editStatus, setEditStatus] = useState('')
  const [editSummary, setEditSummary] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<any>(null)
  const [selectedReportIds, setSelectedReportIds] = useState<string[]>([])
  const [batchDeleteOpen, setBatchDeleteOpen] = useState(false)

  async function load() {
    setLoading(true)
    setError('')
    try {
      const [listRes, summaryRes, typesRes]: any[] = await Promise.all([
        reports.list({ report_type: reportType || undefined, limit: pageSize, offset }),
        reports.summary(),
        reports.types(),
      ])
      const nextItems = listRes.data?.items || []
      setItems(nextItems)
      setTotal(Number(listRes.data?.total || 0))
      setSummary(summaryRes.data || {})
      setTypes(typesRes.data?.types || {})
      const visibleIds = new Set(nextItems.map((item: any) => item.id))
      setSelectedReportIds((prev) => prev.filter((id) => visibleIds.has(id)))
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setLoading(false)
    }
  }

  async function generate() {
    setGenerating(true)
    setError('')
    setMessage('')
    try {
      const res: any = await reports.generate({
        report_type: generateType,
        target_id: targetId || undefined,
        format,
        focus: focus || undefined,
        include_raw: includeRaw,
      })
      setMessage(`报告已生成：${res.data?.report?.title || res.data?.report?.id}`)
      setTargetId('')
      await load()
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setGenerating(false)
    }
  }

  function openEdit(item: any) {
    setEditTarget(item)
    setEditTitle(item.title || '')
    setEditStatus(item.status || 'ready')
    setEditSummary(item.summary || '')
  }

  async function saveEdit() {
    if (!editTarget) return
    setError('')
    setMessage('')
    try {
      await reports.update(editTarget.id, { title: editTitle, status: editStatus, summary: editSummary })
      setEditTarget(null)
      setMessage('报告已更新')
      await load()
    } catch (e: any) {
      setError(e?.message || String(e))
    }
  }

  async function deleteReport() {
    if (!deleteTarget) return
    setError('')
    setMessage('')
    try {
      await reports.delete(deleteTarget.id)
      setDeleteTarget(null)
      setSelectedReportIds((prev) => prev.filter((id) => id !== deleteTarget.id))
      setMessage('报告已删除')
      await load()
    } catch (e: any) {
      setError(e?.message || String(e))
    }
  }

  function toggleReportSelection(id: string, checked: boolean) {
    setSelectedReportIds((prev) => checked ? Array.from(new Set([...prev, id])) : prev.filter((x) => x !== id))
  }

  async function deleteSelectedReports(ids = selectedReportIds) {
    if (!ids.length) return
    setError('')
    setMessage('')
    try {
      await reports.deleteMany({ report_ids: ids })
      setBatchDeleteOpen(false)
      setSelectedReportIds([])
      setMessage(`批量删除 ${ids.length} 份报告`)
      await load()
    } catch (e: any) {
      setError(e?.message || String(e))
    }
  }

  useEffect(() => { load() }, [reportType, offset, pageSize])

  const selectedType = types[generateType] || {}
  const requiresTarget = ['operation_chain', 'deployment'].includes(generateType)
  const generatableTypeKeys = Object.keys(types).filter((key) => types[key]?.can_generate !== false)
  const generateTypeKeys = generatableTypeKeys.length
    ? generatableTypeKeys
    : Object.keys(TYPE_LABELS).filter((key) => key !== 'db_query_export')
  const selectedFormats = selectedType.formats || ['json', 'md']
  const currentPageReportIds = items.map((item) => item.id)
  const allReportsOnPageSelected = currentPageReportIds.length > 0 && currentPageReportIds.every((id) => selectedReportIds.includes(id))

  useEffect(() => {
    if (generateTypeKeys.length > 0 && !generateTypeKeys.includes(generateType)) {
      setGenerateType(generateTypeKeys[0])
    }
  }, [generateType, generateTypeKeys.join('|')])

  useEffect(() => {
    if (selectedFormats.length > 0 && !selectedFormats.includes(format)) {
      setFormat(selectedFormats[0])
    }
  }, [format, generateType, selectedFormats.join('|')])

  return (
    <div className="page-container">
      <PageHeader
        title="报告中心"
        description="统一管理系统诊断、发布报告、巡检报告与操作链路报告；报告只打包已有证据，便于下载、归档和审计回放。"
        actions={<div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}><FavoriteButton url={ROUTES.reports} label="报告中心" category="reports" /><Link className="btn btn-subtle" to={ROUTES.audit}>审计日志</Link><Link className="btn btn-subtle" to={ROUTES.tasks}>任务中心</Link><button className="btn primary" onClick={load} disabled={loading}>{loading ? '加载中...' : '刷新'}</button></div>}
      />

      {error && <div className="alert alert-error" style={{ marginBottom: 12 }}>{error}</div>}
      {message && <div className="alert alert-success" style={{ marginBottom: 12 }}>{message}</div>}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12, marginBottom: 16 }}>
        <div className="mini-card"><div className="muted">最近报告</div><strong>{summary.total_recent || 0}</strong></div>
        <div className="mini-card"><div className="muted">报告类型</div><strong>{Object.keys(types).length || 0}</strong></div>
        <div className="mini-card"><div className="muted">总大小</div><strong>{formatBytes(summary.total_size_bytes)}</strong></div>
        <div className="mini-card"><div className="muted">最新报告</div><strong>{summary.latest?.report_type ? (TYPE_LABELS[summary.latest.report_type] || summary.latest.report_type) : '-'}</strong></div>
      </div>

      <div className="card" style={{ marginBottom: 16, borderColor: 'var(--primary-soft)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', marginBottom: 10 }}>
          <div>
            <h3 style={{ margin: 0 }}>生成报告</h3>
            <p style={{ margin: '4px 0 0', color: 'var(--text-muted)', fontSize: 13 }}>生成过程只读取现有诊断、审计、发布与操作链路数据；不会执行发布、恢复、删除、SQL 写入或终端命令。</p>
          </div>
          <span className="badge">low · read-only evidence</span>
        </div>
        <div className="form-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))' }}>
          <label>报告类型
            <select value={generateType} onChange={(e) => setGenerateType(e.target.value)}>
              {Object.keys(types).length ? Object.keys(types).map((key) => <option key={key} value={key}>{TYPE_LABELS[key] || key}</option>) : Object.keys(TYPE_LABELS).map((key) => <option key={key} value={key}>{TYPE_LABELS[key]}</option>)}
            </select>
          </label>
          <label>目标 ID {requiresTarget ? <span style={{ color: 'var(--danger)' }}>*</span> : null}
            <input value={targetId} onChange={(e) => setTargetId(e.target.value)} placeholder={generateType === 'operation_chain' ? 'tool:/job:/plan:/deployment:/audit:' : generateType === 'deployment' ? 'deployment_id' : '可留空'} />
          </label>
          <label>格式
            <select value={format} onChange={(e) => setFormat(e.target.value)}>
              {(selectedType.formats || ['json', 'md']).map((f: string) => <option key={f} value={f}>{f}</option>)}
            </select>
          </label>
          <label>聚焦
            <select value={focus} onChange={(e) => setFocus(e.target.value)}>
              {['general', 'frontend', 'mcp', 'deploy', 'backup'].map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
          </label>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginTop: 12, flexWrap: 'wrap' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-muted)' }}>
            <input type="checkbox" checked={includeRaw} onChange={(e) => setIncludeRaw(e.target.checked)} /> 包含更多原始证据
          </label>
          <button className="btn primary" onClick={generate} disabled={generating || (requiresTarget && !targetId.trim())}>{generating ? '生成中...' : '生成报告'}</button>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="form-grid" style={{ gridTemplateColumns: 'minmax(220px, 320px) auto' }}>
          <label>筛选类型
            <select value={reportType} onChange={(e) => { setReportType(e.target.value); setOffset(0) }}>
              <option value="">全部报告</option>
              {Object.keys(types).length ? Object.keys(types).map((key) => <option key={key} value={key}>{TYPE_LABELS[key] || key}</option>) : Object.keys(TYPE_LABELS).map((key) => <option key={key} value={key}>{TYPE_LABELS[key]}</option>)}
            </select>
          </label>
          <div style={{ display: 'flex', alignItems: 'end', gap: 8, flexWrap: 'wrap' }}>
            <button className="btn" onClick={load}>筛选</button>
            <button className="btn btn-danger" disabled={!selectedReportIds.length || loading} onClick={() => setBatchDeleteOpen(true)}>批量删除 ({selectedReportIds.length})</button>
          </div>
        </div>
      </div>

      {!items.length && !loading ? <EmptyState title="暂无报告" description="先生成一份系统诊断报告或操作链路报告。" /> : (
        <div className="card table-card">
          <table className="data-table">
            <thead><tr><th style={{ width: 36 }}><input type="checkbox" checked={allReportsOnPageSelected} onChange={(e) => setSelectedReportIds(e.target.checked ? currentPageReportIds : [])} aria-label="选择当前页报告" /></th><th style={{ width: 140 }}>时间</th><th style={{ minWidth: 200 }}>报告</th><th style={{ minWidth: 180 }}>目标</th><th style={{ width: 80 }}>格式</th><th style={{ width: 80 }}>大小</th><th style={{ width: 140 }}>SHA256</th><th>操作</th></tr></thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td><input type="checkbox" checked={selectedReportIds.includes(item.id)} onChange={(e) => toggleReportSelection(item.id, e.target.checked)} aria-label={`选择报告 ${item.title || item.id}`} /></td>
                  <td><span className="ellipsis" style={{ maxWidth: 130 }} title={formatTime(item.created_at)}>{formatTime(item.created_at)}</span></td>
                  <td>
                    <div className="ellipsis" style={{ fontWeight: 700, maxWidth: 360 }} title={item.title}>{item.title}</div>
                    <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{TYPE_LABELS[item.report_type] || item.report_type} · {item.summary || '-'}</div>
                  </td>
                  <td>
                    <div className="ellipsis" style={{ maxWidth: 220 }} title={item.target_type || '-'}>{item.target_type || '-'}</div>
                    <div style={{ color: 'var(--text-muted)', fontSize: 12 }} className="ellipsis">{item.target_id || '-'}</div>
                  </td>
                  <td><span className="badge">{item.format}</span></td>
                  <td>{formatBytes(item.size_bytes)}</td>
                  <td><code style={{ fontSize: 11, wordBreak: 'break-all' }} title={item.sha256}>{String(item.sha256 || '').slice(0, 16)}...</code></td>
                  <td>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      {item.format === 'html' && <a className="btn small" href={reports.downloadUrl(item.id)} target="_blank" rel="noreferrer">在线查看</a>}
                      <a className="btn small" href={reports.downloadUrl(item.id)} target="_blank" rel="noreferrer" download>{item.format === 'html' ? '下载' : '下载'}</a>
                      <button className="btn small" onClick={() => openEdit(item)}>编辑</button>
                      <button className="btn small btn-danger" onClick={() => setDeleteTarget(item)}>删除</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <PageControls
        total={total}
        pageSize={pageSize}
        offset={offset}
        onOffsetChange={setOffset}
        onPageSizeChange={(next) => { setPageSize(next); setOffset(0) }}
      />
      <ConfirmDialog
        open={Boolean(editTarget)}
        title="更新报告信息"
        description="仅更新报告元数据，不改写报告文件内容。"
        confirmLabel="保存更新"
        onCancel={() => setEditTarget(null)}
        onConfirm={saveEdit}
      >
        <div className="form-grid" style={{ gridTemplateColumns: '1fr', gap: 10 }}>
          <label>标题
            <input value={editTitle} onChange={(e) => setEditTitle(e.target.value)} />
          </label>
          <label>状态
            <select value={editStatus} onChange={(e) => setEditStatus(e.target.value)}>
              {['ready', 'archived', 'reviewed', 'invalid'].map((status) => <option key={status} value={status}>{status}</option>)}
            </select>
          </label>
          <label>摘要
            <textarea value={editSummary} onChange={(e) => setEditSummary(e.target.value)} rows={4} />
          </label>
        </div>
      </ConfirmDialog>
      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="删除报告"
        description={`确认删除 ${deleteTarget?.title || deleteTarget?.id}？会同时尝试删除本地报告文件。`}
        confirmLabel="删除"
        danger
        onCancel={() => setDeleteTarget(null)}
        onConfirm={deleteReport}
      />
      <ConfirmDialog
        open={batchDeleteOpen}
        title="批量删除报告"
        description={`确认删除选中的 ${selectedReportIds.length} 份报告？会同步尝试删除本地报告文件。`}
        confirmLabel="批量删除"
        danger
        onCancel={() => setBatchDeleteOpen(false)}
        onConfirm={() => deleteSelectedReports(selectedReportIds)}
      />
    </div>
  )
}
