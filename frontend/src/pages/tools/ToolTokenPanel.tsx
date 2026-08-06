import { useMemo, useState } from 'react'
import { CopyButton } from '../../components/ui'

type TokenInfo = {
  id?: string
  name?: string
  description?: string
  status?: string
  created_at?: string
  last_used_at?: string
  expires_at?: string
  revoked_at?: string
  scopes?: string[]
  allow_write?: boolean
  allow_prod?: boolean
  bound_room_ids?: string[]
  approver_matrix_ids?: string[]
  key_prefix?: string
  value?: string
  masked_value?: string
}

type TokenPayload = {
  name: string
  description?: string
  scopes?: string[]
  allow_write?: boolean
  allow_prod?: boolean
  expires_in_days?: number
  bound_room_ids?: string[]
  approver_matrix_ids?: string[]
}

type TokenUpdatePayload = {
  name?: string
  description?: string
  scopes?: string[]
  allow_write?: boolean
  allow_prod?: boolean
  expires_in_days?: number
  revoke?: boolean
  bound_room_ids?: string[]
  approver_matrix_ids?: string[]
}

type PolicyPreviewResult = {
  tool?: string
  allowed?: boolean
  blocked_reason?: string
  risk?: string
  category?: string
  required_scopes?: string[]
}

type TokenTemplate = {
  key: string
  name: string
  description?: string
  notes?: string
  scopes: string[]
  allow_write: boolean
  allow_prod: boolean
  expires_in_days: number
}

const DEFAULT_SCOPES = ['ops:read', 'ops:write']
const FALLBACK_TOKEN_TEMPLATES: TokenTemplate[] = [
  {
    key: 'readonly-ai',
    name: 'Readonly AI',
    description: '只读 AI/Agent 接入',
    notes: '仅 ops:read，不能跑巡检 execute、不能写入',
    scopes: ['ops:read'],
    allow_write: false,
    allow_prod: false,
    expires_in_days: 90,
  },
  {
    key: 'inspection-ai',
    name: 'Inspection AI',
    description: '巡检 Agent 接入',
    notes: 'ops:read + ops:write + server:read + audit:read，可跑 Path A 巡检',
    scopes: ['ops:read', 'ops:write', 'server:read', 'audit:read'],
    allow_write: true,
    allow_prod: false,
    expires_in_days: 90,
  },
  {
    key: 'operator-human',
    name: 'Operator Human',
    description: '运维人员接入',
    notes: '允许发布计划/预检/执行，不含生产环境',
    scopes: ['ops:read', 'ops:write', 'server:read', 'audit:read', 'package:write', 'deploy:plan', 'deploy:precheck', 'deploy:execute'],
    allow_write: true,
    allow_prod: false,
    expires_in_days: 90,
  },
  {
    key: 'admin-breakglass',
    name: 'Admin Breakglass',
    description: '管理员紧急接入',
    notes: '全部权限，含生产环境，仅在紧急情况下使用',
    scopes: ['ops:read', 'ops:write', 'server:read', 'server:write', 'audit:read', 'package:write', 'package:cleanup', 'deploy:plan', 'deploy:precheck', 'deploy:execute', 'db:write'],
    allow_write: true,
    allow_prod: true,
    expires_in_days: 7,
  },
]

const SCOPE_LABELS: Record<string, string> = {
  'ops:read': 'ops:读',
  'ops:write': 'ops:写',
  'server:read': '服务器:读',
  'server:write': '服务器:写',
  'audit:read': '审计:读',
  'package:write': '包:写',
  'package:cleanup': '包:清理',
  'deploy:plan': '部署:计划',
  'deploy:precheck': '部署:预检',
  'deploy:execute': '部署:执行',
  'db:write': 'DB:写',
}

const DANGEROUS_SCOPES = new Set(['server:write', 'package:cleanup', 'deploy:execute', 'db:write'])

function scopesToText(scopes: string[]): string {
  return (scopes || []).join('\n')
}

