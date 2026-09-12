import { useEffect, useState } from 'react'
import { inspection } from '../../api'
import { RiskBadge, StatusBadge } from '../../components/ui'
import { formatTime, riskLabel } from './inspectionHelpers'

export type IssueDetailModalProps = {
  issueId: string
  onClose: () => void
  /** 状态变更/删除后刷新外部列表 */
  onChanged?: () => void | Promise<void>
  /** 跳转到该风险问题所属的巡检详情 */
  onOpenRun?: (runId: string) => void
  /** 由外部提供删除动作（复用列表页的确认与刷新逻辑）；返回 false 表示未删除（例如用户取消确认） */
  onDelete?: (issueId: string) => unknown | Promise<unknown>
}

const STATUS_ACTIONS: Array<{ key: string; label: string }> = [
  { key: 'PROCESSING', label: '标记处理中' },
  { key: 'FIXED', label: '标记已修复' },
  { key: 'VERIFIED', label: '标记已验证' },
  { key: 'IGNORED', label: '忽略' },
]

const STATUS_TEXT: Record<string, string> = {
  OPEN: '待处理',
  PROCESSING: '处理中',
  FIXED: '已修复',
  VERIFIED: '已验证',
  IGNORED: '已忽略',
}

function statusText(value?: string) {
  const key = (value || '').toUpperCase()
  return STATUS_TEXT[key] ? `${STATUS_TEXT[key]}（${key}）` : value || '-'
}

/**
 * 风险问题详情弹窗：巡检中心「风险问题」列表与概览「最近风险」共用。
 * 自身按 issue_id 拉取完整详情（含证据），避免依赖列表行数据的字段完整度。
 */
export function IssueDetailModal({ issueId, onClose, onChanged, onOpenRun, onDelete }: IssueDetailModalProps) {
  const [issue, setIssue] = useState<any>(null)
  const [evidence, setEvidence] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  async function load() {
    setLoading(true)
    setError('')
    try {
      const res: any = await inspection.issueDetail(issueId)
      const data = res?.data || null
      setIssue(data)
      if (data?.evidence_id) {
        try {
          const ev: any = await inspection.evidenceDetail(data.evidence_id)
          setEvidence(ev?.data || null)
        } catch {
          setEvidence(null)
        }
      } else {
        setEvidence(null)
      }
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [issueId])

  async function changeStatus(status: string) {
    setBusy(status)
    setError('')
    try {
      await inspection.updateIssue(issueId, { status })
      await load()
      await onChanged?.()
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setBusy('')
    }
  }

  async function remove() {
    if (!onDelete) return
    setBusy('delete')
    setError('')
    try {
      const result = await onDelete(issueId)
      if (result === false) return  // 用户取消确认或删除失败：保持弹窗，展示错误
      onClose()
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setBusy('')
    }
  }

  const objectLabel = issue ? (issue.server_id || issue.project_id || '-') : '-'

  return (
    <div className="inspection-modal-overlay" onClick={onClose}>
      <div className="panel-card inspection-issue-detail-modal" onClick={(e) => e.stopPropagation()}>
        <div className="inspection-modal-header">
          <div>
            <h2 style={{ margin: 0 }}>风险问题详情</h2>
            <p className="muted" style={{ margin: '4px 0 0' }}>
              {issue?.id ? `问题 ID: ${issue.id} · ` : ''}
              {issue?.scope_type ? `${issue.scope_type} · ` : ''}对象：{objectLabel}
            </p>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            {issue && <RiskBadge level={issue.risk_level} label={riskLabel(issue.risk_level)} />}
            {issue && <StatusBadge value={issue.status} />}
            <button className="btn btn-subtle" onClick={onClose}>关闭</button>
          </div>
        </div>

        {loading && <div className="alert alert-warning">加载详情…</div>}
        {error && <div className="alert alert-error">{error}</div>}
        {!loading && !issue && !error && <div className="alert alert-warning">未找到该风险问题，可能已被删除。</div>}

        {issue && (
          <>
            <div style={{ display: 'grid', gap: 12 }}>
              <div className="mini-card"><strong>{issue.title || '-'}</strong></div>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 8 }}>
                <div className="stat-card"><span>风险等级</span><strong>{riskLabel(issue.risk_level)}</strong><small>{issue.risk_level || '-'}</small></div>
                <div className="stat-card"><span>当前状态</span><strong>{statusText(issue.status)}</strong><small>{issue.owner_id ? `负责人 ${issue.owner_id}` : '未指派负责人'}</small></div>
                <div className="stat-card"><span>巡检对象</span><strong>{objectLabel}</strong><small>{issue.scope_type || '-'}</small></div>
                <div className="stat-card"><span>最近更新</span><strong>{formatTime(issue.updated_at)}</strong><small>创建 {formatTime(issue.created_at)}</small></div>
              </div>

              <div>
                <strong>问题描述</strong>
                <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', marginTop: 4 }}>{issue.description || '-'}</div>
              </div>

              <div>
                <strong>整改建议</strong>
                <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', marginTop: 4 }}>{issue.suggestion || '-'}</div>
              </div>

              <div>
                <strong>闭环时间线</strong>
                <div className="table-scroll" style={{ marginTop: 4 }}>
                  <table className="data-table data-table--compact">
                    <thead><tr><th>创建时间</th><th>截止时间</th><th>修复时间</th><th>验证时间</th><th>更新时间</th></tr></thead>
                    <tbody><tr>
                      <td>{formatTime(issue.created_at)}</td>
                      <td>{formatTime(issue.deadline_at)}</td>
                      <td>{formatTime(issue.fixed_at)}</td>
                      <td>{formatTime(issue.verified_at)}</td>
                      <td>{formatTime(issue.updated_at)}</td>
                    </tr></tbody>
                  </table>
                </div>
              </div>

              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                {issue.run_id
                  ? <button className="btn btn-subtle" onClick={() => onOpenRun?.(issue.run_id)} disabled={!onOpenRun}>查看所属巡检详情</button>
                  : <span className="muted">未关联巡检记录</span>}
                <small className="muted">
                  巡检记录 {issue.run_id || '-'} · 巡检项 {issue.item_result_id || '-'} · 证据 {issue.evidence_id || '-'}
                </small>
              </div>

              {evidence && (
                <div>
                  <strong>问题证据</strong>
                  <div className="muted" style={{ marginTop: 4 }}>
                    来源：{evidence.source_type || '-'} · 路径：{evidence.source_path || '-'}
                    {evidence.masked ? ' · 已脱敏' : ''}
                  </div>
                  {evidence.command && <pre className="inspection-command-cell" style={{ marginTop: 4 }}>{evidence.command}</pre>}
                  <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 240, overflow: 'auto', background: 'rgba(15,23,42,.05)', borderRadius: 12, padding: 12, marginTop: 4 }}>
                    {evidence.content_snapshot || '（无内容快照）'}
                  </pre>
                </div>
              )}
            </div>

            <div className="inspection-modal-actions">
              {STATUS_ACTIONS.map((a) => (
                <button
                  key={a.key}
                  className="btn btn-subtle"
                  disabled={!!busy || (issue.status || '').toUpperCase() === a.key}
                  onClick={() => changeStatus(a.key)}
                >
                  {busy === a.key ? '提交中…' : a.label}
                </button>
              ))}
              {onDelete && (
                <button className="btn btn-danger" disabled={!!busy} onClick={remove}>
                  {busy === 'delete' ? '删除中…' : '删除'}
                </button>
              )}
              <button className="btn primary" onClick={onClose}>关闭</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
