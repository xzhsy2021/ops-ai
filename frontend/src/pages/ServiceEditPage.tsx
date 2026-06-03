import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { resource, serverManagement } from '../api'
import { useNotificationStore } from '../store'
import KeyValueEditor from '../components/KeyValueEditor'

const TEMPLATE_LABELS: Record<string, string> = {
  generic_backend_direct: '后端 Direct',
  generic_frontend: '前端',
  dovo_bluegreen_update: 'Dovo 蓝绿',
}

export default function ServiceEditPage() {
  const { systemName, serviceName } = useParams<{ systemName: string; serviceName: string }>()
  const navigate = useNavigate()
  const notify = useNotificationStore((state) => state.addMessage)
  const isCreate = !serviceName

  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [allServers, setAllServers] = useState<string[]>([])
  const [serverSearch, setServerSearch] = useState('')
  const [debouncedServerSearch, setDebouncedServerSearch] = useState('')
  const serverSearchTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const [form, setForm] = useState({
    name: '',
    display_name: '',
    template: 'generic_backend_direct',
    repo: '',
    servers: [] as string[],
    template_variables: {} as Record<string, any>,
  })

  useEffect(() => {
    if (serverSearchTimer.current) clearTimeout(serverSearchTimer.current)
    serverSearchTimer.current = setTimeout(() => {
      setDebouncedServerSearch(serverSearch.trim().toLowerCase())
    }, 300)
    return () => {
      if (serverSearchTimer.current) clearTimeout(serverSearchTimer.current)
    }
  }, [serverSearch])

  useEffect(() => {
    serverManagement.list(false).then((res: any) => {
      const list = (res.data || []).map((s: any) => s.name).filter(Boolean).sort()
      setAllServers(list)
    }).catch(() => {})
  }, [])

  useEffect(() => {
    if (!systemName || !serviceName) return
    setLoading(true)
    resource.systems.get(systemName).then((res: any) => {
      const system = res.data || {}
      const svc = (system.services || []).find((s: any) => s.name === serviceName)
      if (svc) {
        setForm({
          name: svc.name || '',
          display_name: svc.display_name || '',
          template: svc.template || 'generic_backend_direct',
          repo: svc.repo || '',
          servers: svc.servers || [],
          template_variables: svc.template_variables || {},
        })
      } else {
        notify('服务不存在', 'error')
      }
    }).catch(() => {
      notify('无法加载服务配置', 'error')
    }).finally(() => {
      setLoading(false)
    })
  }, [systemName, serviceName])

  const handleSubmit = async () => {
    if (!systemName) return
    const name = form.name.trim()
    if (!name) return notify('请输入服务名', 'error')
    const payload = {
      name,
      display_name: form.display_name.trim() || name,
      template: form.template || 'generic_backend_direct',
      repo: form.repo.trim(),
      servers: form.servers || [],
      template_variables: form.template_variables,
    }
    setSaving(true)
    try {
      if (serviceName) {
        await resource.systemServices.update(systemName, serviceName, payload)
        notify('服务已更新', 'success')
      } else {
        await resource.systemServices.create(systemName, payload)
        notify('服务已创建', 'success')
      }
      navigate(`/systems`)
    } catch (e: any) {
      const msg = typeof e === 'string' ? e : (e?.message || '操作失败')
      notify(msg, 'error')
    } finally {
      setSaving(false)
    }
  }

  const toggleServer = (srv: string) => {
    const current = form.servers || []
    const next = current.includes(srv)
      ? current.filter((s: string) => s !== srv)
      : [...current, srv]
    setForm({ ...form, servers: next })
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '8px 12px',
    background: 'var(--bg-surface)',
    border: '1px solid var(--border-strong)',
    borderRadius: '6px', color: 'var(--text-primary)',
    fontSize: '14px',
  }

  const filteredServers = debouncedServerSearch
    ? allServers.filter((s: string) => s.toLowerCase().includes(debouncedServerSearch))
    : allServers

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
          {isCreate
            ? `新建服务 (${systemName})`
            : `编辑服务: ${serviceName} (${systemName})`}
        </h2>
        <button className="btn" onClick={() => navigate('/systems')}>
          返回系统列表
        </button>
      </div>

      <div className="card">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px' }}>
          <div>
            <label>服务名 *</label>
            <input
              style={inputStyle}
              value={form.name}
              disabled={!!serviceName}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="crypto-frontend"
            />
            {!!serviceName && (
              <div style={{ color: 'var(--text-muted)', fontSize: '12px', marginTop: '4px' }}>
                服务名创建后不可修改
              </div>
            )}
          </div>
          <div>
            <label>显示名称</label>
            <input
              style={inputStyle}
              value={form.display_name}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              placeholder="前端"
            />
          </div>
          <div>
            <label>模板</label>
            <select
              style={inputStyle}
              value={form.template}
              onChange={(e) => setForm({ ...form, template: e.target.value })}
            >
              {Object.entries(TEMPLATE_LABELS).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </div>
          <div>
            <label>代码仓库</label>
            <input
              style={inputStyle}
              value={form.repo}
              onChange={(e) => setForm({ ...form, repo: e.target.value })}
              placeholder="git@github.com:org/repo.git"
            />
          </div>
          <div style={{ gridColumn: '1 / -1' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
              <label style={{ margin: 0 }}>默认服务器 (从资产中选择)</label>
              <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                共 {allServers.length} 台，已选 {(form.servers || []).length} 台
              </span>
            </div>
            <input
              style={{ ...inputStyle, marginBottom: '6px' }}
              placeholder="搜索服务器..."
              value={serverSearch}
              onChange={(e) => setServerSearch(e.target.value)}
            />
            <div style={{
              maxHeight: '200px', overflow: 'auto',
              background: 'var(--bg-surface)', border: '1px solid var(--border-strong)',
              borderRadius: '6px',
            }}>
              {filteredServers.length > 0 ? (
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border-strong)', background: 'var(--bg-base)' }}>
                      <th style={{ padding: '6px 12px', width: '40px', textAlign: 'center' }}></th>
                      <th style={{ padding: '6px 12px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: '600' }}>服务器名</th>
                      <th style={{ padding: '6px 12px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: '600' }}>状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredServers.map((srv: string) => {
                      const selected = (form.servers || []).includes(srv)
                      return (
                        <tr key={srv}
                          onClick={() => toggleServer(srv)}
                          style={{
                            borderBottom: '1px solid var(--border-strong)',
                            cursor: 'pointer',
                            background: selected ? 'var(--action-bg)' : 'transparent',
                            transition: 'background 0.15s',
                          }}
                          onMouseEnter={(e) => {
                            if (!selected) (e.currentTarget as HTMLElement).style.background = 'var(--bg-hover, rgba(0,0,0,0.03))'
                          }}
                          onMouseLeave={(e) => {
                            if (!selected) (e.currentTarget as HTMLElement).style.background = 'transparent'
                          }}
                        >
                          <td style={{ padding: '6px 12px', textAlign: 'center' }}>
                            <input type="checkbox" checked={selected} readOnly style={{ pointerEvents: 'none' }} />
                          </td>
                          <td style={{
                            padding: '6px 12px',
                            fontWeight: selected ? 600 : 400,
                            color: selected ? 'var(--brand)' : 'var(--text-primary)',
                          }}>
                            {srv}
                          </td>
                          <td style={{ padding: '6px 12px' }}>
                            {selected ? (
                              <span style={{
                                fontSize: '11px', padding: '2px 8px', borderRadius: '4px',
                                background: 'var(--brand)', color: '#fff',
                              }}>已选</span>
                            ) : (
                              <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>—</span>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              ) : (
                <div style={{ padding: '16px', color: 'var(--text-muted)', fontSize: '13px', textAlign: 'center' }}>
                  {allServers.length === 0 ? '暂无服务器资产，请先在"服务器"页面添加' : `未找到匹配 "${serverSearch}" 的服务器`}
                </div>
              )}
            </div>
            {(form.servers || []).length > 0 && (
              <div style={{ marginTop: '6px', fontSize: '12px', color: 'var(--text-muted)' }}>
                已选: {(form.servers || []).join(', ')}
              </div>
            )}
          </div>
          <div style={{ gridColumn: '1 / -1' }}>
            <label style={{ display: 'block', marginBottom: '8px' }}>模板变量</label>
            <KeyValueEditor
              value={form.template_variables}
              onChange={(v) => setForm({ ...form, template_variables: v })}
              keyPlaceholder="变量名"
              valuePlaceholder="变量值"
              addButtonText="+ 添加变量"
              emptyText="暂无模板变量"
            />
          </div>
        </div>
        <div style={{ display: 'flex', gap: '12px', marginTop: '20px' }}>
          <button className="btn btn-primary" onClick={handleSubmit} disabled={saving}>
            {saving ? '保存中...' : (isCreate ? '创建服务' : '保存修改')}
          </button>
          <button className="btn" onClick={() => navigate('/systems')}>
            取消
          </button>
        </div>
      </div>
    </div>
  )
}
