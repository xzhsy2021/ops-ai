import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { resource, serverManagement } from '../api'
import { useNotificationStore } from '../store'
import KeyValueEditor from '../components/KeyValueEditor'
import { ApproverEditor, RoomEditor } from '../components/BindingEditors'
import type { ConversationBinding } from '../components/BindingEditors'
import {
  normalizeApprovers,
  withStructuredApprovers,
} from '../utils/approverIdentities.js'
import type { ApproverIdentity } from '../utils/approverIdentities.js'

const STRATEGY_LABELS: Record<string, string> = {
  DIRECT: 'Direct 直推',
  DOVO: 'Dovo 蓝绿',
  WORKFLOW: 'Workflow',
}

interface MessageRouting {
  enabled: boolean
  aliases: string[]
  keywords: string[]
  priority: number
  approvers: ApproverIdentity[]
  rooms: ConversationBinding[]
}

const DEFAULT_ROUTING: MessageRouting = {
  enabled: false,
  aliases: [],
  keywords: [],
  priority: 0,
  approvers: [],
  rooms: [],
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
  const [services, setServices] = useState<Array<{ name: string }>>([])
  const [routing, setRouting] = useState<MessageRouting>(DEFAULT_ROUTING)
  const [aliasInput, setAliasInput] = useState('')
  const [keywordInput, setKeywordInput] = useState('')
  const [environments, setEnvironments] = useState<Array<{ name: string; display_name?: string; category?: string; variables: Record<string, any>; servers?: any[]; service_overrides?: Record<string, any> }>>([])
  const [activeEnv, setActiveEnv] = useState('')
  const [envVars, setEnvVars] = useState<Record<string, any>>({})
  const [envServers, setEnvServers] = useState('')
  const [envSaving, setEnvSaving] = useState(false)
  // 环境级服务覆盖（服务×环境差异化：template/servers）
  const [envServiceOverrides, setEnvServiceOverrides] = useState<Record<string, any>>({})
  // 服务器管理库（可选清单来源）
  const [serverOptions, setServerOptions] = useState<Array<{ name: string; host?: string; group?: string; enabled?: boolean }>>([])
  const [serverPickerQuery, setServerPickerQuery] = useState('')

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
      setServices(Array.isArray(s.services) ? s.services : [])
      const r = s.message_routing || {}
      setRouting({
        enabled: !!r.enabled,
        aliases: Array.isArray(r.aliases) ? r.aliases : [],
        keywords: Array.isArray(r.keywords) ? r.keywords : [],
        priority: typeof r.priority === 'number' ? r.priority : 0,
        approvers: normalizeApprovers(r.approvers),
        rooms: normalizeRooms(r.rooms),
      })
    }).catch(() => {
      notify('无法加载系统配置', 'error')
    }).finally(() => {
      setLoading(false)
    })

    resource.environments(name).then((res: any) => {
      const list = Array.isArray(res.data) ? res.data.filter((e: any) => e?.name && e?.type === 'system') : []
      setEnvironments(list)
      if (list.length > 0) {
        setActiveEnv(list[0].name)
        setEnvVars(stripEnvMetaKeys(list[0].variables || {}))
        setEnvServers(normalizeEnvServers(list[0].servers))
        setEnvServiceOverrides(JSON.parse(JSON.stringify(list[0].service_overrides || {})))
      }
    }).catch(() => {})
  }, [name])

  // 服务器管理库：环境清单的可选池（不强制——仍可手动补行）
  useEffect(() => {
    serverManagement.list(false).then((res: any) => {
      const list = Array.isArray(res.data) ? res.data : []
      setServerOptions(list
        .filter((s: any) => s?.name)
        .map((s: any) => ({ name: s.name, host: s.host, group: s.group, enabled: s.enabled !== false }))
        .sort((a: any, b: any) => a.name.localeCompare(b.name)))
    }).catch(() => {})
  }, [])

  // 启用路由时，若关键词为空，自动填充当前系统已配置的服务名
  const handleToggleEnabled = (checked: boolean) => {
    if (checked && routing.keywords.length === 0 && services.length > 0) {
      const serviceNames = services
        .map((s) => s?.name)
        .filter((n): n is string => !!n && typeof n === 'string')
      if (serviceNames.length > 0) {
        setRouting({
          ...routing,
          enabled: true,
          keywords: serviceNames,
        })
        notify(`已自动填充 ${serviceNames.length} 个服务名为关键词`, 'success')
        return
      }
    }
    setRouting({ ...routing, enabled: checked })
  }

  const addAlias = () => {
    const v = aliasInput.trim()
    if (!v) return
    if (routing.aliases.includes(v)) {
      notify('别名已存在', 'error')
      return
    }
    setRouting({ ...routing, aliases: [...routing.aliases, v] })
    setAliasInput('')
  }

  const removeAlias = (idx: number) => {
    setRouting({ ...routing, aliases: routing.aliases.filter((_, i) => i !== idx) })
  }

  const addKeyword = () => {
    const v = keywordInput.trim()
    if (!v) return
    if (routing.keywords.includes(v)) {
      notify('关键词已存在', 'error')
      return
    }
    setRouting({ ...routing, keywords: [...routing.keywords, v] })
    setKeywordInput('')
  }

  const removeKeyword = (idx: number) => {
    setRouting({ ...routing, keywords: routing.keywords.filter((_, i) => i !== idx) })
  }

  // 过滤后端注入的元数据键（environment/category/display_name 由环境配置本身维护）
  const stripEnvMetaKeys = (v: Record<string, any>) => {
    const { environment, category, display_name, ...rest } = v || {}
    return rest
  }

  // 环境权威服务器清单 → 文本域行（dict {id} 或纯字符串均兼容）
  const normalizeEnvServers = (servers: any): string => {
    if (!Array.isArray(servers)) return ''
    return servers
      .map((s) => (typeof s === 'string' ? s : (s?.id ?? s?.name ?? '')))
      .filter(Boolean)
      .join('\n')
  }

  // 规范化系统级 message_routing.rooms（渠道会话绑定），无效项忽略
  const normalizeRooms = (value: unknown): ConversationBinding[] => {
    if (!Array.isArray(value)) return []
    const result: ConversationBinding[] = []
    const seen = new Set<string>()
    value.forEach((item) => {
      if (!item || typeof item !== 'object') return
      const candidate = item as Record<string, unknown>
      const channel = typeof candidate.channel === 'string' ? candidate.channel.trim() : ''
      const account = typeof candidate.channel_account_id === 'string'
        ? candidate.channel_account_id.trim()
        : ''
      const conversation = typeof candidate.conversation_id === 'string'
        ? candidate.conversation_id.trim()
        : ''
      if (!channel || !conversation) return
      const key = `${channel}\u0000${account || 'default'}\u0000${conversation}`
      if (seen.has(key)) return
      seen.add(key)
      result.push({ channel, channel_account_id: account || 'default', conversation_id: conversation })
    })
    return result
  }

  const handleEnvChange = (envName: string) => {
    const env = environments.find((e) => e.name === envName)
    setActiveEnv(envName)
    setEnvVars(stripEnvMetaKeys(env?.variables || {}))
    setEnvServers(normalizeEnvServers(env?.servers))
    setEnvServiceOverrides(JSON.parse(JSON.stringify(env?.service_overrides || {})))
  }

  const handleEnvSave = async () => {
    if (!name || !activeEnv) return
    const env = environments.find((e) => e.name === activeEnv)
    const servers = envServers.split('\n').map((s) => s.trim()).filter(Boolean)
    if ((env?.category === 'test' || env?.category === 'prod') && servers.length === 0) {
      if (!window.confirm(`环境 ${activeEnv} 的服务器清单为空——空清单下该环境的所有执行计划都会被拒绝（fail-closed）。确认保存？`)) return
    }
    setEnvSaving(true)
    try {
      await resource.systemEnvironments.update(name, activeEnv, {
        name: activeEnv,
        display_name: env?.display_name || '',
        category: env?.category || 'custom',
        servers,
        variables: envVars,
        service_overrides: envServiceOverrides,
      })
      notify(`环境 ${activeEnv} 已保存（${servers.length} 台服务器）`, 'success')
      setEnvironments((prev) => prev.map((e) =>
        e.name === activeEnv ? { ...e, variables: { ...envVars, environment: activeEnv }, servers: servers.map((id) => ({ id })), service_overrides: JSON.parse(JSON.stringify(envServiceOverrides)) } : e
      ))
    } catch (e: any) {
      const msg = typeof e === 'string' ? e : (e?.message || '保存失败')
      notify(msg, 'error')
    } finally {
      setEnvSaving(false)
    }
  }

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
      message_routing: withStructuredApprovers(routing),
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
            <label style={{ display: 'block', marginBottom: '8px' }}>系统变量（所有环境共享）</label>
            <div style={{ color: 'var(--text-muted)', fontSize: '12px', marginBottom: '8px' }}>
              系统级变量对所有环境生效；同名键会被环境变量、服务模板变量逐级覆盖。继承顺序：系统变量 → 环境变量 → 服务模板变量。
            </div>
            <KeyValueEditor
              value={form.variables}
              onChange={(v) => setForm({ ...form, variables: v })}
              keyPlaceholder="变量名"
              valuePlaceholder="变量值"
              addButtonText="+ 添加变量"
              emptyText="暂无系统变量"
            />
          </div>
          <div style={{ gridColumn: '1 / -1' }}>
            <label style={{ display: 'block', marginBottom: '8px' }}>环境变量（按环境独立）</label>
            <div style={{ color: 'var(--text-muted)', fontSize: '12px', marginBottom: '8px' }}>
              各环境（如 prod / test）可配置独立变量，同名键会覆盖系统变量；被服务模板变量覆盖。
            </div>
            {environments.length > 0 ? (
              <>
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '10px', flexWrap: 'wrap' }}>
                  <select
                    style={{ ...inputStyle, maxWidth: '240px' }}
                    value={activeEnv}
                    onChange={(e) => handleEnvChange(e.target.value)}
                  >
                    {environments.map((e) => (
                      <option key={e.name} value={e.name}>
                        {e.display_name || e.name}{e.category ? ` (${e.category})` : ''}
                      </option>
                    ))}
                  </select>
                  {(() => {
                    const env = environments.find((e) => e.name === activeEnv)
                    if (!env?.category) return null
                    const isTest = env.category === 'test'
                    const isProd = env.category === 'prod'
                    return (
                      <span style={{
                        fontSize: '11px', padding: '2px 8px', borderRadius: '10px',
                        background: isTest ? 'var(--success-surface, #e8f5e9)' : isProd ? 'var(--danger-surface, #fce8e8)' : 'var(--bg-secondary)',
                        color: isTest ? 'var(--success, #2e7d32)' : isProd ? 'var(--danger, #c62828)' : 'var(--text-secondary)',
                        fontWeight: 600,
                      }}>
                        {env.category}
                      </span>
                    )
                  })()}
                  <button className="btn" onClick={handleEnvSave} disabled={envSaving}>
                    {envSaving ? '保存中...' : `保存 ${activeEnv} 环境`}
                  </button>
                </div>

                {/* 环境权威服务器清单（环境隔离闸） */}
                <div style={{
                  marginBottom: '16px', padding: '12px',
                  border: '1px solid var(--border)', borderRadius: '8px',
                }}>
                  <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '4px' }}>
                    环境服务器清单（安全边界）
                  </div>
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '8px' }}>
                    定义该环境允许触达的服务器范围（环境隔离闸的唯一数据源）——测试计划的 targets 必须全部在此清单内。
                    具体某个服务在哪些服务器上，请在服务编辑页「服务器分配」配置，此处不重复。
                  </div>
                  {(() => {
                    const env = environments.find((e) => e.name === activeEnv)
                    const listed = envServers.split('\n').map((s) => s.trim()).filter(Boolean)
                    const isGated = env?.category === 'test' || env?.category === 'prod'
                    if (isGated && listed.length === 0) {
                      return (
                        <div style={{
                          fontSize: '12px', padding: '8px 10px', marginBottom: '8px', borderRadius: '6px',
                          background: 'var(--warning-surface, #fff8e1)', color: 'var(--warning, #b26a00)',
                          border: '1px solid var(--warning, #ffb300)',
                        }}>
                          ⚠ 清单为空（fail-closed）：该环境的所有执行计划都会被拒绝——录入服务器后放行
                        </div>
                      )
                    }
                    return null
                  })()}

                  {/* 服务器管理库选择器：勾选 ⇄ 加入/移出清单 */}
                  {(() => {
                    const listed = new Set(envServers.split('\n').map((s) => s.trim()).filter(Boolean))
                    const q = serverPickerQuery.trim().toLowerCase()
                    const candidates = serverOptions.filter((s) =>
                      !listed.has(s.name) && (!q || s.name.toLowerCase().includes(q) || (s.host || '').toLowerCase().includes(q) || (s.group || '').toLowerCase().includes(q))
                    ).slice(0, 60)
                    const inPool = serverOptions.filter((s) => listed.has(s.name))
                    const orphans = listed.size > inPool.length
                      ? [...listed].filter((n) => !serverOptions.some((s) => s.name === n))
                      : []
                    const toggleServer = (serverName: string) => {
                      setEnvServers((prev) => {
                        const cur = new Set(prev.split('\n').map((s) => s.trim()).filter(Boolean))
                        if (cur.has(serverName)) cur.delete(serverName)
                        else cur.add(serverName)
                        return [...cur].join('\n')
                      })
                    }
                    return (
                      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', marginBottom: '10px' }}>
                        <div style={{ flex: '1 1 320px', minWidth: '260px' }}>
                          <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>
                            从服务器管理选择（{inPool.length}/{serverOptions.length} 已选）
                          </div>
                          <input
                            style={{ ...inputStyle, marginBottom: '6px' }}
                            value={serverPickerQuery}
                            onChange={(e) => setServerPickerQuery(e.target.value)}
                            placeholder="搜索名称 / IP / 分组..."
                          />
                          <div style={{
                            maxHeight: '180px', overflowY: 'auto',
                            border: '1px solid var(--border)', borderRadius: '6px',
                          }}>
                            {candidates.length === 0 ? (
                              <div style={{ padding: '10px', fontSize: '12px', color: 'var(--text-muted)' }}>
                                {q ? '无匹配服务器' : '服务器管理库为空或全部已选'}
                              </div>
                            ) : candidates.map((s) => (
                              <div key={s.name} style={{
                                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                                padding: '6px 10px', borderBottom: '1px solid var(--border)',
                                fontSize: '12px',
                              }}>
                                <button
                                  type="button"
                                  style={{
                                    background: 'none', border: 'none', cursor: 'pointer',
                                    padding: 0, textAlign: 'left', color: 'inherit',
                                    display: 'flex', flexDirection: 'column', gap: '2px',
                                  }}
                                  onClick={() => toggleServer(s.name)}
                                  title="点击加入/移出清单"
                                >
                                  <span style={{ fontFamily: 'monospace' }}>
                                    {s.enabled === false ? '⏸ ' : '+ '}{s.name}
                                  </span>
                                  <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>
                                    {[s.host, s.group].filter(Boolean).join(' · ') || '无分组'}
                                  </span>
                                </button>
                              </div>
                            ))}
                          </div>
                        </div>
                        <div style={{ flex: '1 1 260px', minWidth: '220px' }}>
                          <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>
                            当前清单（{listed.size} 台）
                          </div>
                          <div style={{
                            maxHeight: '212px', overflowY: 'auto',
                            border: '1px solid var(--border)', borderRadius: '6px',
                          }}>
                            {listed.size === 0 ? (
                              <div style={{ padding: '10px', fontSize: '12px', color: 'var(--text-muted)' }}>
                                空
                              </div>
                            ) : [...listed].map((n) => {
                              const known = serverOptions.find((s) => s.name === n)
                              return (
                                <div key={n} style={{
                                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                                  padding: '6px 10px', borderBottom: '1px solid var(--border)',
                                  fontSize: '12px',
                                }}>
                                  <span style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                                    <span style={{ fontFamily: 'monospace' }}>{n}</span>
                                    {known && known.group ? (
                                      <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>{known.group}</span>
                                    ) : !known ? (
                                      <span style={{ color: 'var(--warning, #b26a00)', fontSize: '11px' }}>
                                        不在服务器管理库（手动条目）
                                      </span>
                                    ) : null}
                                  </span>
                                  <button
                                    type="button"
                                    onClick={() => toggleServer(n)}
                                    style={{
                                      background: 'none', border: 'none', cursor: 'pointer',
                                      color: 'var(--danger, #c62828)', fontSize: '14px', padding: '2px 6px',
                                    }}
                                    title="移出清单"
                                  >
                                    ✕
                                  </button>
                                </div>
                              )
                            })}
                          </div>
                          {orphans.length > 0 && (
                            <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>
                              其中 {orphans.length} 台为手动条目（不在服务器管理库，仍会保存生效）
                            </div>
                          )}
                        </div>
                      </div>
                    )
                  })()}

                  <textarea
                    style={{ ...inputStyle, minHeight: '64px', fontFamily: 'monospace', fontSize: '12px' }}
                    value={envServers}
                    onChange={(e) => setEnvServers(e.target.value)}
                    placeholder={'也可手动编辑（每行一台服务器）\n47.84.58.154-量化测试'}
                  />
                  <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>
                    {envServers.split('\n').map((s) => s.trim()).filter(Boolean).length} 台 · 与服务绑定的服务器名一致（servers 列口径）
                  </div>
                </div>

                {/* 环境级服务差异化（service_overrides）——只管部署方式；服务器分配统一在服务编辑页 */}
                <div style={{
                  marginBottom: '16px', padding: '12px',
                  border: '1px solid var(--border)', borderRadius: '8px',
                }}>
                  <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '4px' }}>
                    部署方式差异化（按环境覆盖模板）
                  </div>
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '8px' }}>
                    同一服务在不同环境用不同部署模板——如线上二进制包、测试 docker compose。
                    服务器的分配统一在系统列表 → 服务编辑「服务器分配」，此处不重复配置。
                  </div>
                  {services.length === 0 ? (
                    <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>系统暂无服务</div>
                  ) : services.map((svc: any) => {
                    const ov = envServiceOverrides[svc.name] || {}
                    const hasOverride = !!envServiceOverrides[svc.name]
                    const updateOverride = (patch: Record<string, any>) => {
                      setEnvServiceOverrides((prev) => {
                        const cur = { ...prev }
                        const merged = { ...(cur[svc.name] || {}), ...patch }
                        const meaningful = Object.entries(merged).some(([, v]) =>
                          Array.isArray(v) ? v.length > 0 : (v !== undefined && v !== null && String(v) !== ''))
                        if (meaningful) cur[svc.name] = merged
                        else delete cur[svc.name]
                        return cur
                      })
                    }
                    return (
                      <div key={svc.name} style={{
                        display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap',
                        padding: '8px 10px', marginBottom: '6px',
                        border: '1px solid var(--border)', borderRadius: '6px',
                        background: hasOverride ? 'var(--action-bg)' : 'transparent',
                      }}>
                        <span style={{ fontSize: '13px', fontWeight: hasOverride ? 600 : 400, minWidth: '140px' }}>
                          {svc.display_name || svc.name}
                        </span>
                        <select
                          style={{ ...inputStyle, width: 'auto', minWidth: '260px', fontSize: '12px', padding: '4px 8px' }}
                          value={ov.template ?? ''}
                          onChange={(e) => updateOverride({ template: e.target.value })}
                        >
                          <option value="">跟随服务默认模板</option>
                          <option value="generic_backend_direct">generic_backend_direct（二进制直推）</option>
                          <option value="generic_frontend">generic_frontend（前端静态）</option>
                          <option value="docker_compose">docker_compose（容器编排）</option>
                          <option value="crypto_docker_compose">crypto_docker_compose（量化容器）</option>
                        </select>
                        {hasOverride && (
                          <button type="button" className="btn" style={{ fontSize: '12px', padding: '4px 10px' }}
                            onClick={() => setEnvServiceOverrides((prev) => { const c = { ...prev }; delete c[svc.name]; return c })}>
                            清除覆盖
                          </button>
                        )}
                      </div>
                    )
                  })}
                </div>

                <KeyValueEditor
                  value={envVars}
                  onChange={setEnvVars}
                  keyPlaceholder="变量名"
                  valuePlaceholder="变量值"
                  addButtonText="+ 添加变量"
                  emptyText="暂无环境变量"
                />
              </>
            ) : (
              <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>暂无环境，可先在发布页或环境管理接口创建环境。</div>
            )}
          </div>
        </div>

        {/* QClaw 消息渠道路由配置 */}
        <div style={{
          marginTop: '24px', paddingTop: '20px',
          borderTop: '1px solid var(--border-strong)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
            <h3 style={{ margin: 0, fontSize: '15px' }}>QClaw 消息渠道路由</h3>
            <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={routing.enabled}
                onChange={(e) => handleToggleEnabled(e.target.checked)}
              />
              <span style={{ fontSize: '13px' }}>启用</span>
            </label>
          </div>
          <div style={{ color: 'var(--text-muted)', fontSize: '12px', marginBottom: '12px' }}>
            配置后，QClaw 可将 Matrix、微信或 Telegram 消息确定性路由到此系统。详见{' '}
            <a href="/docs/qclaw-element-approval-integration.md" target="_blank" rel="noreferrer">
              集成文档
            </a>
            。
          </div>
          {routing.enabled && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '16px' }}>
              <div>
                <label>别名（精确匹配）</label>
                <div style={{ display: 'flex', gap: '6px', marginBottom: '6px' }}>
                  <input
                    style={{ ...inputStyle, flex: 1 }}
                    value={aliasInput}
                    onChange={(e) => setAliasInput(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addAlias() } }}
                    placeholder="如：量化、量化交易"
                  />
                  <button className="btn" onClick={addAlias} type="button">+</button>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                  {routing.aliases.map((a, i) => (
                    <span
                      key={i}
                      style={{
                        display: 'inline-flex', alignItems: 'center', gap: '4px',
                        padding: '2px 8px', borderRadius: '12px',
                        background: 'var(--bg-hover)', fontSize: '12px',
                      }}
                    >
                      {a}
                      <button
                        onClick={() => removeAlias(i)}
                        type="button"
                        style={{
                          background: 'none', border: 'none', cursor: 'pointer',
                          color: 'var(--text-muted)', padding: 0, fontSize: '14px',
                        }}
                      >×</button>
                    </span>
                  ))}
                  {routing.aliases.length === 0 && (
                    <span style={{ color: 'var(--text-muted)', fontSize: '12px' }}>暂无别名</span>
                  )}
                </div>
              </div>
              <div>
                <label>关键词（包含匹配）</label>
                <div style={{ color: 'var(--text-muted)', fontSize: '12px', marginBottom: '6px' }}>
                  启用时已自动填入当前系统已配置的服务名，可继续编辑添加自定义关键词
                </div>
                <div style={{ display: 'flex', gap: '6px', marginBottom: '6px' }}>
                  <input
                    style={{ ...inputStyle, flex: 1 }}
                    value={keywordInput}
                    onChange={(e) => setKeywordInput(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addKeyword() } }}
                    placeholder="如：btc strategy、crypto deploy"
                  />
                  <button className="btn" onClick={addKeyword} type="button">+</button>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                  {routing.keywords.map((k, i) => (
                    <span
                      key={i}
                      style={{
                        display: 'inline-flex', alignItems: 'center', gap: '4px',
                        padding: '2px 8px', borderRadius: '12px',
                        background: 'var(--bg-hover)', fontSize: '12px',
                      }}
                    >
                      {k}
                      <button
                        onClick={() => removeKeyword(i)}
                        type="button"
                        style={{
                          background: 'none', border: 'none', cursor: 'pointer',
                          color: 'var(--text-muted)', padding: 0, fontSize: '14px',
                        }}
                      >×</button>
                    </span>
                  ))}
                  {routing.keywords.length === 0 && (
                    <span style={{ color: 'var(--text-muted)', fontSize: '12px' }}>暂无关键词</span>
                  )}
                </div>
              </div>
              <div style={{ gridColumn: '1 / -1' }}>
                <label>优先级（0-1000，数字越大优先级越高）</label>
                <input
                  type="number"
                  min={0}
                  max={1000}
                  style={{ ...inputStyle, maxWidth: '200px' }}
                  value={routing.priority}
                  onChange={(e) => setRouting({ ...routing, priority: Math.max(0, Math.min(1000, Number(e.target.value) || 0)) })}
                />
                <div style={{ color: 'var(--text-muted)', fontSize: '12px', marginTop: '4px' }}>
                  多个系统匹配同关键词时，优先级高者优先；同优先级会判定为歧义（AMBIGUOUS）。
                </div>
              </div>
              <div style={{ gridColumn: '1 / -1' }}>
                <label>授权审批人</label>
                <ApproverEditor
                  value={routing.approvers}
                  onChange={(v) => setRouting({ ...routing, approvers: v })}
                  hint="审批身份按消息渠道、渠道账号和发送者 ID 精确匹配；系统级配置为唯一权威来源，token 级仅作旧数据回退。"
                  emptyText="未配置（同房间任意成员可审批）"
                />
              </div>
              <div style={{ gridColumn: '1 / -1' }}>
                <label>授权房间/会话</label>
                <RoomEditor
                  value={routing.rooms}
                  onChange={(v) => setRouting({ ...routing, rooms: v })}
                  hint="指定允许发起审批的渠道会话；留空表示不限制会话（仍受审批人绑定约束）。"
                  emptyText="未配置（不限制会话）"
                />
              </div>
            </div>
          )}
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
