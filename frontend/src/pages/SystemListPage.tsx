import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { resource, serverGroups, serverManagement } from '../api'
import { useAuthStore, useNotificationStore } from '../store'
import { ConfirmDialog } from '../components/ui'

interface SystemInfo {
  name: string
  display_name: string
  strategy: string
  description: string
  environment_count: number
  service_count: number
  services: any[]
  environments: Record<string, any>
  groups: Record<string, any>
  variables: Record<string, any>
  servers: string[]
}

const STRATEGY_LABELS: Record<string, string> = {
  DIRECT: 'Direct 直推',
  DOVO: 'Dovo 蓝绿',
  WORKFLOW: 'Workflow',
}

const TEMPLATE_LABELS: Record<string, string> = {
  generic_backend_direct: '后端 Direct',
  generic_frontend: '前端',
  dovo_bluegreen_update: 'Dovo 蓝绿',
}

export default function SystemListPage() {
  const navigate = useNavigate()
  const { user } = useAuthStore()
  const notify = useNotificationStore((state) => state.addMessage)
  const isAdmin = user?.is_admin

  const [systems, setSystems] = useState<any[]>([])
  const [detailName, setDetailName] = useState<string | null>(null)
  const [detail, setDetail] = useState<SystemInfo | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  // Delete
  const [deleteTarget, setDeleteTarget] = useState<{ name: string } | null>(null)
  const [deleteError, setDeleteError] = useState('')

  // Group form
  const [showGroupForm, setShowGroupForm] = useState(false)
  const [editingGroupCode, setEditingGroupCode] = useState<string | null>(null)
  const emptyGroupForm = { code: '', display_name: '', server: '', servers: '', server_keywords: '', servers_by_env: '{}', variables: '{}' }
  const [groupForm, setGroupForm] = useState(emptyGroupForm)
  const [groupDeleteTarget, setGroupDeleteTarget] = useState<{ code: string } | null>(null)

  // Server-side groups (DB + inventory) for import-into-system-group flow
  type ServerGroupChoice = {
    key: string
    name: string
    display_name: string
    description: string
    server_names: string[]
    source: 'db' | 'inventory'
  }
  const [serverGroupChoices, setServerGroupChoices] = useState<ServerGroupChoice[]>([])
  const [groupImportKey, setGroupImportKey] = useState('')

  const loadSystems = () => {
    resource.systems().then((res: any) => {
      setSystems(res.data || [])
    }).catch(() => {})
  }

  useEffect(() => { loadSystems() }, [])

  const loadDetail = async (name: string) => {
    setDetailLoading(true)
    setDetailName(name)
    try {
      const res: any = await resource.systems.get(name)
      setDetail(res.data || null)
    } catch { setDetail(null) }
    setDetailLoading(false)
  }

  const openCreate = () => {
    navigate('/systems/create')
  }

  const openEdit = (name: string) => {
    navigate(`/systems/${name}/edit`)
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    try {
      await resource.systems.delete(deleteTarget.name)
      setDeleteTarget(null)
      setDeleteError('')
      notify('系统已删除', 'success')
      loadSystems()
      if (detailName === deleteTarget.name) {
        setDetailName(null)
        setDetail(null)
      }
    } catch (e: any) {
      const msg = typeof e === 'string' ? e : (e?.message || '删除失败')
      setDeleteError(msg)
    }
  }

  const handleDeleteService = async (svcName: string) => {
    if (!detailName) return
    if (!confirm(`确认删除服务 "${svcName}"？`)) return
    try {
      await resource.systemServices.delete(detailName, svcName)
      notify('服务已删除', 'success')
      loadDetail(detailName)
    } catch (e: any) {
      notify('删除失败', 'error')
    }
  }

  const openGroupCreate = () => {
    setEditingGroupCode(null)
    setGroupForm(emptyGroupForm)
    setGroupImportKey('')
    void loadServerGroupChoices()
    setShowGroupForm(true)
  }

  const loadServerGroupChoices = async () => {
    try {
      const [dbRes, invRes] = await Promise.allSettled([
        serverGroups.list(),
        serverManagement.groups.list(),
      ])
      const out: ServerGroupChoice[] = []
      const seen = new Set<string>()
      if (dbRes.status === 'fulfilled') {
        const items = (dbRes.value as any)?.data || []
        for (const g of items) {
          const name = String(g?.name || '').trim()
          if (!name || seen.has(`db:${name}`)) continue
          seen.add(`db:${name}`)
          out.push({
            key: `db:${name}`,
            name,
            display_name: g?.display_name || '',
            description: g?.description || '',
            server_names: Array.isArray(g?.server_names) ? g.server_names : [],
            source: 'db',
          })
        }
      }
      if (invRes.status === 'fulfilled') {
        const items = (invRes.value as any)?.data || []
        for (const g of items) {
          const name = String(g?.name || '').trim()
          if (!name || g?.is_default || seen.has(`inv:${name}`)) continue
          // Skip if same name already imported from DB to avoid duplicate-looking choices
          if (seen.has(`db:${name}`)) continue
          seen.add(`inv:${name}`)
          out.push({
            key: `inv:${name}`,
            name,
            display_name: '',
            description: `服务器清单分组（${g?.server_count || 0} 台）`,
            server_names: Array.isArray(g?.server_names) ? g.server_names : [],
            source: 'inventory',
          })
        }
      }
      setServerGroupChoices(out)
    } catch {
      setServerGroupChoices([])
    }
  }

  const applyServerGroupImport = (key: string) => {
    setGroupImportKey(key)
    if (!key) return
    const choice = serverGroupChoices.find((c) => c.key === key)
    if (!choice) return
    setGroupForm((prev) => ({
      ...prev,
      code: prev.code.trim() || choice.name,
      display_name: prev.display_name.trim() || choice.display_name || choice.name,
      servers: choice.server_names.join(', '),
    }))
  }

  const openGroupEdit = (code: string, g: any) => {
    setEditingGroupCode(code)
    setGroupImportKey('')
    setGroupForm({
      code,
      display_name: g.display_name || '',
      server: g.server || '',
      servers: (g.servers || []).join(', '),
      server_keywords: (g.server_keywords || []).join(', '),
      servers_by_env: JSON.stringify(g.servers_by_env || {}, null, 2),
      variables: JSON.stringify(g.variables || {}, null, 2),
    })
    void loadServerGroupChoices()
    setShowGroupForm(true)
  }

  const handleGroupSubmit = async () => {
    if (!detailName) return
    const code = groupForm.code.trim()
    if (!code) return notify('请输入分组代码', 'error')
    let svrs_by_env: any = {}
    try { svrs_by_env = JSON.parse(groupForm.servers_by_env || '{}') } catch { return notify('servers_by_env JSON 格式错误', 'error') }
    let vars: any = {}
    try { vars = JSON.parse(groupForm.variables || '{}') } catch { return notify('variables JSON 格式错误', 'error') }
    const servers = groupForm.servers.split(',').map((s: string) => s.trim()).filter(Boolean)
    const serverKeywords = groupForm.server_keywords.split(',').map((s: string) => s.trim()).filter(Boolean)
    const payload = {
      code,
      display_name: groupForm.display_name.trim() || code,
      server: groupForm.server.trim(),
      servers,
      server_keywords: serverKeywords,
      servers_by_env: svrs_by_env,
      variables: vars,
    }
    try {
      if (editingGroupCode) {
        await resource.systemGroups.update(detailName, editingGroupCode, payload)
        notify('分组已更新', 'success')
      } else {
        await resource.systemGroups.create(detailName, payload)
        notify('分组已创建', 'success')
      }
      setShowGroupForm(false)
      loadDetail(detailName)
    } catch (e: any) {
      const msg = typeof e === 'string' ? e : (e?.message || '操作失败')
      notify(msg, 'error')
    }
  }

  const handleGroupDelete = async () => {
    if (!detailName || !groupDeleteTarget) return
    try {
      await resource.systemGroups.delete(detailName, groupDeleteTarget.code)
      setGroupDeleteTarget(null)
      notify('分组已删除', 'success')
      loadDetail(detailName)
    } catch (e: any) {
      const msg = typeof e === 'string' ? e : (e?.message || '删除失败')
      notify(msg, 'error')
    }
  }

  const openServiceCreate = () => {
    if (!detailName) return
    navigate(`/systems/${detailName}/services/create`)
  }

  const openServiceEdit = (svc: any) => {
    if (!detailName) return
    navigate(`/systems/${detailName}/services/${svc.name}/edit`)
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '8px 12px',
    background: 'var(--bg-surface)',
    border: '1px solid var(--border-strong)',
    borderRadius: '6px', color: 'var(--text-primary)',
    fontSize: '14px',
  }

  const cardStyle: React.CSSProperties = {
    background: 'var(--bg-surface)', borderRadius: '10px', padding: '18px',
    cursor: 'pointer', transition: 'all 0.2s',
    border: '1px solid var(--border-strong)',
  }

  const badgeStyle = (type: string): React.CSSProperties => ({
    padding: '2px 8px', borderRadius: '4px', fontSize: '12px',
    background: type === 'DIRECT' ? 'var(--action-bg)' : 'rgba(16,185,129,.12)',
    color: type === 'DIRECT' ? 'var(--action-text)' : '#059669',
  })

  return (
    <div style={{ display: 'grid', gap: '20px' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ margin: 0 }}>系统管理 ({systems.length})</h2>
        {isAdmin && (
          <button className="btn btn-primary" onClick={openCreate}>+ 新建系统</button>
        )}
      </div>

      {/* System Cards Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '16px' }}>
        {systems.map((s) => (
          <div key={s.name} style={cardStyle}
            onClick={() => loadDetail(s.name)}
            onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--brand)' }}
            onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border-strong)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div>
                <h3 style={{ margin: 0, fontSize: '18px' }}>{s.display_name || s.name}</h3>
                <div style={{ color: 'var(--text-muted)', fontSize: '13px', marginTop: '4px' }}>{s.name}</div>
              </div>
              <span style={badgeStyle(s.strategy)}>{STRATEGY_LABELS[s.strategy] || s.strategy}</span>
            </div>
            <div style={{ display: 'flex', gap: '16px', marginTop: '12px', fontSize: '13px', color: 'var(--text-secondary)' }}>
              <span>{s.service_count || 0} 服务</span>
              <span>{s.environment_count || 0} 环境</span>
            </div>
            {isAdmin && (
              <div style={{ display: 'flex', gap: '8px', marginTop: '12px' }}
                onClick={(e) => e.stopPropagation()}>
                <button className="btn" style={{ padding: '2px 8px', fontSize: '12px', background: 'var(--border-strong)' }}
                  onClick={() => openEdit(s.name)}>编辑</button>
                <button className="btn" style={{ padding: '2px 8px', fontSize: '12px', background: 'var(--danger-surface)', color: 'var(--danger)' }}
                  onClick={() => { setDeleteTarget({ name: s.name }); setDeleteError('') }}>删除</button>
              </div>
            )}
          </div>
        ))}
        {systems.length === 0 && (
          <div style={{ color: 'var(--text-muted)', textAlign: 'center', gridColumn: '1 / -1', padding: '40px' }}>
            暂无系统 {isAdmin ? '—— 点击「+ 新建系统」创建第一个' : ''}
          </div>
        )}
      </div>

      {/* System Detail */}
      {detailName && (
        <div className="card">
          {detailLoading ? (
            <div style={{ color: 'var(--text-muted)', padding: '24px', textAlign: 'center' }}>加载中...</div>
          ) : detail ? (
            <>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <h3 style={{ margin: 0 }}>{detail.display_name} ({detail.name})</h3>
                <span style={badgeStyle(detail.strategy)}>{STRATEGY_LABELS[detail.strategy] || detail.strategy}</span>
              </div>
              {detail.description && (
                <div style={{ color: 'var(--text-secondary)', fontSize: '13px', marginTop: '8px' }}>{detail.description}</div>
              )}

              {/* Services Table */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '20px', marginBottom: '10px' }}>
                <h4 style={{ margin: 0 }}>
                  服务配置 ({(detail.services || []).length})
                </h4>
                {isAdmin && (
                  <button className="btn" style={{ padding: '2px 10px', fontSize: '12px' }} onClick={openServiceCreate}>
                    + 新建服务
                  </button>
                )}
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border-strong)', textAlign: 'left' }}>
                    <th style={{ padding: '8px 12px', color: 'var(--text-muted)', fontWeight: '600' }}>名称</th>
                    <th style={{ padding: '8px 12px', color: 'var(--text-muted)', fontWeight: '600' }}>显示名</th>
                    <th style={{ padding: '8px 12px', color: 'var(--text-muted)', fontWeight: '600' }}>模板</th>
                    <th style={{ padding: '8px 12px', color: 'var(--text-muted)', fontWeight: '600' }}>默认服务器</th>
                    {isAdmin && <th style={{ padding: '8px 12px', width: '80px' }}></th>}
                  </tr>
                </thead>
                <tbody>
                  {(detail.services || []).map((svc: any) => (
                    <tr key={svc.name}
                      style={{ borderBottom: '1px solid var(--border-strong)', transition: 'background 0.15s' }}
                      onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = 'var(--action-bg)' }}
                      onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = '' }}>
                      <td style={{ padding: '8px 12px', fontWeight: '600' }}>{svc.name}</td>
                      <td style={{ padding: '8px 12px', color: 'var(--text-secondary)' }}>{svc.display_name || '-'}</td>
                      <td style={{ padding: '8px 12px', color: 'var(--brand)', fontSize: '12px' }}>
                        {TEMPLATE_LABELS[svc.template] || svc.template || '-'}
                      </td>
                      <td style={{ padding: '8px 12px', color: 'var(--text-muted)' }}>
                        {(svc.servers || []).join(', ') || '-'}
                      </td>
                      {isAdmin && (
                        <td style={{ padding: '8px 12px', display: 'flex', gap: '4px' }}>
                          <button className="btn"
                            style={{ padding: '0 6px', fontSize: '11px', background: 'var(--action-bg)', color: 'var(--action-text)', border: 'none', borderRadius: '3px' }}
                            onClick={() => openServiceEdit(svc)}>✏</button>
                          <button className="btn"
                            style={{ padding: '0 6px', fontSize: '11px', background: 'var(--danger-surface)', color: 'var(--danger)', border: 'none', borderRadius: '3px' }}
                            onClick={() => handleDeleteService(svc.name)}>🗑</button>
                        </td>
                      )}
                    </tr>
                  ))}
                  {(detail.services || []).length === 0 && (
                    <tr>
                      <td colSpan={isAdmin ? 5 : 4} style={{ padding: '16px', color: 'var(--text-muted)', textAlign: 'center' }}>
                        暂无服务 {isAdmin ? '—— 点击「+ 新建服务」创建第一个' : ''}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>

              {/* Groups */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '24px', marginBottom: '10px' }}>
                <h4 style={{ margin: 0 }}>
                  分组/Region ({Object.keys(detail.groups).length})
                </h4>
                {isAdmin && (
                  <button className="btn" style={{ padding: '2px 10px', fontSize: '12px' }} onClick={openGroupCreate}>
                    + 新建分组
                  </button>
                )}
              </div>
              {Object.keys(detail.groups).length > 0 ? (
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                  {Object.entries(detail.groups).map(([code, g]: [string, any]) => (
                    <span key={code} style={{
                      padding: '4px 10px', borderRadius: '6px', fontSize: '13px',
                      background: 'var(--action-bg)', color: 'var(--action-text)',
                      display: 'flex', alignItems: 'center', gap: '6px',
                    }}>
                      <span>
                        {code}{g.display_name ? ` (${g.display_name})` : ''}
                        {g.server ? ` → ${g.server}` : ''}
                      </span>
                      {isAdmin && (
                        <>
                          <button className="btn"
                            style={{ padding: '0 4px', fontSize: '10px', background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer' }}
                            title="编辑"
                            onClick={() => openGroupEdit(code, g)}>✏</button>
                          <button className="btn"
                            style={{ padding: '0 4px', fontSize: '10px', background: 'transparent', border: 'none', color: 'var(--danger)', cursor: 'pointer' }}
                            title="删除"
                            onClick={() => setGroupDeleteTarget({ code })}>🗑</button>
                        </>
                      )}
                    </span>
                  ))}
                </div>
              ) : (
                <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>
                  暂无分组 {isAdmin ? '—— 点击「+ 新建分组」创建' : ''}
                </div>
              )}

              {/* Group Form Modal */}
              {showGroupForm && (
                <div className="card" style={{ marginTop: '16px' }}>
                  <h4>{editingGroupCode ? `编辑分组: ${editingGroupCode}` : '新建分组'}</h4>
                  {serverGroupChoices.length > 0 && (
                    <div style={{ marginTop: '12px', padding: '10px 12px', background: 'var(--bg-page)', border: '1px dashed var(--border-strong)', borderRadius: '6px' }}>
                      <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '4px' }}>
                        从服务器分组导入（自动填充代码、显示名、服务器列表）
                      </label>
                      <select
                        style={inputStyle}
                        value={groupImportKey}
                        onChange={(e) => applyServerGroupImport(e.target.value)}
                      >
                        <option value="">— 不导入 / 手动填写 —</option>
                        {serverGroupChoices.map((c) => (
                          <option key={c.key} value={c.key}>
                            {c.source === 'db' ? '[DB]' : '[清单]'} {c.display_name ? `${c.display_name} (${c.name})` : c.name}
                            {` · ${c.server_names.length} 台`}
                          </option>
                        ))}
                      </select>
                      {groupImportKey && (
                        <div style={{ marginTop: '6px', fontSize: '12px', color: 'var(--text-muted)' }}>
                          已导入：可在下方继续修改 servers_by_env、关键词、变量等字段。
                        </div>
                      )}
                    </div>
                  )}
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px', marginTop: '12px' }}>
                    <div>
                      <label>分组代码 *</label>
                      <input style={inputStyle} value={groupForm.code}
                        disabled={!!editingGroupCode}
                        onChange={(e) => setGroupForm({ ...groupForm, code: e.target.value })}
                        placeholder="cn-shanghai" />
                    </div>
                    <div>
                      <label>显示名称</label>
                      <input style={inputStyle} value={groupForm.display_name}
                        onChange={(e) => setGroupForm({ ...groupForm, display_name: e.target.value })}
                        placeholder="上海区域" />
                    </div>
                    <div>
                      <label>主服务器</label>
                      <input style={inputStyle} value={groupForm.server}
                        onChange={(e) => setGroupForm({ ...groupForm, server: e.target.value })}
                        placeholder="prod-cn-shanghai" />
                    </div>
                    <div>
                      <label>服务器列表 (逗号分隔)</label>
                      <input style={inputStyle} value={groupForm.servers}
                        onChange={(e) => setGroupForm({ ...groupForm, servers: e.target.value })}
                        placeholder="server-1, server-2" />
                    </div>
                    <div>
                      <label>服务器关键词 (逗号分隔)</label>
                      <input style={inputStyle} value={groupForm.server_keywords}
                        onChange={(e) => setGroupForm({ ...groupForm, server_keywords: e.target.value })}
                        placeholder="blue, green" />
                    </div>
                    <div></div>
                    <div style={{ gridColumn: '1 / -1' }}>
                      <label>servers_by_env (JSON)</label>
                      <textarea style={{ ...inputStyle, minHeight: '60px', fontFamily: 'monospace', fontSize: '13px' }}
                        value={groupForm.servers_by_env}
                        onChange={(e) => setGroupForm({ ...groupForm, servers_by_env: e.target.value })} />
                    </div>
                    <div style={{ gridColumn: '1 / -1' }}>
                      <label>变量 (JSON)</label>
                      <textarea style={{ ...inputStyle, minHeight: '60px', fontFamily: 'monospace', fontSize: '13px' }}
                        value={groupForm.variables}
                        onChange={(e) => setGroupForm({ ...groupForm, variables: e.target.value })} />
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: '12px', marginTop: '16px' }}>
                    <button className="btn btn-primary" onClick={handleGroupSubmit}>
                      {editingGroupCode ? '保存修改' : '创建分组'}
                    </button>
                    <button className="btn" onClick={() => setShowGroupForm(false)}>取消</button>
                  </div>
                </div>
              )}

              {/* Environments */}
              <h4 style={{ marginTop: '24px', marginBottom: '10px' }}>
                环境 ({Object.keys(detail.environments || {}).length})
              </h4>
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                {Object.entries(detail.environments || {}).map(([name, env]: [string, any]) => (
                  <span key={name} style={{
                    padding: '4px 10px', borderRadius: '6px', fontSize: '13px',
                    background: name === 'prod' ? 'var(--danger-surface)' : name === 'test' ? 'var(--action-bg)' : 'var(--border-strong)',
                    color: name === 'prod' ? 'var(--danger)' : name === 'test' ? 'var(--action-text)' : 'var(--text-secondary)',
                  }}>
                    {env.display_name || name}
                    {(env.servers || []).length > 0 ? ` (${env.servers.length}台)` : ''}
                  </span>
                ))}
                {Object.keys(detail.environments || {}).length === 0 && (
                  <span style={{ color: 'var(--text-muted)' }}>暂无环境</span>
                )}
              </div>

              {/* Variables */}
              <h4 style={{ marginTop: '24px', marginBottom: '10px' }}>
                系统变量 ({Object.keys(detail.variables || {}).length})
              </h4>
              {Object.keys(detail.variables || {}).length > 0 ? (
                <pre style={{
                  padding: '12px', background: 'var(--bg-page)',
                  border: '1px solid var(--border-strong)', borderRadius: '6px',
                  fontSize: '12px', fontFamily: 'monospace', overflow: 'auto',
                  color: 'var(--text-secondary)',
                }}>
                  {JSON.stringify(detail.variables, null, 2)}
                </pre>
              ) : (
                <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>无系统级变量</div>
              )}

              {detail.servers && detail.servers.length > 0 && (
                <>
                  <h4 style={{ marginTop: '24px', marginBottom: '10px' }}>
                    系统级默认服务器 ({detail.servers.length})
                  </h4>
                  <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                    {detail.servers.map((srv: string) => (
                      <span key={srv} style={{
                        padding: '4px 10px', borderRadius: '6px', fontSize: '13px',
                        background: 'var(--border-strong)', color: 'var(--text-secondary)',
                      }}>{srv}</span>
                    ))}
                  </div>
                </>
              )}
            </>
          ) : (
            <div style={{ color: 'var(--error)', padding: '24px', textAlign: 'center' }}>
              加载失败，请重试。
            </div>
          )}
        </div>
      )}

      {/* Delete Confirm */}
      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="删除系统"
        description={`确认删除系统 "${deleteTarget?.name || ''}"？如果有应用引用了该系统，删除会被阻止。`}
        confirmLabel="删除系统"
        danger
        onCancel={() => { setDeleteTarget(null); setDeleteError('') }}
        onConfirm={handleDelete}
      >{deleteError ? (
          <div style={{ color: 'var(--danger)', fontSize: '13px', marginTop: '12px', padding: '8px', background: 'var(--danger-surface)', borderRadius: '6px' }}>
            {deleteError}
          </div>
        ) : null}
      </ConfirmDialog>

      {/* Group Delete Confirm */}
      <ConfirmDialog
        open={Boolean(groupDeleteTarget)}
        title="删除分组"
        description={`确认删除分组 "${groupDeleteTarget?.code || ''}"？`}
        confirmLabel="删除分组"
        danger
        onCancel={() => setGroupDeleteTarget(null)}
        onConfirm={handleGroupDelete}
      />
    </div>
  )
}