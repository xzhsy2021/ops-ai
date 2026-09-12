import { memo, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useNotificationStore } from '../../store'
import { temporaryApprovals } from '../../api'
import { formatTime } from '../../utils/datetime.js'

type GrantInfo = {
  id?: string
  beneficiary_actor_key?: string
  channel?: string
  channel_account_id?: string
  conversation_id?: string
  system_id?: string
  environment_id?: string
  allowed_actions?: string[]
  authorized_identities?: { channel?: string; channel_account_id?: string; sender_id?: string }[]
  reason?: string
  starts_at?: string
  expires_at?: string
  status?: string
  requested_by_actor_key?: string
  approved_by_actor_key?: string
  request_message_id?: string
  confirmation_message_id?: string
  revoked_by_actor_key?: string
  revoked_at?: string
  revoke_reason?: string
  created_at?: string
}

const STATUS_TABS = [
  { key: '', label: '全部' },
  { key: 'PENDING', label: '待确认' },
  { key: 'ACTIVE', label: '生效中' },
  { key: 'REVOKED', label: '已撤销' },
  { key: 'EXPIRED', label: '已过期' },
]

const ACTION_LABELS: Record<string, string> = {
  FILE_UPLOAD: '包上传',
  RELEASE: '发布',
  SERVICE_CONTROL: '服务控制',
  HEALTH_CHECK: '健康检查',
}

function shortId(id?: string): string {
  return (id || '').slice(0, 8).toUpperCase()
}

function GrantModal({
  label,
  onClose,
  children,
}: {
  label: string
  onClose: () => void
  children: ReactNode
}) {
  return createPortal(
    <div
      className="modal-backdrop tool-token-modal-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className="card tool-token-modal-dialog" role="dialog" aria-modal="true" aria-label={label}>
        {children}
      </div>
    </div>,
    document.body,
  )
}

