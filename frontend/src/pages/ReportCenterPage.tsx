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

const TYPE_LABELS: Record<string, string> = {
  diagnostics: '系统诊断',
  ai_diagnostics: 'AI 诊断',
  operation_chain: '操作链路',
  operation_chains_index: '链路索引',
  deployment: '发布报告',
}

export default function ReportCenterPage() {
  const [items, setItems] = useState<any[]>([])
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

  async function load() {
    setLoading(true)
    setError('')
    try {
      const [listRes, summaryRes, typesRes]: any[] = await Promise.all([
        reports.list({ report_type: reportType || undefined, limit: 200 }),
        reports.summary(),
        reports.types(),
      ])
      setItems(listRes.data?.items || [])
      setSummary(summaryRes.data || {})
      setTypes(typesRes.data?.types || {})
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
      setMessage('报告已删除')
      await load()
    } catch (e: any) {
      setError(e?.message || String(e))
    }
  }

  useEffect(() => { load() }, [])
  useEffect(() => { load() }, [reportType])

  const selectedType = types[generateType] || {}
  const requiresTarget = ['operation_chain', 'deployment'].includes(generateType)
  const generatableTypeKeys = Object.keys(types).filter((key) => types[key]?.can_generate !== false)
  const generateTypeKeys = generatableTypeKeys.length
    ? generatableTypeKeys
    : Object.keys(TYPE_LABELS).filter((key) => key !== 'db_query_export')
  const selectedFormats = selectedType.formats || ['json', 'md']

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
        description="统一管理系统诊断、AI 分析、发布报告与 MCP/AI 操作链路报告；报告只打包已有证据，便于下载、归档和审计回放。"
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
            <select value={reportType} onChange={(e) => setReportType(e.target.value)}>
              <option value="">全部报告</option>
              {Object.keys(types).length ? Object.keys(types).map((key) => <option key={key} value={key}>{TYPE_LABELS[key] || key}</option>) : Object.keys(TYPE_LABELS).map((key) => <option key={key} value={key}>{TYPE_LABELS[key]}</option>)}
            </select>
          </label>
          <div style={{ display: 'flex', alignItems: 'end' }}><button className="btn" onClick={load}>筛选</button></div>
        </div>
      </div>

      {!items.length && !loading ? <EmptyState title="暂无报告" description="先生成一份系统诊断报告或操作链路报告。" /> : (
        <div className="card table-card">
          <table className="data-table">
            <thead><tr><th>时间</th><th>报告</th><th>目标</th><th>格式</th><th>大小</th><th>SHA256</th><th>操作</th></tr></thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>{formatTime(item.created_at)}</td>
                  <td><div style={{ fontWeight: 700 }}>{item.title}</div><div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{TYPE_LABELS[item.report_type] || item.report_type} · {item.summary || '-'}</div></td>
                  <td><div>{item.target_type || '-'}</div><div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{item.target_id || '-'}</div></td>
                  <td><span className="badge">{item.format}</span></td>
                  <td>{formatBytes(item.size_bytes)}</td>
                  <td><code style={{ fontSize: 11 }}>{String(item.sha256 || '').slice(0, 16)}...</code></td>
                  <td>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      <a className="btn small" href={reports.downloadUrl(item.id)} target="_blank" rel="noreferrer">下载</a>
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
    </div>
  )
}
