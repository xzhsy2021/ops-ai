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
}

type TokenUpdatePayload = {
  name?: string
  description?: string
  scopes?: string[]
  allow_write?: boolean
  allow_prod?: boolean
  expires_in_days?: number
  revoke?: boolean
}

type PolicyPreviewResult = {
  tool?: string
  allowed?: boolean
  blocked_reason?: string
  risk?: string
  category?: string
  required_scopes?: string[]
}

export type TokenTemplate = {
  key: string
  name: string
  description?: string
  notes?: string
  scopes: string[]
  allow_write: boolean
  allow_prod: boolean
  expires_in_days?: number
}

const FALLBACK_TOKEN_TEMPLATES: TokenTemplate[] = [
  {
    key: 'readonly-ai',
    name: 'Readonly AI',
    description: 'Read-only diagnostics, server inventory, reports, audit and risk context.',
    notes: 'Path A inspection execution is blocked.',
    allow_write: false,
    allow_prod: false,
    expires_in_days: 90,
    scopes: ['ops:read', 'audit:read', 'server:read'],
  },
  {
    key: 'inspection-ai',
    name: 'Inspection AI',
    description: 'Routine MCP inspection assistant with Path A run tools and read evidence tools.',
    notes: 'Path A run tools require exact confirm_text.',
    allow_write: true,
    allow_prod: false,
    expires_in_days: 90,
    scopes: ['ops:read', 'ops:write', 'server:read', 'audit:read'],
  },
  {
    key: 'operator-human',
    name: 'Operator Human',
    description: 'Human-operated token for planning, precheck and queued operational actions.',
    notes: 'Does not include deploy:execute.',
    allow_write: true,
    allow_prod: false,
    expires_in_days: 30,
    scopes: ['ops:read', 'ops:write', 'server:read', 'audit:read', 'deploy:plan', 'deploy:precheck'],
  },
  {
    key: 'admin-breakglass',
    name: 'Admin Breakglass',
    description: 'Short-lived emergency token for admin-only operations.',
    notes: 'Use only for emergency windows; revoke immediately after use.',
    allow_write: true,
    allow_prod: true,
    expires_in_days: 7,
    scopes: ['*'],
  },
]

const DANGEROUS_SCOPES = ['deploy:execute', 'config:write', 'server:write', 'package:write', 'package:cleanup', 'db:write', '*']
const DEFAULT_SCOPES = FALLBACK_TOKEN_TEMPLATES[0].scopes
const SCOPE_LABELS: Record<string, string> = {
  '*': '全部权限',
  'ops:read': '运维读取',
  'ops:write': '运维写入',
  'audit:read': '审计读取',
  'server:read': '服务器读取',
  'server:write': '服务器写入',
  'deploy:plan': '发布计划',
  'deploy:precheck': '发布预检',
  'deploy:execute': '发布执行',
  'config:write': '配置写入',
  'package:write': '包写入',
  'package:cleanup': '包清理',
  'db:write': '数据库写入',
}

function scopesToText(scopes?: string[]) {
  return (scopes || []).join('\n')
}

function textToScopes(text: string) {
  return text
    .split(/[\n,\s]+/)
    .map((x) => x.trim())
    .filter(Boolean)
}

function formatTime(value?: string) {
  return value ? new Date(value).toLocaleString() : '-'
}

function hasDangerousScope(text: string) {
  return textToScopes(text).some((scope) => DANGEROUS_SCOPES.includes(scope.trim()))
}

function formatScopeLabel(scope: string) {
  return SCOPE_LABELS[scope] || scope
}

function templateTitle(tpl: TokenTemplate) {
  const labels = (tpl.scopes || []).map(formatScopeLabel).join('、')
  return [tpl.description, tpl.notes, labels ? `权限：${labels}` : ''].filter(Boolean).join(' ')
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
  const [previewing, setPreviewing] = useState(false)
  const [policyPreview, setPolicyPreview] = useState<PolicyPreviewResult | null>(null)
  const activeCount = useMemo(() => tokens.filter((x) => x.status === 'active').length, [tokens])

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
      })
      setName('')
      setDescription('')
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
          <span>{tokens.length} 个令牌，{activeCount} 个可用。全局开关决定能力可见性，Token 权限范围决定 MCP/AI 实际可调用能力。</span>
        </div>
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

      <div className="form-compact">
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
        <button className="btn btn-primary" disabled={!name.trim() || generating} onClick={handleGenerate}>
          {generating ? '生成中...' : '生成 Token'}
        </button>
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
            {!loading && tokens.length === 0 && <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>暂无 Token</td></tr>}
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