export const TemporaryApprovalPanel = memo(function TemporaryApprovalPanel({
  canManage = false,
}: {
  canManage?: boolean
}) {
  const addMessage = useNotificationStore((s) => s.addMessage)
  // notify 必须 useCallback 稳定引用，否则 load 的依赖 [status, notify]
  // 每次渲染都变 → useEffect([load]) 死循环触发 list 请求
  const notify = useCallback(
    (type: 'success' | 'error' | 'info', text: string) => addMessage(text, type),
    [addMessage],
  )

  const [grants, setGrants] = useState<GrantInfo[]>([])
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(false)
  const [confirmTarget, setConfirmTarget] = useState<GrantInfo | null>(null)
  const [confirmPhrase, setConfirmPhrase] = useState('')
  const [revokeTarget, setRevokeTarget] = useState<GrantInfo | null>(null)
  const [revokeReason, setRevokeReason] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<GrantInfo | null>(null)
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [batchAction, setBatchAction] = useState<'' | 'revoke' | 'delete'>('')
  const [submitting, setSubmitting] = useState(false)

  const load = useCallback(async (silent = false) => {
    // 静默刷新(silent=true)用于操作成功后:保留旧数据,新数据到达后直接替换,
    // 避免表格闪"加载中"或短暂空白,与其他页面动作体验一致。
    if (!silent) setLoading(true)
    try {
      const res = await temporaryApprovals.list({ status: status || undefined, limit: 100 })
      const data = (res as any).data
      const items = Array.isArray(data) ? data : ((data && data.items) || [])
      setGrants(items)
    } catch (e: any) {
      notify('error', String(e))
    } finally {
      if (!silent) setLoading(false)
    }
  }, [status, notify])

  // 页面加载与操作后拉取一次，不做轮询
  useEffect(() => {
    void load()
  }, [load])

  const activeCount = useMemo(() => grants.filter((g) => g.status === 'ACTIVE').length, [grants])

  const handleConfirm = async () => {
    if (!confirmTarget?.id) return
    setSubmitting(true)
    try {
      await temporaryApprovals.confirm(confirmTarget.id, { confirmation_phrase: confirmPhrase.trim() })
      notify('success', '临时授权已确认生效')
      setConfirmTarget(null)
      setConfirmPhrase('')
      await load(true)
    } catch (e: any) {
      notify('error', String(e?.response?.data?.message || e))
    } finally {
      setSubmitting(false)
    }
  }

  const handleRevoke = async () => {
    if (!revokeTarget?.id) return
    setSubmitting(true)
    try {
      await temporaryApprovals.revoke(revokeTarget.id, { reason: revokeReason.trim() || undefined })
      notify('success', '临时授权已撤销')
      setRevokeTarget(null)
      setRevokeReason('')
      await load(true)
    } catch (e: any) {
      notify('error', String(e?.response?.data?.message || e))
    } finally {
      setSubmitting(false)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget?.id) return
    setSubmitting(true)
    try {
      await temporaryApprovals.delete(deleteTarget.id)
      notify('success', '临时授权记录已删除')
      setDeleteTarget(null)
      await load(true)
    } catch (e: any) {
      notify('error', String(e?.response?.data?.message || e))
    } finally {
      setSubmitting(false)
    }
  }

  const toggleSelected = (id: string, checked: boolean) => {
    setSelectedIds((prev) => checked ? Array.from(new Set([...prev, id])) : prev.filter((x) => x !== id))
  }

  const toggleAllVisible = (checked: boolean) => {
    const visibleIds = grants.map((g) => g.id || '').filter(Boolean)
    setSelectedIds((prev) => checked ? Array.from(new Set([...prev, ...visibleIds])) : prev.filter((id) => !visibleIds.includes(id)))
  }

  const handleBatchRevoke = async () => {
    if (!selectedIds.length) return
    setBatchAction('revoke')
    setSubmitting(true)
    try {
      await temporaryApprovals.batchRevoke(selectedIds, '批量撤销')
      notify('success', `已批量撤销 ${selectedIds.length} 条授权`)
      setSelectedIds([])
      await load(true)
    } catch (e: any) {
      notify('error', String(e?.response?.data?.message || e))
    } finally {
      setBatchAction('')
      setSubmitting(false)
    }
  }

  const handleBatchDelete = async () => {
    if (!selectedIds.length) return
    if (!window.confirm(`确认永久删除选中的 ${selectedIds.length} 条授权记录？此操作不可恢复。`)) return
    setBatchAction('delete')
    setSubmitting(true)
    try {
      await temporaryApprovals.batchDelete(selectedIds)
      notify('success', `已批量删除 ${selectedIds.length} 条授权记录`)
      setSelectedIds([])
      await load(true)
    } catch (e: any) {
      notify('error', String(e?.response?.data?.message || e))
    } finally {
      setBatchAction('')
      setSubmitting(false)
    }
  }

  const identityText = (grant: GrantInfo): string => {
    const approved = grant.approved_by_actor_key || grant.requested_by_actor_key
    const channel = grant.channel || '-'
    return approved ? `${approved}（${channel}）` : `${channel}`
  }

  return (
    <div className="card" style={{ display: 'grid', gap: 14 }}>
      <div className="card-header">
        <div>
          <h2>临时自审批授权</h2>
          <span>{grants.length} 条记录，{activeCount} 条生效中</span>
        </div>
        {canManage && selectedIds.length > 0 && (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button
              className="btn btn-subtle"
              disabled={submitting}
              onClick={handleBatchRevoke}
              title="仅对生效中(ACTIVE)的授权生效"
            >
              {batchAction === 'revoke' ? '撤销中...' : `批量撤销（${selectedIds.length}）`}
            </button>
            <button className="btn btn-subtle btn-danger" disabled={submitting} onClick={handleBatchDelete}>
              {batchAction === 'delete' ? '删除中...' : `批量删除（${selectedIds.length}）`}
            </button>
          </div>
        )}
      </div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {STATUS_TABS.map((tab) => (
          <button
            key={tab.key || 'all'}
            className={`btn ${status === tab.key ? 'btn-primary' : 'btn-subtle'}`}
            onClick={() => setStatus(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="table-scroll">
        <table className="data-table data-table--compact">
          <thead>
            <tr>
              {canManage && (
                <th style={{ width: 36 }}>
                  <input
                    type="checkbox"
                    aria-label="全选"
                    checked={grants.length > 0 && selectedIds.length === grants.filter((g) => g.id).length}
                    onChange={(e) => toggleAllVisible(e.target.checked)}
                  />
                </th>
              )}
              <th>受益人</th>
              <th>通道/会话</th>
              <th>系统/环境</th>
              <th>动作</th>
              <th>原因</th>
              <th>审批人</th>
              <th>过期时间</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {loading && grants.length === 0 && (
              <tr><td colSpan={canManage ? 10 : 9} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>加载中...</td></tr>
            )}
            {!loading && grants.length === 0 && (
              <tr><td colSpan={canManage ? 10 : 9} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>暂无临时授权记录</td></tr>
            )}
            {grants.map((grant) => (
              <tr key={grant.id}>
                {canManage && (
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`选择 ${grant.beneficiary_actor_key || grant.id}`}
                      checked={selectedIds.includes(grant.id || '')}
                      onChange={(e) => toggleSelected(grant.id || '', e.target.checked)}
                    />
                  </td>
                )}
                <td>
                  <strong>{grant.beneficiary_actor_key || '-'}</strong>
                  {grant.id && <small style={{ display: 'block', color: 'var(--text-muted)' }}>{shortId(grant.id)}</small>}
                </td>
                <td style={{ fontFamily: 'monospace', fontSize: 12 }}>
                  {grant.channel || '-'}
                  {grant.channel_account_id ? `/${grant.channel_account_id}` : ''}
                  <small style={{ display: 'block', color: 'var(--text-muted)' }}>{grant.conversation_id || ''}</small>
                </td>
                <td>
                  {grant.system_id || '-'}
                  <small style={{ display: 'block', color: 'var(--text-muted)' }}>{grant.environment_id || ''}</small>
                </td>
                <td>
                  <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                    {(grant.allowed_actions || []).map((a) => (
                      <span className="scope-chip" key={a} title={a}>{ACTION_LABELS[a] || a}</span>
                    ))}
                  </div>
                </td>
                <td style={{ maxWidth: 220 }}>
                  <span title={grant.reason}>{grant.reason || '-'}</span>
                </td>
                <td>{identityText(grant)}</td>
                <td>{formatTime(grant.expires_at)}</td>
                <td>
                  <span className={`tag ${grant.status === 'ACTIVE' ? 'tag-success' : grant.status === 'PENDING' ? 'tag-warning' : grant.status === 'REVOKED' ? 'tag-danger' : ''}`}>
                    {grant.status || '-'}
                  </span>
                  {grant.revoke_reason && <small style={{ display: 'block', color: 'var(--text-muted)' }}>{grant.revoke_reason}</small>}
                </td>
                <td>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {grant.status === 'PENDING' && canManage && (
                      <button className="btn btn-subtle" onClick={() => { setConfirmTarget(grant); setConfirmPhrase('') }}>确认</button>
                    )}
                    {grant.status === 'ACTIVE' && canManage && (
                      <button className="btn btn-subtle" onClick={() => { setRevokeTarget(grant); setRevokeReason('') }}>撤销</button>
                    )}
                    {canManage && (
                      <button className="btn btn-subtle btn-danger" onClick={() => setDeleteTarget(grant)} title="彻底删除该授权记录，不可恢复">删除</button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {confirmTarget && (
        <GrantModal label="确认临时授权" onClose={() => setConfirmTarget(null)}>
          <div className="card-header">
            <div>
              <h2>确认临时授权</h2>
              <span>确认后授权立即生效，直到过期时间。</span>
            </div>
            <button className="btn btn-subtle" onClick={() => setConfirmTarget(null)}>关闭</button>
          </div>
          <div className="alert alert-warning">
            请输入包含授权短标识 <code>{shortId(confirmTarget.id)}</code> 的确认短语，例如：<code>确认授权 {shortId(confirmTarget.id)}</code>。
          </div>
          <div className="field-item">
            <label>确认短语</label>
            <input
              value={confirmPhrase}
              onChange={(e) => setConfirmPhrase(e.target.value)}
              placeholder={`确认授权 ${shortId(confirmTarget.id)}`}
            />
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            <button className="btn btn-subtle" onClick={() => setConfirmTarget(null)}>取消</button>
            <button className="btn btn-primary" disabled={submitting || !confirmPhrase.trim()} onClick={handleConfirm}>
              {submitting ? '确认中...' : '确认授权'}
            </button>
          </div>
        </GrantModal>
      )}

      {revokeTarget && (
        <GrantModal label="撤销临时授权" onClose={() => setRevokeTarget(null)}>
          <div className="card-header">
            <div>
              <h2>撤销临时授权</h2>
              <span>撤销后授权立即失效，受益人不再能自审批。</span>
            </div>
            <button className="btn btn-subtle" onClick={() => setRevokeTarget(null)}>关闭</button>
          </div>
          <div className="field-item">
            <label>撤销原因</label>
            <input
              value={revokeReason}
              onChange={(e) => setRevokeReason(e.target.value)}
              placeholder="例如：集成窗口已关闭"
            />
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            <button className="btn btn-subtle" onClick={() => setRevokeTarget(null)}>取消</button>
            <button className="btn btn-danger" disabled={submitting} onClick={handleRevoke}>
              {submitting ? '撤销中...' : '撤销授权'}
            </button>
          </div>
        </GrantModal>
      )}

      {deleteTarget && (
        <GrantModal label="删除临时授权" onClose={() => setDeleteTarget(null)}>
          <div className="card-header">
            <div>
              <h2>删除临时授权记录</h2>
              <span>彻底删除该授权记录，此操作不可恢复。</span>
            </div>
            <button className="btn btn-subtle" onClick={() => setDeleteTarget(null)}>关闭</button>
          </div>
          <div className="alert alert-warning">
            将永久删除授权 <code>{shortId(deleteTarget.id)}</code>（受益人
            <code>{deleteTarget.beneficiary_actor_key || '-'}</code>，状态
            {deleteTarget.status || '-'}）。若授权仍在生效，请先撤销再删除。
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            <button className="btn btn-subtle" onClick={() => setDeleteTarget(null)}>取消</button>
            <button className="btn btn-danger" disabled={submitting} onClick={handleDelete}>
              {submitting ? '删除中...' : '确认删除'}
            </button>
          </div>
        </GrantModal>
      )}
    </div>
  )
})
