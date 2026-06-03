import { useState } from 'react'
import { CopyButton } from '../../components/ui'

type TokenInfo = {
  id?: string
  name?: string
  description?: string
  status?: string
  created_at?: string
  last_used_at?: string
  expires_at?: string
  scopes?: string[]
  key_prefix?: string
  value?: string
  masked_value?: string
}

export function ToolTokenPanel({
  tokens,
  loading,
  onGenerate,
  onRevoke,
}: {
  tokens: TokenInfo[]
  loading?: boolean
  onGenerate: (data: { name: string; description?: string; scopes?: string[] }) => Promise<void>
  onRevoke: (tokenId: string) => Promise<void>
}) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [generating, setGenerating] = useState(false)
  const [revoking, setRevoking] = useState<string | null>(null)

  const handleGenerate = async () => {
    if (!name.trim()) return
    setGenerating(true)
    try {
      await onGenerate({ name: name.trim(), description: description.trim() || undefined })
      setName('')
      setDescription('')
    } finally {
      setGenerating(false)
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
    <div className="card" style={{ display: 'grid', gap: 12 }}>
      <div className="card-header">
        <div>
          <h2>访问令牌</h2>
          <span>{tokens.length} 个令牌</span>
        </div>
      </div>
      <div className="form-compact">
        <div className="form-grid form-grid--compact">
          <div className="field-item">
            <label>令牌名称</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="输入令牌名称..."
            />
          </div>
          <div className="field-item">
            <label>描述（可选）</label>
            <input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="令牌用途描述..."
            />
          </div>
        </div>
        <button
          className="btn btn-primary"
          disabled={!name.trim() || generating}
          onClick={handleGenerate}
        >
          {generating ? '生成中...' : '生成新令牌'}
        </button>
      </div>
      <div className="table-scroll">
        <table className="data-table data-table--compact">
          <thead>
            <tr>
              <th>名称</th>
              <th>状态</th>
              <th>创建时间</th>
              <th>最近使用</th>
              <th>过期时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {loading && tokens.length === 0 && (
              <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>加载中...</td></tr>
            )}
            {!loading && tokens.length === 0 && (
              <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>暂无令牌</td></tr>
            )}
            {tokens.map((token) => (
              <tr key={token.id || token.name}>
                <td>
                  <strong>{token.name || '-'}</strong>
                  {token.description && <small style={{ display: 'block', color: 'var(--text-muted)' }}>{token.description}</small>}
                  {token.value && (
                    <div style={{ marginTop: 4 }}>
                      <code style={{ fontSize: 12, wordBreak: 'break-all' }}>{token.value}</code>
                      <CopyButton text={token.value} />
                    </div>
                  )}
                  {token.masked_value && !token.value && (
                    <div style={{ marginTop: 4 }}>
                      <code style={{ fontSize: 12 }}>{token.masked_value}</code>
                    </div>
                  )}
                </td>
                <td>
                  <span className={`tag ${token.status === 'active' ? 'tag-success' : token.status === 'expired' ? 'tag-warning' : 'tag-danger'}`}>
                    {token.status || '-'}
                  </span>
                </td>
                <td>{token.created_at ? new Date(token.created_at).toLocaleString() : '-'}</td>
                <td>{token.last_used_at ? new Date(token.last_used_at).toLocaleString() : '-'}</td>
                <td>{token.expires_at ? new Date(token.expires_at).toLocaleString() : '-'}</td>
                <td>
                  <button
                    className="btn btn-subtle btn-danger"
                    disabled={revoking === token.id}
                    onClick={() => handleRevoke(token.id || token.name || '')}
                  >
                    {revoking === token.id ? '撤销中...' : '撤销'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}