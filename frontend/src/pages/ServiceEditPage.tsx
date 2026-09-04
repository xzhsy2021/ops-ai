import { useState, useEffect } from 'react'
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

  const [form, setForm] = useState({
    name: '',
    display_name: '',
    template: 'generic_backend_direct',
    repo: '',
    servers: [] as string[], // 存量兜底清单（只读展示语义：无按环境配置时发布回退用）
    template_variables: {} as Record<string, any>,
  })
  // 服务 × 环境服务器分配（template_variables.servers_by_env）
  const [systemEnvs, setSystemEnvs] = useState<Array<{ name: string; display_name?: string; category?: string; servers?: any[] }>>([])
  const [serversByEnv, setServersByEnv] = useState<Record<string, string[]>>({})
  const [envServerSearch, setEnvServerSearch] = useState<Record<string, string>>({})

  useEffect(() => {
    serverManagement.list(false).then((res: any) => {
      const list = (res.data || []).map((s: any) => s.name).filter(Boolean).sort()
      setAllServers(list)
    }).catch(() => {})
  }, [])

  // 系统环境列表（服务×环境分配的目标环境）
  useEffect(() => {
    if (!systemName) return
    resource.environments(systemName).then((res: any) => {
      const list = Array.isArray(res.data) ? res.data.filter((e: any) => e?.name && e?.type === 'system') : []
      setSystemEnvs(list)
    }).catch(() => {})
  }, [systemName])

  useEffect(() => {
    if (!systemName || !serviceName) return
    setLoading(true)
    resource.systems.get(systemName).then((res: any) => {
      const system = res.data || {}
      const svc = (system.services || []).find((s: any) => s.name === serviceName)
      if (svc) {
        const tv = { ...(svc.template_variables || {}) }
        const sbe = tv.servers_by_env
        delete tv.servers_by_env // 服务器分配由上方专用区管理，不暴露原始键
        setForm({
          name: svc.name || '',
          display_name: svc.display_name || '',
          template: svc.template || 'generic_backend_direct',
          repo: svc.repo || '',
          servers: svc.servers || [],
          template_variables: tv,
        })
        if (sbe && typeof sbe === 'object') {
          const normalized: Record<string, string[]> = {}
          for (const [env, val] of Object.entries(sbe)) {
            if (Array.isArray(val)) normalized[env] = val.map(String)
            else if (typeof val === 'string' && val.trim()) normalized[env] = val.split(',').map((s) => s.trim()).filter(Boolean)
          }
          setServersByEnv(normalized)
        }
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
    // servers_by_env 并入模板变量（发布链路 _service_servers_for_environment 消费）
    const template_variables = { ...form.template_variables }
    const hasEnvAlloc = Object.values(serversByEnv).some((v) => (v || []).length > 0)
    if (hasEnvAlloc) {
      template_variables.servers_by_env = serversByEnv
    } else {
      delete template_variables.servers_by_env
    }
    const payload = {
      name,
      display_name: form.display_name.trim() || name,
      template: form.template || 'generic_backend_direct',
      repo: form.repo.trim(),
      servers: form.servers || [],
      template_variables,
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
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="crypto-frontend"
            />
            {!!serviceName && (
              <div style={{ color: 'var(--text-muted)', fontSize: '12px', marginTop: '4px' }}>
                修改名称会同步更新环境级服务覆盖配置；若新名称已被占用将被拒绝
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
              <label style={{ margin: 0 }}>服务器分配（按环境，本服务唯一服务器配置点）</label>
              <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                共 {allServers.length} 台可选
              </span>
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>
              发布时按发布环境取对应清单；⚠ 表示超出该环境权威清单（系统编辑页配置），发布会被隔离闸拦截。
            </div>
            {systemEnvs.length === 0 ? (
              <div style={{ padding: '16px', border: '1px dashed var(--border)', borderRadius: '6px', color: 'var(--text-muted)', fontSize: '13px', textAlign: 'center' }}>
                该系统暂无环境配置——请先在系统编辑页创建环境（test/prod），再回到此处按环境分配服务器。
              </div>
            ) : systemEnvs.map((env) => {
                const selected = serversByEnv[env.name] || []
                const q = (envServerSearch[env.name] || '').trim().toLowerCase()
                const candidates = (q
                  ? allServers.filter((s) => s.toLowerCase().includes(q))
                  : allServers
                ).filter((s) => !selected.includes(s)).slice(0, 50)
                const isTest = env.category === 'test'
                const isProd = env.category === 'prod'
                const envServerIds = new Set((env.servers || []).map((s: any) => (typeof s === 'string' ? s : (s?.id ?? s?.name ?? ''))).filter(Boolean))
                const toggle = (srv: string) => {
                  setServersByEnv((prev) => {
                    const cur = prev[env.name] || []
                    const next = cur.includes(srv) ? cur.filter((x) => x !== srv) : [...cur, srv]
                    return { ...prev, [env.name]: next }
                  })
                }
                return (
                  <div key={env.name} style={{
                    border: '1px solid var(--border)', borderRadius: '6px',
                    padding: '10px', marginBottom: '10px',
                  }}>
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '6px' }}>
                      <span style={{ fontWeight: 600, fontSize: '13px' }}>
                        {env.display_name || env.name}
                      </span>
                      {env.category && (
                        <span style={{
                          fontSize: '11px', padding: '1px 8px', borderRadius: '10px',
                          background: isTest ? 'var(--success-surface, #e8f5e9)' : isProd ? 'var(--danger-surface, #fce8e8)' : 'var(--bg-secondary)',
                          color: isTest ? 'var(--success, #2e7d32)' : isProd ? 'var(--danger, #c62828)' : 'var(--text-secondary)',
                          fontWeight: 600,
                        }}>{env.category}</span>
                      )}
                      <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                        {selected.length} 台
                      </span>
                    </div>
                    {selected.length > 0 && (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginBottom: '6px' }}>
                        {selected.map((srv) => {
                          const outside = envServerIds.size > 0 && !envServerIds.has(srv)
                          return (
                            <span key={srv} title={outside ? '不在环境权威清单内——发布时会被环境隔离闸拦截' : '点击 × 移除'}
                              style={{
                                display: 'inline-flex', alignItems: 'center', gap: '6px',
                                padding: '2px 8px', borderRadius: '4px', fontSize: '12px', fontFamily: 'monospace',
                                background: outside ? 'var(--warning-surface, #fff8e1)' : 'var(--action-bg)',
                                border: outside ? '1px solid var(--warning, #ffb300)' : '1px solid transparent',
                                color: outside ? 'var(--warning, #b26a00)' : 'var(--text-primary)',
                              }}>
                              {srv}{outside && '⚠'}
                              <button type="button" onClick={() => toggle(srv)} aria-label={`移除 ${srv}`}
                                style={{ cursor: 'pointer', border: 'none', background: 'transparent', color: 'inherit', fontSize: '12px', padding: 0, lineHeight: 1 }}>×</button>
                            </span>
                          )
                        })}
                      </div>
                    )}
                    <div style={{ display: 'flex', gap: '6px' }}>
                      <input
                        style={{ ...inputStyle, flex: '0 1 220px', fontSize: '12px', padding: '5px 10px' }}
                        placeholder="搜索添加服务器..."
                        value={envServerSearch[env.name] || ''}
                        onChange={(e) => setEnvServerSearch((prev) => ({ ...prev, [env.name]: e.target.value }))}
                      />
                      <div style={{
                        flex: 1, maxHeight: '110px', overflowY: 'auto',
                        border: '1px solid var(--border)', borderRadius: '6px', fontSize: '12px',
                      }}>
                        {candidates.length === 0 ? (
                          <div style={{ padding: '8px 10px', color: 'var(--text-muted)' }}>
                            {q ? '无匹配' : '全部已选或无候选'}
                          </div>
                        ) : candidates.map((srv) => {
                          const outside = envServerIds.size > 0 && !envServerIds.has(srv)
                          return (
                            <button key={srv} type="button" onClick={() => toggle(srv)}
                              title={outside ? '该服务器不在环境权威清单内' : '点击加入'}
                              style={{
                                display: 'flex', justifyContent: 'space-between', width: '100%',
                                padding: '5px 10px', background: 'transparent', border: 'none',
                                borderBottom: '1px solid var(--border)', cursor: 'pointer',
                                color: outside ? 'var(--warning, #b26a00)' : 'var(--text-primary)',
                                fontFamily: 'monospace', fontSize: '12px', textAlign: 'left',
                              }}>
                              <span>{outside ? '⚠ ' : '+ '}{srv}</span>
                            </button>
                          )
                        })}
                      </div>
                    </div>
                  </div>
                )
              })}
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
