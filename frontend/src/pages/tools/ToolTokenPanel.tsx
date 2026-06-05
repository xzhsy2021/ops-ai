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
  scopes?: string[]
  allow_write?: boolean
  allow_prod?: boolean
  expires_in_days?: number
  revoke?: boolean
}

const TOKEN_TEMPLATES = [
  {
    key: 'readonly',
    name: 'AI 只读分析',
    hint: '只允许查询状态、报告、风险和巡检结果。',
    allow_write: false,
    allow_prod: false,
    scopes: ['tool:read', 'server:read', 'service:read', 'system:read', 'inspection:read', 'report:read', 'risk:read', 'diagnostic:read'],
  },
  {
    key: 'inspection',
    name: 'AI 巡检执行',
    hint: '允许查询服务器并发起只读巡检、生成报告。',
    allow_write: true,
    allow_prod: false,
    scopes: ['tool:read', 'server:read', 'service:read', 'system:read', 'inspection:read', 'inspection:run', 'report:read', 'report:write', 'risk:read', 'diagnostic:read', 'job:read'],
  },
  {
    key: 'admin_read',
    name: '管理员只读审计',
    hint: '增加审计、任务和报告查询，不授予执行权限。',
    allow_write: false,
    allow_prod: false,
    scopes: ['tool:read', 'server:read', 'service:read', 'system:read', 'inspection:read', 'report:read', 'risk:read', 'diagnostic:read', 'job:read', 'audit:read'],
  },
]

const DANGEROUS_SCOPES = ['deploy:execute', 'config:write', 'server:write', 'package:write', 'package:cleanup', 'db:write', '*']

const DEFAULT_SCOPES = TOKEN_TEMPLATES[0].scopes

function scopesToText(scopes?: string[]) {
  return (scopes || []).join('\n')
}

function textToScopes(text: string) {
  return text
    .split(/[\n,，\s]+/)
    .map((x) => x.trim())
    .filter(Boolean)
}

function formatTime(value?: string) {
  return value ? new Date(value).toLocaleString() : '-'
}