function textToScopes(text: string): string[] {
  return text
    .split(/[\n,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

function formatScopeLabel(scope: string): string {
  return SCOPE_LABELS[scope] || scope
}

function hasDangerousScope(text: string): boolean {
  return textToScopes(text).some((s) => DANGEROUS_SCOPES.has(s))
}

function templateTitle(tpl: TokenTemplate): string {
  return [tpl.description, tpl.notes].filter(Boolean).join(' — ')
}

function formatTime(s?: string): string {
  if (!s) return '-'
  const d = new Date(s)
  if (isNaN(d.getTime())) return s
  return d.toLocaleString('zh-CN', { hour12: false })
}

export function ToolTokenPanel({
  tokens,
  loading,
  canDelete = false,
  tokenTemplates,
  onGenerate,
  onUpdate,
  onRevoke,
  onDelete,
  onPreviewPolicy,
}: {
  tokens: TokenInfo[]
  loading?: boolean
  canDelete?: boolean
  tokenTemplates?: TokenTemplate[]
  onGenerate: (data: TokenPayload) => Promise<void>
  onUpdate: (tokenId: string, data: TokenUpdatePayload) => Promise<void>
  onRevoke: (tokenId: string) => void
  onDelete: (tokenId: string) => void
  onPreviewPolicy: (data: Record<string, any>) => Promise<PolicyPreviewResult>
}) {
  const templates = tokenTemplates?.length ? tokenTemplates : FALLBACK_TOKEN_TEMPLATES
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [scopesText, setScopesText] = useState(scopesToText(DEFAULT_SCOPES))
  const [allowWrite, setAllowWrite] = useState(false)
  const [allowProd, setAllowProd] = useState(false)
  const [expiresDays, setExpiresDays] = useState(90)
  const [boundRooms, setBoundRooms] = useState<string[]>([])
  const [roomInput, setRoomInput] = useState('')
  const [approvers, setApprovers] = useState<string[]>([])
  const [approverInput, setApproverInput] = useState('')
  const [generating, setGenerating] = useState(false)
  const [revoking, setRevoking] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [editing, setEditing] = useState<TokenInfo | null>(null)
  const [saving, setSaving] = useState(false)
  const [editName, setEditName] = useState('')
  const [editDescription, setEditDescription] = useState('')
  const [editScopesText, setEditScopesText] = useState('')
  const [editAllowWrite, setEditAllowWrite] = useState(false)
  const [editAllowProd, setEditAllowProd] = useState(false)
  const [editExpiresDays, setEditExpiresDays] = useState(90)
  const [editBoundRooms, setEditBoundRooms] = useState<string[]>([])
  const [editRoomInput, setEditRoomInput] = useState('')
  const [editApprovers, setEditApprovers] = useState<string[]>([])
  const [editApproverInput, setEditApproverInput] = useState('')
  const [previewing, setPreviewing] = useState(false)
  const [policyPreview, setPolicyPreview] = useState<PolicyPreviewResult | null>(null)
  const [showCreateModal, setShowCreateModal] = useState(false)
  const activeCount = useMemo(() => tokens.filter((x) => x.status === 'active').length, [tokens])

  // Chip helpers (rooms / approvers)
  const pushChip = (
    setter: (v: (prev: string[]) => string[]) => void,
    raw: string,
    clear: () => void,
  ) => {
    const v = raw.trim()
    if (!v) return
    setter((prev) => (prev.includes(v) ? prev : [...prev, v]))
    clear()
  }
  const popChip = (
    idx: number,
    setter: (v: (prev: string[]) => string[]) => void,
  ) => {
    setter((prev) => prev.filter((_, i) => i !== idx))
  }
  const onChipKey = (
    e: React.KeyboardEvent<HTMLInputElement>,
    setter: (v: (prev: string[]) => string[]) => void,
    raw: string,
    clear: () => void,
  ) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      pushChip(setter, raw, clear)
    }
  }

  const renderRoomChips = (
    rooms: string[],
    setter: (v: (prev: string[]) => string[]) => void,
    input: string,
    setInput: (v: string) => void,
    readOnly = false,
    placeholder = '如：!opsRoom:matrix.org',
  ) => (
    <div>
      {!readOnly && (
        <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => onChipKey(e, setter, input, () => setInput(''))}
            placeholder={placeholder}
            style={{ flex: 1 }}
          />
          <button type="button" className="btn" onClick={() => pushChip(setter, input, () => setInput(''))}>
            +
          </button>
        </div>
      )}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {rooms.map((r, i) => (
          <span
            key={`${r}-${i}`}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 4,
              padding: '2px 8px', borderRadius: 12,
              background: 'var(--bg-hover)', fontSize: 12,
              fontFamily: 'monospace',
            }}
            title={r}
          >
            {r}
            {!readOnly && (
              <button
                type="button"
                onClick={() => popChip(i, setter)}
                style={{
                  background: 'none', border: 'none', cursor: 'pointer',
                  color: 'var(--text-muted)', padding: 0, fontSize: 14,
                }}
              >×</button>
            )}
          </span>
        ))}
        {rooms.length === 0 && (
          <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
            {readOnly ? '未设置' : '暂未添加'}
          </span>
        )}
      </div>
    </div>
  )

  const applyTemplate = (tpl: TokenTemplate) => {
    setScopesText(scopesToText(tpl.scopes))
    setAllowWrite(tpl.allow_write)
    setAllowProd(tpl.allow_prod)
    if (typeof tpl.expires_in_days === 'number') setExpiresDays(tpl.expires_in_days)
  }

  const applyEditTemplate = (tpl: TokenTemplate) => {
    setEditScopesText(scopesToText(tpl.scopes))
    setEditAllowWrite(tpl.allow_write)
    setEditAllowProd(tpl.allow_prod)
    if (typeof tpl.expires_in_days === 'number') setEditExpiresDays(tpl.expires_in_days)
  }

  const openEdit = (token: TokenInfo) => {
    setEditing(token)
    setEditName(token.name || '')
    setEditDescription(token.description || '')
    setEditScopesText(scopesToText(token.scopes || DEFAULT_SCOPES))
    setEditAllowWrite(Boolean(token.allow_write))
    setEditAllowProd(Boolean(token.allow_prod))
    setEditExpiresDays(90)
    setEditBoundRooms(Array.isArray(token.bound_room_ids) ? [...token.bound_room_ids] : [])
    setEditApprovers(Array.isArray(token.approver_matrix_ids) ? [...token.approver_matrix_ids] : [])
    setEditApproverInput('')
  }

  const openCreate = () => {
    // 重置新建表单为默认值
    setName('')
    setDescription('')
    setScopesText(scopesToText(DEFAULT_SCOPES))
    setAllowWrite(false)
    setAllowProd(false)
    setExpiresDays(90)
    setBoundRooms([])
    setRoomInput('')
    setApprovers([])
    setApproverInput('')
    setPolicyPreview(null)
    setShowCreateModal(true)
  }

  const handleGenerate = async () => {
    if (!name.trim()) return
    setGenerating(true)
    try {
      await onGenerate({
        name: name.trim(),
        description: description.trim() || undefined,
        scopes: textToScopes(scopesText),
        allow_write: allowWrite,
        allow_prod: allowProd,
        expires_in_days: expiresDays,
        bound_room_ids: boundRooms,
        approver_matrix_ids: approvers,
      })
      setName('')
      setDescription('')
      setBoundRooms([])
      setRoomInput('')
      setApprovers([])
      setApproverInput('')
      setShowCreateModal(false)
    } finally {
      setGenerating(false)
    }
  }

  const handleUpdate = async () => {
    if (!editing?.id || !editName.trim()) return
    setSaving(true)
    try {
      await onUpdate(editing.id, {
        name: editName.trim(),
        description: editDescription.trim(),
        scopes: textToScopes(editScopesText),
        allow_write: editAllowWrite,
        allow_prod: editAllowProd,
        expires_in_days: editExpiresDays,
        bound_room_ids: editBoundRooms,
        approver_matrix_ids: editApprovers,
      })
      setEditing(null)
    } finally {
      setSaving(false)
    }
  }

  const handleRevoke = async (tokenId: string) => {
    setRevoking(tokenId)
    try {
      await onRevoke(tokenId)
    } finally {
      setRevoking(null)
    }
  }

  const handleDelete = async (tokenId: string) => {
    setDeleting(tokenId)
    try {
      await onDelete(tokenId)
    } finally {
      setDeleting(null)
    }
  }

  const previewCurrentPolicy = async () => {
    setPreviewing(true)
    try {
      const result = await onPreviewPolicy({
        tool: 'ops.inspection.run_server',
        arguments: { server_id: 'preview' },
        scopes: textToScopes(scopesText),
        allow_write: allowWrite,
        allow_prod: allowProd,
      })
      setPolicyPreview(result)
    } finally {
      setPreviewing(false)
    }
  }

  return (
    <div className="card" style={{ display: 'grid', gap: 14 }}>
      <div className="card-header">
        <div>
          <h2>Tool Token</h2>
          <span>{tokens.length} 个令牌，{activeCount} 个可用</span>
        </div>
        <button className="btn btn-primary" onClick={openCreate}>+ 新建 Token</button>
      </div>

      <div className="table-scroll">
        <table className="data-table data-table--compact">
          <thead>
            <tr>
              <th>名称</th>
              <th>状态</th>
              <th>权限</th>
              <th>创建时间</th>
              <th>最近使用</th>
              <th>过期时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {loading && tokens.length === 0 && <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>加载中...</td></tr>}
            {!loading && tokens.length === 0 && <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>暂无 Token，点击右上角「新建 Token」创建</td></tr>}
            {tokens.map((token) => (
              <tr key={token.id || token.name}>
                <td>
                  <strong>{token.name || '-'}</strong>
                  {token.description && <small style={{ display: 'block', color: 'var(--text-muted)' }}>{token.description}</small>}
                  {token.value && <div style={{ marginTop: 4 }}><code style={{ fontSize: 12, wordBreak: 'break-all' }}>{token.value}</code><CopyButton text={token.value} /></div>}
                  {token.masked_value && !token.value && <div style={{ marginTop: 4 }}><code style={{ fontSize: 12 }}>{token.masked_value}</code></div>}
                </td>
                <td>
                  <span className={`tag ${token.status === 'active' ? 'tag-success' : token.status === 'expired' ? 'tag-warning' : 'tag-danger'}`}>{token.status || '-'}</span>
                </td>
                <td>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 4 }}>
                    {token.allow_write && <span className="tag tag-warning">允许写</span>}
                    {token.allow_prod && <span className="tag tag-danger">生产操作</span>}
                    {(token.bound_room_ids || []).length > 0 && (
                      <span
                        className="tag"
                        title={(token.bound_room_ids || []).join('\n')}
                        style={{ background: 'var(--bg-hover)', fontFamily: 'monospace' }}
                      >
                        房间: {(token.bound_room_ids || []).length}
                      </span>
                    )}
                    {(token.approver_matrix_ids || []).length > 0 && (
                      <span
                        className="tag"
                        title={(token.approver_matrix_ids || []).join('\n')}
                        style={{ background: 'var(--bg-hover)', fontFamily: 'monospace' }}
                      >
                        授权人: {(token.approver_matrix_ids || []).length}
                      </span>
                    )}
                    {(token.scopes || []).slice(0, 6).map((s) => <span className="scope-chip" key={s} title={s}>{formatScopeLabel(s)}</span>)}
                    {(token.scopes || []).length > 6 && <span style={{ color: 'var(--text-muted)' }}>+{(token.scopes || []).length - 6}</span>}
                  </div>
                </td>
                <td>{formatTime(token.created_at)}</td>
                <td>{formatTime(token.last_used_at)}</td>
                <td>{formatTime(token.expires_at)}</td>
                <td>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    <button className="btn btn-subtle" onClick={() => openEdit(token)}>编辑</button>
                    {token.status === 'active' && (
                      <button className="btn btn-subtle" disabled={revoking === token.id} onClick={() => handleRevoke(token.id || token.name || '')}>
                        {revoking === token.id ? '吊销中...' : '吊销'}
                      </button>
                    )}
                    {canDelete && (
                      <button
                        className="btn btn-subtle btn-danger"
                        disabled={deleting === token.id}
                        onClick={() => handleDelete(token.id || token.name || '')}
                        title="永久删除该 Token 记录，此操作不可撤销。"
                      >
                        {deleting === token.id ? '删除中...' : '删除'}
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 新建 Token 弹窗 */}
      {showCreateModal && (
        <div className="modal-backdrop" style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,.35)', zIndex: 1000, display: 'grid', placeItems: 'center', padding: 24 }}>
          <div className="card" style={{ width: 'min(920px, 96vw)', maxHeight: '88vh', overflow: 'auto', display: 'grid', gap: 14 }}>
            <div className="card-header">
              <div>
                <h2>新建 Tool Token</h2>
                <span>创建后 Token 字符串仅展示一次，请妥善保存。</span>
              </div>
              <button className="btn btn-subtle" onClick={() => setShowCreateModal(false)}>关闭</button>
            </div>

            <div className="alert alert-info">
              <strong>推荐模板：</strong>
              <span style={{ marginLeft: 8 }}>
                日常巡检优先使用 Inspection AI。执行类工具仍需精确 <code>confirm_text</code>；只读令牌不能创建巡检任务。
              </span>
            </div>

            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {templates.map((tpl) => (
                <button key={tpl.key} className="btn btn-subtle" type="button" title={templateTitle(tpl)} onClick={() => applyTemplate(tpl)}>
                  使用 {tpl.name}
                </button>
              ))}
            </div>

            <div className="form-grid form-grid--compact">
              <div className="field-item">
                <label>Token 名称</label>
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder="输入令牌名称..." />
              </div>
              <div className="field-item">
                <label>用途说明</label>
                <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="可选，说明令牌用途..." />
              </div>
              <div className="field-item">
                <label>有效天数</label>
                <input type="number" min={0} max={3650} value={expiresDays} onChange={(e) => setExpiresDays(Number(e.target.value || 0))} />
              </div>
            </div>

            <div className="field-item">
              <label>权限范围</label>
              <textarea rows={5} value={scopesText} onChange={(e) => setScopesText(e.target.value)} placeholder="ops:read&#10;ops:write&#10;server:read" />
              <div className="scope-chip-row">
                {textToScopes(scopesText).map((scope) => <span className="scope-chip" key={scope} title={scope}>{formatScopeLabel(scope)}</span>)}
              </div>
              {hasDangerousScope(scopesText) && (
                <div style={{ color: 'var(--text-warning)', fontSize: 13, marginTop: 4 }}>
                  检测到高危权限，创建或修改该令牌需要管理员权限。
                </div>
              )}
            </div>

            <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
              <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                <input type="checkbox" checked={allowWrite} onChange={(e) => setAllowWrite(e.target.checked)} />
                允许写操作工具
              </label>
              <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                <input type="checkbox" checked={allowProd} onChange={(e) => setAllowProd(e.target.checked)} />
                允许生产操作
              </label>
            </div>

            <div className="field-item">
              <label>绑定 Element 房间 ID（qclaw 限定）</label>
              <div style={{ color: 'var(--text-muted)', fontSize: 12, marginBottom: 6 }}>
                留空表示不限制；填写后，此 Token 仅能在指定 Element 房间被 qclaw 用于路由/审批调用。
                推荐每个 qclaw Token 只绑定一个房间。
              </div>
              {renderRoomChips(boundRooms, setBoundRooms, roomInput, setRoomInput)}
            </div>

            <div className="field-item">
              <label>绑定授权人（Matrix 用户 ID，qclaw 限定）</label>
              <div style={{ color: 'var(--text-muted)', fontSize: 12, marginBottom: 6 }}>
                留空表示不限制审批人（回退到系统/服务级 message_routing.approvers；两者均未配置时同房间任意成员可审批）。
                填写后，只有列表中的 Matrix 用户能消费此 Token 发起的审批短码。
              </div>
              {renderRoomChips(approvers, setApprovers, approverInput, setApproverInput, false, '如：@jack.han:hubtel.xyz')}
            </div>

            <div className="alert alert-info">
              <strong>权限预览</strong>
              <span style={{ marginLeft: 8 }}>检查当前草稿是否可调用 <code>ops.inspection.run_server</code>。</span>
              <button className="btn btn-subtle" style={{ marginLeft: 8 }} type="button" disabled={previewing} onClick={previewCurrentPolicy}>
                {previewing ? '检查中...' : '预览'}
              </button>
              {policyPreview && (
                <div style={{ marginTop: 8 }}>
                  <span className={`tag ${policyPreview.allowed ? 'tag-success' : 'tag-warning'}`}>{policyPreview.allowed ? '允许' : '拦截'}</span>
                  <span style={{ marginLeft: 8, color: 'var(--text-muted)' }}>{policyPreview.blocked_reason || `${policyPreview.risk || '-'} / ${policyPreview.category || '-'}`}</span>
                </div>
              )}
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button className="btn btn-subtle" onClick={() => setShowCreateModal(false)}>取消</button>
              <button className="btn btn-primary" disabled={!name.trim() || generating} onClick={handleGenerate}>
                {generating ? '生成中...' : '生成 Token'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 编辑 Token 弹窗 */}
      {editing && (
        <div className="modal-backdrop" style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,.35)', zIndex: 1000, display: 'grid', placeItems: 'center', padding: 24 }}>
          <div className="card" style={{ width: 'min(920px, 96vw)', maxHeight: '88vh', overflow: 'auto', display: 'grid', gap: 14 }}>
            <div className="card-header">
              <div>
                <h2>编辑 Tool Token</h2>
                <span>权限变更会在下一次 MCP/API 调用生效，Token 字符串本身不会变化。</span>
              </div>
              <button className="btn btn-subtle" onClick={() => setEditing(null)}>关闭</button>
            </div>
            <div className="form-grid form-grid--compact">
              <div className="field-item">
                <label>Token 名称</label>
                <input value={editName} onChange={(e) => setEditName(e.target.value)} />
              </div>
              <div className="field-item">
                <label>用途说明</label>
                <input value={editDescription} onChange={(e) => setEditDescription(e.target.value)} placeholder="可选，说明令牌用途..." />
              </div>
              <div className="field-item">
                <label>延长有效天数</label>
                <input type="number" min={0} max={3650} value={editExpiresDays} onChange={(e) => setEditExpiresDays(Number(e.target.value || 0))} />
              </div>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {templates.map((tpl) => (
                <button key={tpl.key} className="btn btn-subtle" type="button" title={templateTitle(tpl)} onClick={() => applyEditTemplate(tpl)}>
                  应用 {tpl.name}
                </button>
              ))}
            </div>
            <div className="field-item">
              <label>权限范围</label>
              <textarea rows={9} value={editScopesText} onChange={(e) => setEditScopesText(e.target.value)} />
              <div className="scope-chip-row">
                {textToScopes(editScopesText).map((scope) => <span className="scope-chip" key={scope} title={scope}>{formatScopeLabel(scope)}</span>)}
              </div>
              {hasDangerousScope(editScopesText) && (
                <div style={{ color: 'var(--text-warning)', fontSize: 13, marginTop: 4 }}>
                  检测到高危权限，创建或修改该令牌需要管理员权限。
                </div>
              )}
            </div>
            <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
              <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                <input type="checkbox" checked={editAllowWrite} onChange={(e) => setEditAllowWrite(e.target.checked)} />
                允许写操作工具
              </label>
              <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                <input type="checkbox" checked={editAllowProd} onChange={(e) => setEditAllowProd(e.target.checked)} />
                允许生产操作
              </label>
            </div>
            <div className="field-item">
              <label>绑定 Element 房间 ID（qclaw 限定）</label>
              <div style={{ color: 'var(--text-muted)', fontSize: 12, marginBottom: 6 }}>
                修改后将立即在下次 MCP 调用生效。留空表示不限制房间。
              </div>
              {renderRoomChips(editBoundRooms, setEditBoundRooms, editRoomInput, setEditRoomInput)}
            </div>
            <div className="field-item">
              <label>绑定授权人（Matrix 用户 ID，qclaw 限定）</label>
              <div style={{ color: 'var(--text-muted)', fontSize: 12, marginBottom: 6 }}>
                留空表示不限制审批人（回退到系统/服务级配置）；填写后只有列表中的用户能审批此 Token 发起的操作。
              </div>
              {renderRoomChips(editApprovers, setEditApprovers, editApproverInput, setEditApproverInput, false, '如：@jack.han:hubtel.xyz')}
            </div>
            <div className="alert alert-warning">
              巡检执行需要 <code>ops:write</code> 与写操作开关；启用服务器读取管控时，服务器只读工具需要 <code>server:read</code>。
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button className="btn btn-subtle" onClick={() => setEditing(null)}>取消</button>
              <button className="btn btn-primary" disabled={!editName.trim() || saving} onClick={handleUpdate}>{saving ? '保存中...' : '保存变更'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
