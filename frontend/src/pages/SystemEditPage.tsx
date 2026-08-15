import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { resource } from '../api'
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
  const [environments, setEnvironments] = useState<Array<{ name: string; display_name?: string; category?: string; variables: Record<string, any> }>>([])
  const [activeEnv, setActiveEnv] = useState('')
  const [envVars, setEnvVars] = useState<Record<string, any>>({})
  const [envSaving, setEnvSaving] = useState(false)

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
      const list = Array.isArray(res.data) ? res.data.filter((e: any) => e?.name) : []
      setEnvironments(list)
      if (list.length > 0) {
        setActiveEnv(list[0].name)
        setEnvVars(stripEnvMetaKeys(list[0].variables || {}))
      }
    }).catch(() => {})
  }, [name])

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
  }

  const handleEnvSave = async () => {
    if (!name || !activeEnv) return
    const env = environments.find((e) => e.name === activeEnv)
    setEnvSaving(true)
    try {
      await resource.systemEnvironments.update(name, activeEnv, {
        name: activeEnv,
        display_name: env?.display_name || '',
        category: env?.category || 'custom',
        variables: envVars,
      })
      notify(`环境 ${activeEnv} 变量已保存`, 'success')
      setEnvironments((prev) => prev.map((e) =>
        e.name === activeEnv ? { ...e, variables: { ...envVars, environment: activeEnv } } : e
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
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '10px' }}>
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
                  <button className="btn" onClick={handleEnvSave} disabled={envSaving}>
                    {envSaving ? '保存中...' : `保存 ${activeEnv} 环境变量`}
                  </button>
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
