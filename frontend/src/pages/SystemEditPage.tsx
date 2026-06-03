import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { resource } from '../api'
import { useNotificationStore } from '../store'
import KeyValueEditor from '../components/KeyValueEditor'

const STRATEGY_LABELS: Record<string, string> = {
  DIRECT: 'Direct 直推',
  DOVO: 'Dovo 蓝绿',
  WORKFLOW: 'Workflow',
}

export default function SystemEditPage() {
  const { name } = useParams<{ name: string }>()
  const navigate = useNavigate()
  const notify = useNotificationStore((state) => state.addMessage)
  const isCreate = !name

  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState({
    name: '',
    display_name: '',
    strategy: 'DIRECT',
    description: '',
    variables: {} as Record<string, any>,
    servers: '',
  })

  useEffect(() => {
    if (!name) return
    setLoading(true)
    resource.systems.get(name).then((res: any) => {
      const s = res.data || {}
      setForm({
        name: s.name || name,
        display_name: s.display_name || '',
        strategy: s.strategy || 'DIRECT',
        description: s.description || '',
        variables: s.variables || {},
        servers: (s.servers || []).join(', '),
      })
    }).catch(() => {
      notify('无法加载系统配置', 'error')
    }).finally(() => {
      setLoading(false)
    })
  }, [name])

  const handleSubmit = async () => {
    const sysName = form.name.trim()
    if (!sysName) return notify('请输入系统名', 'error')
    const servers = form.servers.split(',').map((s: string) => s.trim()).filter(Boolean)
    const payload = {
      name: sysName,
      display_name: form.display_name.trim() || sysName,
      strategy: form.strategy || 'DIRECT',
      description: form.description.trim(),
      variables: form.variables,
      servers,
    }
    setSaving(true)
    try {
      if (name) {
        await resource.systems.update(name, payload)
        notify('系统已更新', 'success')
      } else {
        await resource.systems.create(payload)
        notify('系统已创建', 'success')
      }
      navigate('/systems')
    } catch (e: any) {
      const msg = typeof e === 'string' ? e : (e?.message || '操作失败')
      notify(msg, 'error')
    } finally {
      setSaving(false)
    }
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '8px 12px',
    background: 'var(--bg-surface)',
    border: '1px solid var(--border-strong)',
    borderRadius: '6px', color: 'var(--text-primary)',
    fontSize: '14px',
  }

  if (loading) {
    return (
      <div className="card" style={{ textAlign: 'center', padding: '40px' }}>
        <div style={{ color: 'var(--text-muted)' }}>加载中...</div>
      </div>
    )
  }

  return (
    <div style={{ display: 'grid', gap: '20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ margin: 0 }}>
          {isCreate ? '新建系统' : `编辑系统: ${name}`}
        </h2>
        <button className="btn" onClick={() => navigate('/systems')}>
          返回系统列表
        </button>
      </div>

      <div className="card">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px' }}>
          <div>
            <label>系统名 *</label>
            <input
              style={inputStyle}
              value={form.name}
              disabled={!!name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="crypto-trader"
            />
            {!!name && (
              <div style={{ color: 'var(--text-muted)', fontSize: '12px', marginTop: '4px' }}>
                系统名创建后不可修改
              </div>
            )}
          </div>
          <div>
            <label>显示名称</label>
            <input
              style={inputStyle}
              value={form.display_name}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              placeholder="Crypto Trader 量化"
            />
          </div>
          <div>
            <label>发布策略</label>
            <select
              style={inputStyle}
              value={form.strategy}
              onChange={(e) => setForm({ ...form, strategy: e.target.value })}
            >
              {Object.entries(STRATEGY_LABELS).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </div>
          <div>
            <label>默认服务器 (逗号分隔)</label>
            <input
              style={inputStyle}
              value={form.servers}
              onChange={(e) => setForm({ ...form, servers: e.target.value })}
              placeholder="prod-1, prod-2"
            />
          </div>
          <div style={{ gridColumn: '1 / -1' }}>
            <label>描述</label>
            <input
              style={inputStyle}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="系统描述"
            />
          </div>
          <div style={{ gridColumn: '1 / -1' }}>
            <label style={{ display: 'block', marginBottom: '8px' }}>系统变量</label>
            <KeyValueEditor
              value={form.variables}
              onChange={(v) => setForm({ ...form, variables: v })}
              keyPlaceholder="变量名"
              valuePlaceholder="变量值"
              addButtonText="+ 添加变量"
              emptyText="暂无系统变量"
            />
          </div>
        </div>
        <div style={{ display: 'flex', gap: '12px', marginTop: '20px' }}>
          <button className="btn btn-primary" onClick={handleSubmit} disabled={saving}>
            {saving ? '保存中...' : (isCreate ? '创建系统' : '保存修改')}
          </button>
          <button className="btn" onClick={() => navigate('/systems')}>
            取消
          </button>
        </div>
      </div>
    </div>
  )
}