export function ToolTokenPanel({
  tokens,
  loading,
  onGenerate,
  onUpdate,
  onRevoke,
}: {
  tokens: TokenInfo[]
  loading?: boolean
  onGenerate: (data: TokenPayload) => Promise<void>
  onUpdate: (tokenId: string, data: TokenUpdatePayload) => Promise<void>
  onRevoke: (tokenId: string) => Promise<void>
}) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [scopesText, setScopesText] = useState(scopesToText(DEFAULT_SCOPES))
  const [allowWrite, setAllowWrite] = useState(false)
  const [allowProd, setAllowProd] = useState(false)
  const [expiresDays, setExpiresDays] = useState(90)
  const [generating, setGenerating] = useState(false)
  const [revoking, setRevoking] = useState<string | null>(null)
  const [editing, setEditing] = useState<TokenInfo | null>(null)
  const [saving, setSaving] = useState(false)
  const [editName, setEditName] = useState('')
  const [editScopesText, setEditScopesText] = useState('')
  const [editAllowWrite, setEditAllowWrite] = useState(false)
  const [editAllowProd, setEditAllowProd] = useState(false)
  const [editExpiresDays, setEditExpiresDays] = useState(90)
  const activeCount = useMemo(() => tokens.filter((x) => x.status === 'active').length, [tokens])

  const applyTemplate = (tpl: typeof TOKEN_TEMPLATES[number]) => {
    setScopesText(scopesToText(tpl.scopes))
    setAllowWrite(tpl.allow_write)
    setAllowProd(tpl.allow_prod)
  }

  const openEdit = (token: TokenInfo) => {
    setEditing(token)
    setEditName(token.name || '')
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

  return (
    <div className="card" style={{ display: 'grid', gap: 14 }}>
      <div className="card-header">
        <div>
          <h2>访问令牌</h2>
          <span>{tokens.length} 个令牌，{activeCount} 个可用。能力开关只控制全局功能，Token scopes 才决定 MCP/AI 实际可调用范围。</span>
        </div>
      </div>

      <div className="alert alert-info">
        <strong>常用模板：</strong>
        <span style={{ marginLeft: 8 }}>巡检服务器需要至少包含 <code>server:read</code>、<code>inspection:read</code>、<code>inspection:run</code>、<code>report:read</code>、<code>report:write</code>。</span>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        {TOKEN_TEMPLATES.map((tpl) => (
          <button key={tpl.key} className="btn btn-subtle" type="button" title={tpl.hint} onClick={() => applyTemplate(tpl)}>
            使用模板：{tpl.name}
          </button>
        ))}
      </div>

      <div className="form-compact">
        <div className="form-grid form-grid--compact">
          <div className="field-item">
            <label>令牌名称</label>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="输入令牌名称..." />
          </div>
          <div className="field-item">
            <label>描述（可选）</label>
            <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="令牌用途描述..." />
          </div>
          <div className="field-item">
            <label>有效期（天，0 表示永不过期）</label>
            <input type="number" min={0} max={3650} value={expiresDays} onChange={(e) => setExpiresDays(Number(e.target.value || 0))} />
          </div>
        </div>
        <div className="field-item">
          <label>Scopes 权限，一行一个或用逗号分隔</label>
          <textarea rows={5} value={scopesText} onChange={(e) => setScopesText(e.target.value)} placeholder="server:read\ninspection:read\ninspection:run" />
          {textToScopes(scopesText).some(s => DANGEROUS_SCOPES.includes(s.trim())) && (
            <div style={{ color: 'var(--text-warning)', fontSize: 13, marginTop: 4 }}>
              包含危险 scope，需要管理员权限才能创建/修改
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
            <input type="checkbox" checked={allowWrite} onChange={(e) => setAllowWrite(e.target.checked)} />
            允许写/执行类工具，巡检执行和报告生成需要开启
          </label>
          <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
            <input type="checkbox" checked={allowProd} onChange={(e) => setAllowProd(e.target.checked)} />
            允许生产环境动作，谨慎开启
          </label>
        </div>
        <button className="btn btn-primary" disabled={!name.trim() || generating} onClick={handleGenerate}>
          {generating ? '生成中...' : '生成新令牌'}
        </button>
      </div>

      <div className="table-scroll">
        <table className="data-table data-table--compact">
          <thead>
            <tr>
              <th>名称</th>
              <th>状态</th>
              <th>权限摘要</th>
              <th>创建时间</th>
              <th>最近使用</th>
              <th>过期时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {loading && tokens.length === 0 && <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>加载中...</td></tr>}
            {!loading && tokens.length === 0 && <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>暂无令牌</td></tr>}
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
                    {token.allow_write && <span className="tag tag-warning">write</span>}
                    {token.allow_prod && <span className="tag tag-danger">prod</span>}
                    {(token.scopes || []).slice(0, 6).map((s) => <code key={s} style={{ fontSize: 11 }}>{s}</code>)}
                    {(token.scopes || []).length > 6 && <span style={{ color: 'var(--text-muted)' }}>+{(token.scopes || []).length - 6}</span>}
                  </div>
                </td>
                <td>{formatTime(token.created_at)}</td>
                <td>{formatTime(token.last_used_at)}</td>
                <td>{formatTime(token.expires_at)}</td>
                <td>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    <button className="btn btn-subtle" onClick={() => openEdit(token)}>编辑</button>
                    <button className="btn btn-subtle btn-danger" disabled={revoking === token.id} onClick={() => handleRevoke(token.id || token.name || '')}>
                      {revoking === token.id ? '撤销中...' : '撤销'}
                    </button>
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
                <span>修改后不改变 token 字符串本身，MCP 客户端无需替换令牌；重新连接或下次调用即可生效。</span>
              </div>
              <button className="btn btn-subtle" onClick={() => setEditing(null)}>关闭</button>
            </div>
            <div className="form-grid form-grid--compact">
              <div className="field-item">
                <label>令牌名称</label>
                <input value={editName} onChange={(e) => setEditName(e.target.value)} />
              </div>
              <div className="field-item">
                <label>延长有效期（天，0 表示永不过期）</label>
                <input type="number" min={0} max={3650} value={editExpiresDays} onChange={(e) => setEditExpiresDays(Number(e.target.value || 0))} />
              </div>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {TOKEN_TEMPLATES.map((tpl) => (
                <button key={tpl.key} className="btn btn-subtle" type="button" onClick={() => { setEditScopesText(scopesToText(tpl.scopes)); setEditAllowWrite(tpl.allow_write); setEditAllowProd(tpl.allow_prod) }}>
                  套用：{tpl.name}
                </button>
              ))}
            </div>
            <div className="field-item">
              <label>Scopes 权限</label>
              <textarea rows={9} value={editScopesText} onChange={(e) => setEditScopesText(e.target.value)} />
              {textToScopes(editScopesText).some(s => DANGEROUS_SCOPES.includes(s.trim())) && (
                <div style={{ color: 'var(--text-warning)', fontSize: 13, marginTop: 4 }}>
                  包含危险 scope，需要管理员权限才能创建/修改
                </div>
              )}
            </div>
            <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
              <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                <input type="checkbox" checked={editAllowWrite} onChange={(e) => setEditAllowWrite(e.target.checked)} />
                允许写/执行类工具
              </label>
              <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                <input type="checkbox" checked={editAllowProd} onChange={(e) => setEditAllowProd(e.target.checked)} />
                允许生产环境动作
              </label>
            </div>
            <div className="alert alert-warning">
              巡检执行需要 <code>inspection:run</code> 和 <code>allow_write=true</code>；读取服务器需要 <code>server:read</code>。
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button className="btn btn-subtle" onClick={() => setEditing(null)}>取消</button>
              <button className="btn btn-primary" disabled={!editName.trim() || saving} onClick={handleUpdate}>{saving ? '保存中...' : '保存修改'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
