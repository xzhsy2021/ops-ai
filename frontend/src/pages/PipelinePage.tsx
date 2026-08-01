import { useState, useEffect, useCallback } from 'react'
import { pipeline, pipelineBinding, resource } from '../api'
import { RiskConfirmDialog, PageHeader } from '../components/ui'
import { useNotificationStore } from '../store'
import { PipelineList, PipelineTemplatePanel } from './pipeline/PipelineComponents'
import type { PipelineData, StepConfig } from './pipeline/pipelineConfig'

const STEP_TYPES = [
  { type: 'checkout', label: '检出代码', defaults: { repo_url: '', branch: 'main' } },
  { type: 'build', label: '构建', defaults: { cmd: 'npm run build', timeout: '300' } },
  { type: 'upload', label: '上传', defaults: { local_path: '', remote_path: '' } },
  { type: 'deploy', label: '部署', defaults: { deploy_path: '', package_path: '' } },
  { type: 'switch', label: '切换版本', defaults: {} },
  { type: 'restart', label: '重启', defaults: { cmd: '' } },
  { type: 'health_check', label: '健康检查', defaults: { url: '', retries: '3' } },
  { type: 'command', label: '命令', defaults: { cmd: '', timeout: '120' } },
  { type: 'wait', label: '等待', defaults: { seconds: '5' } },
  {
    type: 'dovo_bluegreen_update',
    label: 'Dovo蓝绿',
    defaults: {
      group_code: '${service}',
      base_path: '${base_path}',
      instances: '${instances}',
      file_name: '${file_name}',
      update_script: '${update_script}',
      switch_script: '${switch_script}',
      wait_after_binupdate: '10',
      wait_after_portupdate: '5',
      binupdate_timeout: '180',
      portupdate_timeout: '180',
      log_check_timeout: '60',
      detect_timeout: '30',
      detect_command: '',
      log_check_command: '',
      log_must_contain: '',
      log_must_not_contain: '',
    },
  },
  {
    type: 'scripted_service_update',
    label: '后台updatebin',
    defaults: {
      service_dir: '${service_dir}',
      file_name: '${file_name}',
      update_script: '${update_script}',
      log_dir: 'logs',
      timeout: '600',
    },
  },
  {
    type: 'web_script_update',
    label: 'Web脚本发布',
    defaults: {
      deploy_path: '${deploy_path}',
      file_name: '${file_name}',
      update_script: '${update_script}',
      timeout: '600',
    },
  },
  {
    type: 'docker_compose_update',
    label: 'Docker Compose',
    defaults: {
      compose_dir: '/data/crypto-trader',
      compose_file: 'docker-compose.yml',
      wait_after_up: '10',
      log_tail_lines: '30',
      timeout: '600',
    },
  },
]

const STRATEGIES = [
  { value: 'DIRECT', label: '直接部署' },
  { value: 'BLUE_GREEN', label: '蓝绿发布' },
  { value: 'DOCKER_COMPOSE', label: 'Docker Compose' },
]

const BUILTIN_STEP_TYPES = new Set(['checkout', 'build', 'upload', 'deploy', 'switch', 'restart', 'health_check', 'command', 'wait'])

const STEP_RUNBOOKS: Record<string, string[]> = {
  dovo_bluegreen_update: [
    '识别哪个实例目录是未运行的旧程序，例如 idn1 / idn2（detect_command 可覆盖）',
    '上传发布包并更新 standby 目录下的 server',
    '在 standby 目录执行 binupdate.sh（binupdate_timeout 控制超时）',
    '等待 wait_after_binupdate 秒后做第一次日志检查（log_check_command 可覆盖；log_must_contain / log_must_not_contain 触发断言）',
    '在 standby 目录执行 portupdate.sh 切换端口（portupdate_timeout 控制超时）',
    '等待 wait_after_portupdate 秒后做第二次日志检查并再次断言',
  ],
  scripted_service_update: [
    '上传服务包到 service_dir，例如 /data/bin/crypto-trader/system',
    '备份同名旧包为 .preops.时间戳',
    '在服务目录执行 updatebin.sh',
    '查看进程和 logs 目录日志确认程序正常',
  ],
  web_script_update: [
    '上传本地包到 deploy_path，默认 /data/www',
    '在 deploy_path 目录执行 ./www.sh',
    '执行后检查 /data/www 目录结果',
  ],
  docker_compose_update: [
    '检查 compose_dir 是否存在',
    '执行 docker compose -f compose_file pull 拉取最新镜像',
    '执行 docker compose -f compose_file up -d --remove-orphans 启动/重启容器',
    '等待 wait_after_up 秒后做容器稳定观察',
    '执行 docker compose -f compose_file ps 检查容器状态',
    '执行 docker compose -f compose_file logs --tail=log_tail_lines 查看最近日志',
  ],
}

const STEP_FIELD_LABELS: Record<string, Record<string, string>> = {
  dovo_bluegreen_update: {
    group_code: '分组代码',
    base_path: '实例根目录',
    instances: '实例列表',
    file_name: '发布包/程序文件名',
    update_script: '更新脚本 (binupdate.sh)',
    switch_script: '端口切换脚本 (portupdate.sh)',
    wait_after_update: '更新后等待秒数 (旧字段，等价 wait_after_binupdate)',
    wait_after_binupdate: 'binupdate 后等待秒数',
    wait_after_portupdate: 'portupdate 后等待秒数',
    binupdate_timeout: 'binupdate 执行超时(秒)',
    portupdate_timeout: 'portupdate 执行超时(秒)',
    log_check_timeout: '日志检查超时(秒)',
    detect_timeout: 'standby 探测超时(秒)',
    detect_command: '自定义 standby 探测脚本 (留空使用内置)',
    log_check_command: '自定义日志检查命令 (可使用 ${standby_dir} 占位)',
    log_must_contain: '日志必须包含 (逗号分隔或数组)',
    log_must_not_contain: '日志不允许出现 (逗号分隔或数组)',
    remote_path: '远程临时包路径',
    local_path: '本地包路径',
  },
  scripted_service_update: {
    service_dir: '服务目录',
    file_name: '发布包文件名',
    update_script: '更新脚本',
    log_dir: '日志目录',
    timeout: '超时时间',
    remote_path: '远程包路径',
    local_path: '本地包路径',
  },
  web_script_update: {
    deploy_path: 'Web目录',
    file_name: '发布包文件名',
    update_script: '更新脚本',
    timeout: '超时时间',
    remote_path: '远程包路径',
    local_path: '本地包路径',
  },
  docker_compose_update: {
    compose_dir: 'compose 目录',
    compose_file: 'compose 文件名',
    wait_after_up: '启动后等待秒数',
    log_tail_lines: '日志显示行数',
    timeout: '操作超时(秒)',
  },
}

function normalizeConfigForView(config: Record<string, any>) {
  if (!config || typeof config !== 'object') return {}
  if (config.fields && typeof config.fields === 'object') {
    const normalized: Record<string, any> = {}
    Object.entries(config.fields).forEach(([key, value]: [string, any]) => {
      if (value && typeof value === 'object' && value.mode === 'binding') {
        normalized[key] = '${' + (value.var || key) + '}'
      } else if (value && typeof value === 'object' && value.mode === 'literal') {
        normalized[key] = value.value ?? ''
      } else {
        normalized[key] = value
      }
    })
    return normalized
  }
  return config
}

function normalizeConfigForEdit(
  rawConfig: Record<string, any> | undefined
): { config: Record<string, any>; bindings: Record<string, string> } {
  const out: Record<string, any> = {}
  const bindings: Record<string, string> = {}
  if (!rawConfig || typeof rawConfig !== 'object') return { config: out, bindings }
  if (rawConfig.fields && typeof rawConfig.fields === 'object') {
    Object.entries(rawConfig.fields).forEach(([k, v]: [string, any]) => {
      if (v && typeof v === 'object' && v.mode === 'binding') {
        bindings[k] = v.var || ''
        out[k] = ''
      } else if (v && typeof v === 'object' && v.mode === 'literal') {
        out[k] = v.value ?? ''
      } else {
        out[k] = v
      }
    })
    return { config: out, bindings }
  }
  Object.assign(out, rawConfig)
  return { config: out, bindings }
}

function stringifyConfigValue(value: any) {
  if (Array.isArray(value)) return value.join(', ')
  if (value && typeof value === 'object') return JSON.stringify(value)
  return String(value ?? '')
}

const PLACEHOLDER_RE = /\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g

function resolvePlaceholdersInValue(value: any, vars: Record<string, string>): any {
  if (value == null) return value
  if (typeof value === 'string') {
    return value.replace(PLACEHOLDER_RE, (match, name) =>
      Object.prototype.hasOwnProperty.call(vars, name) && vars[name] !== '' && vars[name] != null
        ? String(vars[name])
        : match
    )
  }
  if (Array.isArray(value)) return value.map((v) => resolvePlaceholdersInValue(v, vars))
  if (typeof value === 'object') {
    const out: Record<string, any> = {}
    for (const [k, v] of Object.entries(value)) out[k] = resolvePlaceholdersInValue(v, vars)
    return out
  }
  return value
}

function collectUsedVarNames(value: any, acc: Set<string>) {
  if (value == null) return
  if (typeof value === 'string') {
    let m: RegExpExecArray | null
    const re = new RegExp(PLACEHOLDER_RE.source, 'g')
    while ((m = re.exec(value)) !== null) acc.add(m[1])
    return
  }
  if (Array.isArray(value)) {
    value.forEach((v) => collectUsedVarNames(v, acc))
    return
  }
  if (typeof value === 'object') {
    Object.values(value).forEach((v) => collectUsedVarNames(v, acc))
  }
}

function StepExecutionPreview({ step, systemVars }: { step: StepConfig; systemVars: Record<string, string> }) {
  const normalized = normalizeConfigForView(step.config)
  const entries = Object.entries(normalized)
  const labels = STEP_FIELD_LABELS[step.type] || {}
  const runbook = STEP_RUNBOOKS[step.type] || []
  const resolvedConfig = resolvePlaceholdersInValue(step.config || {}, systemVars)
  const usedVars = new Set<string>()
  collectUsedVarNames(step.config || {}, usedVars)
  const resolvedVars: string[] = []
  const unresolvedVars: string[] = []
  usedVars.forEach((v) => {
    if (Object.prototype.hasOwnProperty.call(systemVars, v) && systemVars[v] !== '' && systemVars[v] != null) {
      resolvedVars.push(v)
    } else {
      unresolvedVars.push(v)
    }
  })

  return (
    <details open={!BUILTIN_STEP_TYPES.has(step.type)} style={{ gridColumn: '1 / -1', marginTop: '4px' }}>
      <summary style={{ cursor: 'pointer', color: 'var(--brand)', fontSize: '13px', fontWeight: 600 }}>
        查看当前步骤执行内容 / 配置
      </summary>
      <div style={{ marginTop: '10px', display: 'grid', gap: '10px' }}>
        {runbook.length > 0 && (
          <div style={{ background: 'var(--bg-surface)', borderRadius: '8px', padding: '10px' }}>
            <div style={{ color: 'var(--text-secondary)', fontSize: '12px', marginBottom: '6px', fontWeight: 600 }}>执行逻辑</div>
            <ol style={{ margin: 0, paddingLeft: '18px', color: 'var(--text-primary)', fontSize: '13px', lineHeight: 1.7 }}>
              {runbook.map((item) => <li key={item}>{item}</li>)}
            </ol>
          </div>
        )}
        {entries.length > 0 ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '8px' }}>
            {entries.map(([key, value]) => (
              <div key={key} style={{ background: 'var(--bg-surface)', borderRadius: '8px', padding: '8px' }}>
                <div style={{ color: 'var(--text-muted)', fontSize: '12px', marginBottom: '4px' }}>
                  {labels[key] || key}
                  <span style={{ marginLeft: '6px', fontFamily: 'monospace', opacity: 0.7 }}>{key}</span>
                </div>
                <code style={{ color: 'var(--text-primary)', fontSize: '12px', wordBreak: 'break-all' }}>
                  {stringifyConfigValue(value) || '-'}
                </code>
              </div>
            ))}
          </div>
        ) : (
          <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>此步骤没有额外配置。</div>
        )}
        <details>
          <summary style={{ cursor: 'pointer', color: 'var(--text-muted)', fontSize: '12px' }}>
            查看原始 JSON（已渲染变量值）
          </summary>
          {(resolvedVars.length > 0 || unresolvedVars.length > 0) && (
            <div style={{ margin: '8px 0 0', display: 'flex', gap: '8px', flexWrap: 'wrap', fontSize: '11px' }}>
              {resolvedVars.map((v) => (
                <span key={v} style={{ padding: '2px 8px', borderRadius: '10px', background: 'var(--success-surface)', color: 'var(--success)' }}>
                  ${'{'}{v}{'}'} → {systemVars[v]}
                </span>
              ))}
              {unresolvedVars.map((v) => (
                <span key={v} style={{ padding: '2px 8px', borderRadius: '10px', background: 'var(--warning-surface)', color: 'var(--warning)' }}>
                  ${'{'}{v}{'}'} 未在系统变量中定义
                </span>
              ))}
            </div>
          )}
          <pre style={{ margin: '8px 0 0', padding: '10px', borderRadius: '8px', background: 'var(--bg-surface)', color: 'var(--text-muted)', fontSize: '12px', overflow: 'auto', maxHeight: '180px' }}>
{JSON.stringify(resolvedConfig, null, 2)}
          </pre>
          <details style={{ marginTop: '6px' }}>
            <summary style={{ cursor: 'pointer', color: 'var(--text-muted)', fontSize: '11px' }}>查看未渲染的原始 JSON</summary>
            <pre style={{ margin: '6px 0 0', padding: '10px', borderRadius: '8px', background: 'var(--bg-surface)', color: 'var(--text-muted)', fontSize: '12px', overflow: 'auto', maxHeight: '180px' }}>
{JSON.stringify(step.config || {}, null, 2)}
            </pre>
          </details>
        </details>
      </div>
    </details>
  )
}

function StepField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div>
      <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '2px' }}>{label}</label>
      <input value={value} onChange={(e) => onChange(e.target.value)} style={{ width: '100%', padding: '4px 8px', fontSize: '13px' }} />
    </div>
  )
}

function BindingField({ label, value, onChange, onChangeMode, onChangeVar,
  mode, boundVar, bindableVars, variables }:
  { label: string; value: string; onChange: (v: string) => void;
      onChangeMode: (m: string) => void; onChangeVar: (v: string) => void;
      mode: string; boundVar: string; bindableVars: string[]; variables: any[] }) {
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2px' }}>
        <label style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{label}</label>
        <select
          value={mode}
          onChange={(e) => onChangeMode(e.target.value)}
          style={{ fontSize: '11px', padding: '1px 4px', background: 'var(--bg-surface)', color: 'var(--text-secondary)', border: '1px solid var(--border-strong)', borderRadius: '4px' }}
        >
          <option value="literal">固定值</option>
          <option value="binding">绑定变量</option>
        </select>
      </div>
      {mode === 'binding' ? (
        <select
          value={boundVar}
          onChange={(e) => { onChangeVar(e.target.value); onChange(e.target.value) }}
          style={{ width: '100%', padding: '4px 8px', fontSize: '13px', background: 'var(--bg-surface)', color: 'var(--text-primary)', border: '1px solid var(--border-strong)', borderRadius: '6px' }}
        >
          <option value="">选择变量…</option>
          {bindableVars.map((v) => {
            const info = variables.find((iv: any) => iv.name === v)
            return <option key={v} value={v}>{info ? info.label : v}</option>
          })}
        </select>
      ) : (
        <input value={value} onChange={(e) => onChange(e.target.value)} style={{ width: '100%', padding: '4px 8px', fontSize: '13px' }} />
      )}
    </div>
  )
}

export default function PipelinePage() {
  const addMessage = useNotificationStore((s) => s.addMessage)
  const [pipelines, setPipelines] = useState<PipelineData[]>([])
  const [templates, setTemplates] = useState<any[]>([])
  const [systems, setSystems] = useState<any[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [pipelineName, setPipelineName] = useState('')
  const [systemName, setSystemName] = useState('')
  const [description, setDescription] = useState('')
  const [strategy, setStrategy] = useState('DIRECT')
  const [steps, setSteps] = useState<StepConfig[]>([])
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [bindingMeta, setBindingMeta] = useState<Record<string, any[]>>({})
  const [registryVars, setRegistryVars] = useState<any[]>([])
  const [fieldModes, setFieldModes] = useState<Record<string, string>>({})
  const [fieldBoundVars, setFieldBoundVars] = useState<Record<string, string>>({})
  const [systemVars, setSystemVars] = useState<Record<string, string>>({})

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [batchMode, setBatchMode] = useState(false)
  const [batchLoading, setBatchLoading] = useState(false)
  const [showBatchEdit, setShowBatchEdit] = useState(false)
  const [batchEditName, setBatchEditName] = useState('')
  const [batchEditSystem, setBatchEditSystem] = useState('')
  const [batchEditDescription, setBatchEditDescription] = useState('')
  const [batchEditStrategy, setBatchEditStrategy] = useState('')
  const [batchProgress, setBatchProgress] = useState<{ current: number; total: number } | null>(null)
  const [pendingRiskDelete, setPendingRiskDelete] = useState<any | null>(null)
  const [riskConfirmValue, setRiskConfirmValue] = useState('')
  const [creatingNew, setCreatingNew] = useState(false)

  useEffect(() => {
    loadPipelines()
    loadBindingMeta()
    pipeline.templates().then((res: any) => setTemplates(res.data || [])).catch(() => {})
    resource.systems().then((res: any) => setSystems(res.data || [])).catch(() => {})
  }, [])

  useEffect(() => {
    if (!systemName) {
      setSystemVars({})
      return
    }
    let cancelled = false
    resource.systems.getVariableInheritance(systemName)
      .then((res: any) => {
        if (cancelled) return
        const chain = res?.data || res || {}
        const flat: Record<string, string> = {}
        Object.entries(chain).forEach(([name, info]: [string, any]) => {
          const v = info && typeof info === 'object' ? info.value : info
          if (v != null && v !== '') flat[name] = typeof v === 'string' ? v : JSON.stringify(v)
        })
        setSystemVars(flat)
      })
      .catch(() => { if (!cancelled) setSystemVars({}) })
    return () => { cancelled = true }
  }, [systemName])

  const loadBindingMeta = async () => {
    try {
      const res: any = await pipelineBinding.metadata()
      setBindingMeta(res.data?.step_fields || {})
      setRegistryVars(res.data?.variables || [])
    } catch { }
  }

  const loadPipelines = useCallback(async () => {
    try {
      const res: any = await pipeline.list()
      setPipelines(res.data || res || [])
    } catch {
      setPipelines([])
    }
  }, [])

  const loadPipeline = async (id: string) => {
    try {
      const res: any = await pipeline.get(id)
      const data = res.data || res
      setSelectedId(id)
      setPipelineName(data.name || '')
      setSystemName(data.system_name || '')
      setDescription(data.description || '')
      setStrategy(data.strategy || 'DIRECT')
      const newSteps: StepConfig[] = []
      const newModes: Record<string, string> = {}
      const newBounds: Record<string, string> = {}
      ;(data.steps || []).forEach((s: any, i: number) => {
        const stepType = s.step_type || s.type || 'command'
        const { config, bindings } = normalizeConfigForEdit(s.config || {})
        newSteps.push({
          id: s.id,
          originalType: stepType,
          type: stepType,
          name: s.name || '',
          config,
        })
        Object.entries(bindings).forEach(([k, v]) => {
          newModes[`${i}:${k}`] = 'binding'
          newBounds[`${i}:${k}`] = v
        })
      })
      setSteps(newSteps)
      setDirty(false)
      setFieldModes(newModes)
      setFieldBoundVars(newBounds)
    } catch {
      setMessage('加载 Pipeline 失败')
    }
  }

  const resetForm = () => {
    setSelectedId(null)
    setPipelineName('')
    setSystemName('')
    setDescription('')
    setStrategy('DIRECT')
    setSteps([])
    setDirty(false)
    setMessage('')
    setFieldModes({})
    setFieldBoundVars({})
    setCreatingNew(false)
  }

  const handleCreateNew = () => {
    setSelectedId(null)
    setPipelineName('')
    setSystemName(systems[0]?.name || '')
    setDescription('')
    setStrategy('DIRECT')
    setSteps([])
    setDirty(false)
    setMessage('')
    setFieldModes({})
    setFieldBoundVars({})
    setCreatingNew(true)
    setTimeout(() => {
      const el = document.getElementById('pipeline-editor-anchor')
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 100)
  }

  const getBindingInfo = (stepIndex: number, fieldKey: string) => {
    const mk = `${stepIndex}:${fieldKey}`
    const mode = fieldModes[mk] || 'literal'
    const boundVar = fieldBoundVars[mk] || ''
    const stepType = steps[stepIndex]?.type || ''
    const bindableFields = bindingMeta[stepType] || []
    const bindableVars = bindableFields.map((b: any) => b.var)
    return { mode, boundVar, bindableVars }
  }

  const createFromTemplate = (templateId: string) => {
    const tpl = templates.find((t: any) => t.id === templateId)
    if (!tpl) return
    const sys = systemName || systems[0]?.name || ''
    setSelectedId(null)
    setPipelineName(tpl.name + ' (副本)')
    setSystemName(sys)
    setDescription(tpl.description || '')
    setStrategy(tpl.strategy || 'DIRECT')
    setSteps(
      (tpl.steps || []).map((s: any) => ({
        type: s.type || 'command',
        name: s.name || '',
        config: { ...(s.config || {}) },
      }))
    )
    setFieldModes({})
    setFieldBoundVars({})
    addMessage('已从模板填充发布流程，请编辑后保存', 'success')
    setTimeout(() => {
      const el = document.getElementById('pipeline-editor-anchor')
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 100)
  }

  const addStep = (type: string) => {
    const def = STEP_TYPES.find((s) => s.type === type)
    setSteps([...steps, { type, name: `${def?.label || type}_${steps.length + 1}`, config: { ...def?.defaults } }])
    setDirty(true)
  }

  const updateStep = (index: number, key: string, value: string) => {
    const next = [...steps]
    next[index] = { ...next[index], config: { ...next[index].config, [key]: value } }
    setSteps(next)
    setDirty(true)
  }

  const updateStepName = (index: number, name: string) => {
    const next = [...steps]
    next[index] = { ...next[index], name }
    setSteps(next)
    setDirty(true)
  }

  const removeStep = (index: number) => {
    setSteps(steps.filter((_, i) => i !== index))
    setDirty(true)
  }

  const moveStep = (index: number, dir: number) => {
    const next = [...steps]
    const target = index + dir
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    setSteps(next)
    setDirty(true)
  }

  const formatStepConfig = (step: StepConfig, stepIndex: number) => {
    const stepType = step.type
    const bindableFields = bindingMeta[stepType] || []
    if (bindableFields.length === 0) return step.config

    const fields: Record<string, any> = {}
    const defaults: Record<string, any> = {}
    const fieldDefaults: Record<string, string> = (STEP_TYPES.find((s) => s.type === stepType)?.defaults as Record<string, string>) || {}

    for (const b of bindableFields) {
      const fkey = b.field
      const mk = `${stepIndex}:${fkey}`
      const mode = fieldModes[mk] || 'literal'
      const bv = fieldBoundVars[mk] || ''
      if (mode === 'binding' && bv) {
        fields[fkey] = { mode: 'binding', var: bv }
        if (fieldDefaults[fkey]) defaults[fkey] = fieldDefaults[fkey]
      } else {
        const val = step.config[fkey] || ''
        fields[fkey] = { mode: 'literal', value: val }
      }
    }

    for (const [k, v] of Object.entries(step.config)) {
      if (!bindableFields.find((b) => b.field === k)) {
        fields[k] = typeof v === 'object' ? v : { mode: 'literal', value: String(v) }
      }
    }

    return { fields, defaults }
  }

  const handleSave = async () => {
    if (!pipelineName || !systemName) {
      setMessage('请填写名称和系统')
      return
    }
    setSaving(true)
    setMessage('')
    try {
      const payloadSteps = steps.map((s, i) => ({
        type: s.type,
        name: s.name,
        config: formatStepConfig(s, i),
        sort_order: i,
      }))

      if (selectedId) {
        await pipeline.update(selectedId, { name: pipelineName, system_name: systemName, description, strategy })

        const existing: any = await pipeline.get(selectedId)
        const existingSteps = (existing.data || existing).steps || []
        const localIds = new Set(steps.filter((s) => s.id).map((s) => s.id as string))

        for (const es of existingSteps) {
          const local = steps.find((s) => s.id === es.id)
          if (!local || local.type !== (es.step_type || es.type)) {
            await pipeline.deleteStep(selectedId, es.id)
          }
        }

        const orderedIds: string[] = []
        for (let i = 0; i < steps.length; i++) {
          const s = steps[i]
          const payload = payloadSteps[i]
          const canPut = s.id && localIds.has(s.id) && s.originalType === s.type
          if (canPut && s.id) {
            await pipeline.updateStep(selectedId, s.id, payload)
            orderedIds.push(s.id)
          } else {
            const addRes: any = await pipeline.addStep(selectedId, payload)
            const newId = addRes?.data?.id || addRes?.id
            if (newId) orderedIds.push(newId)
          }
        }

        if (orderedIds.length > 0) {
          try { await pipeline.reorderSteps(selectedId, orderedIds) } catch { }
        }
        addMessage(`Pipeline "${pipelineName}" 已更新`, 'success')
      } else {
        await pipeline.create({
          name: pipelineName,
          system_name: systemName,
          description,
          strategy,
          steps: payloadSteps,
        })
        addMessage(`Pipeline "${pipelineName}" 已创建`, 'success')
      }
      resetForm()
      await loadPipelines()
    } catch (e: any) {
      const msg = typeof e === 'string' ? e : '保存失败'
      setMessage(msg)
      addMessage(msg, 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleDuplicate = async (id: string, name: string) => {
    const newName = prompt('请输入新名称', name + ' (副本)')
    if (!newName) return
    try {
      await pipeline.duplicate(id, newName)
      await loadPipelines()
      addMessage(`已复制: ${newName}`, 'success')
    } catch {
      addMessage('复制失败', 'error')
    }
  }

  const toggleSelectAll = () => {
    if (selectedIds.size === pipelines.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(pipelines.map((p) => p.id)))
    }
  }

  const handleBatchDelete = () => {
    if (selectedIds.size === 0) return
    setRiskConfirmValue('')
    setPendingRiskDelete({
      kind: 'batch',
      ids: Array.from(selectedIds),
      title: '确认批量删除 Pipeline',
      description: '批量删除不可撤销，依赖这些流程的发布配置可能无法继续使用。',
      target: `${selectedIds.size} 个 Pipeline`,
      confirmText: `DELETE ${selectedIds.size} PIPELINES`,
      confirmButtonLabel: '确认批量删除',
      riskLevel: 'critical',
    })
  }

  const confirmPipelineDelete = async () => {
    const action = pendingRiskDelete
    if (!action) return
    setPendingRiskDelete(null)
    setRiskConfirmValue('')
    setBatchLoading(true)
    setBatchProgress({ current: 0, total: action.ids.length })
    let success = 0
    let failed = 0
    for (let i = 0; i < action.ids.length; i++) {
      setBatchProgress({ current: i + 1, total: action.ids.length })
      try {
        await pipeline.delete(action.ids[i])
        if (selectedId === action.ids[i]) resetForm()
        success++
      } catch {
        failed++
      }
    }
    setSelectedIds(new Set())
    await loadPipelines()
    setBatchLoading(false)
    setBatchProgress(null)
    addMessage(`批量删除完成: 成功 ${success}, 失败 ${failed}`, failed > 0 ? 'error' : 'success')
  }

  const handleBatchDuplicate = async () => {
    if (selectedIds.size === 0) return
    setBatchLoading(true)
    setBatchProgress({ current: 0, total: selectedIds.size })
    let success = 0
    let failed = 0
    const ids = Array.from(selectedIds)
    for (let i = 0; i < ids.length; i++) {
      setBatchProgress({ current: i + 1, total: ids.length })
      const p = pipelines.find((pp) => pp.id === ids[i])
      try {
        await pipeline.duplicate(ids[i], (p?.name || ids[i]) + ' (副本)')
        success++
      } catch {
        failed++
      }
    }
    await loadPipelines()
    setBatchLoading(false)
    setBatchProgress(null)
    addMessage(`批量复制完成: 成功 ${success}, 失败 ${failed}`, failed > 0 ? 'error' : 'success')
  }

  const handleBatchEditSave = async () => {
    if (selectedIds.size === 0) return
    const updates: Record<string, string> = {}
    if (batchEditName) updates.name = batchEditName
    if (batchEditSystem) updates.system_name = batchEditSystem
    if (batchEditDescription) updates.description = batchEditDescription
    if (batchEditStrategy) updates.strategy = batchEditStrategy
    if (Object.keys(updates).length === 0) return

    setBatchLoading(true)
    setBatchProgress({ current: 0, total: selectedIds.size })
    try {
      const ids = Array.from(selectedIds)
      const res: any = await pipeline.batchUpdate(ids, updates)
      const result = res.data || res
      const success = result.success ?? 0
      const failed = result.failed ?? 0
      setSelectedIds(new Set())
      setShowBatchEdit(false)
      setBatchEditName('')
      setBatchEditSystem('')
      setBatchEditDescription('')
      setBatchEditStrategy('')
      await loadPipelines()
      addMessage(`批量编辑完成: 成功 ${success}, 失败 ${failed}`, failed > 0 ? 'error' : 'success')
    } catch (e: any) {
      const msg = typeof e === 'string' ? e : (e?.message || '批量编辑失败')
      addMessage(msg, 'error')
    } finally {
      setBatchLoading(false)
      setBatchProgress(null)
    }
  }

  const exitBatchMode = () => {
    setBatchMode(false)
    setSelectedIds(new Set())
    setShowBatchEdit(false)
    setBatchEditName('')
    setBatchEditSystem('')
    setBatchEditDescription('')
    setBatchEditStrategy('')
    setBatchProgress(null)
  }

  return (
    <div style={{ display: 'grid', gap: '20px' }}>
      <PageHeader
        title="流程管理"
        description={`共 ${pipelines.length} 条流水线`}
        breadcrumbs={[
          { label: '配置管理', href: '/pipeline' },
          { label: '流程列表' },
        ]}
      />
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
        <h2 style={{ margin: 0 }}>流程管理 ({pipelines.length})</h2>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
          {batchMode ? (
            <>
              <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                已选 {selectedIds.size}/{pipelines.length}
              </span>
              <button className="btn" onClick={toggleSelectAll}
                style={{ padding: '4px 12px', fontSize: '13px', background: 'var(--border-strong)', color: 'var(--text-primary)' }}>
                {selectedIds.size === pipelines.length ? '取消全选' : '全选'}
              </button>
              <button className="btn" onClick={() => setShowBatchEdit(true)}
                disabled={selectedIds.size === 0}
                style={{ padding: '4px 12px', fontSize: '13px', background: 'var(--action-bg)', color: 'var(--action-text)', opacity: selectedIds.size === 0 ? 0.5 : 1 }}>
                批量编辑
              </button>
              <button className="btn" onClick={handleBatchDuplicate}
                disabled={selectedIds.size === 0 || batchLoading}
                style={{ padding: '4px 12px', fontSize: '13px', background: 'var(--success-border)', color: 'var(--success)', opacity: selectedIds.size === 0 ? 0.5 : 1 }}>
                {batchLoading ? '处理中...' : '批量复制'}
              </button>
              <button className="btn" onClick={handleBatchDelete}
                disabled={selectedIds.size === 0 || batchLoading}
                style={{ padding: '4px 12px', fontSize: '13px', background: 'var(--danger-surface)', color: 'var(--danger)', opacity: selectedIds.size === 0 ? 0.5 : 1 }}>
                {batchLoading ? '处理中...' : '批量删除'}
              </button>
              <button className="btn" onClick={exitBatchMode}
                style={{ padding: '4px 12px', fontSize: '13px', background: 'var(--border-strong)', color: 'var(--text-secondary)' }}>
                退出批量
              </button>
            </>
          ) : (
            <>
              <button className="btn btn-success" onClick={handleCreateNew}>
                + 新建
              </button>
              {templates.length > 0 && (
                <div style={{ position: 'relative', display: 'inline-block' }}>
                  <button className="btn" style={{ padding: '6px 14px', background: 'var(--action-bg)', color: 'var(--action-text)' }}
                    onClick={(e) => {
                      const menu = e.currentTarget.nextElementSibling as HTMLElement
                      menu.style.display = menu.style.display === 'block' ? 'none' : 'block'
                    }}>
                    📋 从模板创建 ▾
                  </button>
                  <div style={{ display: 'none', position: 'absolute', top: '100%', left: 0, zIndex: 100, background: 'var(--bg-surface)', border: '1px solid var(--border-strong)', borderRadius: '8px', minWidth: 260, marginTop: 4 }}>
                    {templates.map((t: any) => (
                      <button key={t.id} className="btn" onClick={(e) => { createFromTemplate(t.id); (e.currentTarget.parentElement as HTMLElement).style.display = 'none' }}
                        style={{ display: 'block', width: '100%', textAlign: 'left', padding: '10px 14px', background: 'transparent', color: 'var(--text-primary)', borderBottom: '1px solid var(--bg-surface)' }}>
                        <div style={{ fontWeight: 'bold' }}>{t.name}</div>
                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: 2 }}>{t.description}</div>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <button className="btn" onClick={() => setBatchMode(true)}
                style={{ padding: '6px 14px', background: 'var(--border-strong)', color: 'var(--text-primary)' }}>
                批量操作
              </button>
            </>
          )}
        </div>
      </div>

      {batchLoading && (
        <div style={{
          padding: '10px 16px', background: 'var(--warning-surface)', borderRadius: '8px',
          display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', color: 'var(--warning)',
        }}>
          <span style={{ animation: 'pulse 1s infinite' }}>⏳</span>
          {batchProgress
            ? `批量操作执行中 (${batchProgress.current}/${batchProgress.total})，请稍候...`
            : '批量操作执行中，请稍候...'}
        </div>
      )}

      <PipelineList
        pipelines={pipelines}
        systemFilter={systemName}
        onSystemFilter={setSystemName}
        onSelect={(p) => loadPipeline(p.id)}
        onDelete={(id, name) => {
          setPendingRiskDelete({
            kind: 'single',
            ids: [id],
            title: '确认删除 Pipeline',
            description: '删除后依赖该流程的发布配置可能无法继续使用。',
            target: name,
            confirmText: `DELETE PIPELINE ${name}`,
            confirmButtonLabel: '确认删除',
            riskLevel: 'high',
          })
          setRiskConfirmValue('')
        }}
        onDuplicate={(p) => handleDuplicate(p.id, p.name)}
        loading={false}
      />

      {templates.length > 0 && (
        <PipelineTemplatePanel
          templates={templates}
          onImport={(tpl) => createFromTemplate(tpl.id)}
          onExport={() => {}}
        />
      )}

      {showBatchEdit && selectedIds.size > 0 && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
        }} onClick={(e) => { if (e.target === e.currentTarget) setShowBatchEdit(false) }}>
          <div className="card" style={{ width: '500px', maxWidth: '90vw' }}>
            <h3 style={{ margin: '0 0 16px 0' }}>批量编辑 ({selectedIds.size} 个流程)</h3>
            <p style={{ color: 'var(--text-muted)', fontSize: '13px', margin: '0 0 16px 0' }}>
              仅修改下方填写的字段，留空表示不修改
            </p>
            <div style={{ display: 'grid', gap: '12px' }}>
              <div>
                <label style={{ display: 'block', color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '4px' }}>名称</label>
                <input value={batchEditName} onChange={(e) => setBatchEditName(e.target.value)}
                  placeholder="留空不修改"
                  style={{ width: '100%', padding: '8px 12px', background: 'var(--bg-page)', color: 'var(--text-primary)', border: '1px solid var(--border-strong)', borderRadius: '6px' }} />
              </div>
              <div>
                <label style={{ display: 'block', color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '4px' }}>系统名称</label>
                <select value={batchEditSystem} onChange={(e) => setBatchEditSystem(e.target.value)}
                  style={{ width: '100%', padding: '8px 12px', background: 'var(--bg-page)', color: 'var(--text-primary)', border: '1px solid var(--border-strong)', borderRadius: '6px' }}>
                  <option value="">-- 不修改 --</option>
                  {systems.map((s) => (
                    <option key={s.name} value={s.name}>{s.display_name || s.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label style={{ display: 'block', color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '4px' }}>描述</label>
                <input value={batchEditDescription} onChange={(e) => setBatchEditDescription(e.target.value)}
                  placeholder="留空不修改"
                  style={{ width: '100%', padding: '8px 12px', background: 'var(--bg-page)', color: 'var(--text-primary)', border: '1px solid var(--border-strong)', borderRadius: '6px' }} />
              </div>
              <div>
                <label style={{ display: 'block', color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '4px' }}>部署策略</label>
                <select value={batchEditStrategy} onChange={(e) => setBatchEditStrategy(e.target.value)}
                  style={{ width: '100%', padding: '8px 12px', background: 'var(--bg-page)', color: 'var(--text-primary)', border: '1px solid var(--border-strong)', borderRadius: '6px' }}>
                  <option value="">-- 不修改 --</option>
                  {STRATEGIES.map((s) => (
                    <option key={s.value} value={s.value}>{s.label}</option>
                  ))}
                </select>
              </div>
              <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end', marginTop: '4px' }}>
                <button className="btn" onClick={() => setShowBatchEdit(false)}
                  style={{ padding: '8px 16px', background: 'var(--border-strong)', color: 'var(--text-primary)', fontSize: '13px' }}>
                  取消
                </button>
                <button className="btn" onClick={handleBatchEditSave}
                  disabled={batchLoading || (!batchEditName && !batchEditSystem && !batchEditDescription && !batchEditStrategy)}
                  style={{
                    padding: '8px 16px', background: 'var(--success-border)', color: 'var(--success)', fontSize: '13px', fontWeight: 'bold',
                    opacity: (!batchEditName && !batchEditSystem && !batchEditDescription && !batchEditStrategy) ? 0.5 : 1,
                  }}>
                  {batchLoading ? '保存中...' : '应用修改'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      <div id="pipeline-editor-anchor" />
      {(selectedId || steps.length > 0 || creatingNew) && !batchMode && (
        <div className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
            <h2 style={{ margin: 0 }}>
              {selectedId ? `编辑 Pipeline: ${pipelineName}` : '新建 Pipeline'}
              {dirty && <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--warning)', fontWeight: 600, background: 'var(--warning-surface)', padding: '2px 8px', borderRadius: 4 }}>未保存</span>}
            </h2>
            <button className="btn" onClick={resetForm}
              style={{ padding: '4px 12px', fontSize: '13px', background: 'var(--border-strong)', color: 'var(--text-secondary)' }}>
              关闭编辑
            </button>
          </div>
          <div className="responsive-grid-3" style={{ gap: '12px', marginBottom: '16px' }}>
            <div>
              <label style={{ display: 'block', marginBottom: '4px', fontSize: '13px', color: 'var(--text-secondary)' }}>名称</label>
              <input value={pipelineName} onChange={(e) => setPipelineName(e.target.value)} placeholder="generic-deploy" style={{ width: '100%' }} />
            </div>
            <div>
              <label style={{ display: 'block', marginBottom: '4px', fontSize: '13px', color: 'var(--text-secondary)' }}>系统</label>
              <select value={systemName} onChange={(e) => setSystemName(e.target.value)}
                style={{ width: '100%', padding: '8px 12px' }}>
                <option value="">-- 选择 --</option>
                {systems.map((s) => (
                  <option key={s.name} value={s.name}>{s.display_name || s.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label style={{ display: 'block', marginBottom: '4px', fontSize: '13px', color: 'var(--text-secondary)' }}>策略</label>
              <select value={strategy} onChange={(e) => setStrategy(e.target.value)}
                style={{ width: '100%', padding: '8px 12px' }}>
                {STRATEGIES.map((s) => (
                  <option key={s.value} value={s.value}>{s.label}</option>
                ))}
              </select>
            </div>
          </div>
          <div style={{ marginBottom: '16px' }}>
            <label style={{ display: 'block', marginBottom: '4px', fontSize: '13px', color: 'var(--text-secondary)' }}>描述</label>
            <input value={description} onChange={(e) => setDescription(e.target.value)}
              placeholder="Pipeline 用途说明（可选）" style={{ width: '100%' }} />
          </div>

          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '16px' }}>
            {STEP_TYPES.map((s) => (
              <button key={s.type} className="btn" onClick={() => addStep(s.type)} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)' }}>
                + {s.label}
              </button>
            ))}
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {steps.map((step, i) => (
              <div key={i} style={{ background: 'var(--bg-page)', borderRadius: '8px', padding: '12px', border: '1px solid var(--border-strong)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{ color: 'var(--brand)', fontWeight: 'bold' }}>#{i + 1}</span>
                    <input
                      value={step.name}
                      onChange={(e) => updateStepName(i, e.target.value)}
                      style={{ background: 'transparent', border: 'none', color: 'var(--text-primary)', fontWeight: 'bold', padding: 0 }}
                    />
                    <span style={{ color: 'var(--text-muted)', fontSize: '12px' }}>({step.type})</span>
                  </div>
                  <div style={{ display: 'flex', gap: '4px' }}>
                    <button className="btn" onClick={() => moveStep(i, -1)} style={{ padding: '4px 8px', background: 'var(--border-strong)', color: 'var(--text-primary)' }}>↑</button>
                    <button className="btn" onClick={() => moveStep(i, 1)} style={{ padding: '4px 8px', background: 'var(--border-strong)', color: 'var(--text-primary)' }}>↓</button>
                    <button className="btn btn-danger" onClick={() => removeStep(i)} style={{ padding: '4px 8px' }}>删除</button>
                  </div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '8px' }}>
                  {step.type === 'checkout' && (
                    <>
                      <BindingField label="仓库地址" value={step.config.repo_url || ''}
                        onChange={(v) => updateStep(i, 'repo_url', v)}
                        onChangeMode={(m) => { const nm = { ...fieldModes, [`${i}:repo_url`]: m }; setFieldModes(nm) }}
                        onChangeVar={(v) => { const nv = { ...fieldBoundVars, [`${i}:repo_url`]: v }; setFieldBoundVars(nv) }}
                        {...getBindingInfo(i, 'repo_url')}
                        variables={registryVars} />
                      <BindingField label="分支" value={step.config.branch || 'main'}
                        onChange={(v) => updateStep(i, 'branch', v)}
                        onChangeMode={(m) => { const nm = { ...fieldModes, [`${i}:branch`]: m }; setFieldModes(nm) }}
                        onChangeVar={(v) => { const nv = { ...fieldBoundVars, [`${i}:branch`]: v }; setFieldBoundVars(nv) }}
                        {...getBindingInfo(i, 'branch')}
                        variables={registryVars} />
                    </>
                  )}
                  {step.type === 'build' && (
                    <>
                      <BindingField label="构建命令" value={step.config.cmd || ''}
                        onChange={(v) => updateStep(i, 'cmd', v)}
                        onChangeMode={(m) => { const nm = { ...fieldModes, [`${i}:cmd`]: m }; setFieldModes(nm) }}
                        onChangeVar={(v) => { const nv = { ...fieldBoundVars, [`${i}:cmd`]: v }; setFieldBoundVars(nv) }}
                        {...getBindingInfo(i, 'cmd')}
                        variables={registryVars} />
                      <StepField label="超时(秒)" value={step.config.timeout || '300'} onChange={(v) => updateStep(i, 'timeout', v)} />
                    </>
                  )}
                  {step.type === 'upload' && (
                    <>
                      <BindingField label="本地路径" value={step.config.local_path || ''}
                        onChange={(v) => updateStep(i, 'local_path', v)}
                        onChangeMode={(m) => { const nm = { ...fieldModes, [`${i}:local_path`]: m }; setFieldModes(nm) }}
                        onChangeVar={(v) => { const nv = { ...fieldBoundVars, [`${i}:local_path`]: v }; setFieldBoundVars(nv) }}
                        {...getBindingInfo(i, 'local_path')}
                        variables={registryVars} />
                      <BindingField label="远程路径" value={step.config.remote_path || ''}
                        onChange={(v) => updateStep(i, 'remote_path', v)}
                        onChangeMode={(m) => { const nm = { ...fieldModes, [`${i}:remote_path`]: m }; setFieldModes(nm) }}
                        onChangeVar={(v) => { const nv = { ...fieldBoundVars, [`${i}:remote_path`]: v }; setFieldBoundVars(nv) }}
                        {...getBindingInfo(i, 'remote_path')}
                        variables={registryVars} />
                    </>
                  )}
                  {step.type === 'deploy' && (
                    <>
                      <BindingField label="部署路径" value={step.config.deploy_path || ''}
                        onChange={(v) => updateStep(i, 'deploy_path', v)}
                        onChangeMode={(m) => { const nm = { ...fieldModes, [`${i}:deploy_path`]: m }; setFieldModes(nm) }}
                        onChangeVar={(v) => { const nv = { ...fieldBoundVars, [`${i}:deploy_path`]: v }; setFieldBoundVars(nv) }}
                        {...getBindingInfo(i, 'deploy_path')}
                        variables={registryVars} />
                      <BindingField label="包路径" value={step.config.package_path || ''}
                        onChange={(v) => updateStep(i, 'package_path', v)}
                        onChangeMode={(m) => { const nm = { ...fieldModes, [`${i}:package_path`]: m }; setFieldModes(nm) }}
                        onChangeVar={(v) => { const nv = { ...fieldBoundVars, [`${i}:package_path`]: v }; setFieldBoundVars(nv) }}
                        {...getBindingInfo(i, 'package_path')}
                        variables={registryVars} />
                    </>
                  )}
                  {step.type === 'switch' && (
                    <div style={{ color: 'var(--text-muted)', fontSize: '13px', gridColumn: '1 / -1' }}>
                      无需额外配置 — 自动切换到新版本
                    </div>
                  )}
                  {step.type === 'restart' && (
                    <BindingField label="重启命令" value={step.config.cmd || ''}
                      onChange={(v) => updateStep(i, 'cmd', v)}
                      onChangeMode={(m) => { const nm = { ...fieldModes, [`${i}:cmd`]: m }; setFieldModes(nm) }}
                      onChangeVar={(v) => { const nv = { ...fieldBoundVars, [`${i}:cmd`]: v }; setFieldBoundVars(nv) }}
                      {...getBindingInfo(i, 'cmd')}
                      variables={registryVars} />
                  )}
                  {step.type === 'health_check' && (
                    <>
                      <BindingField label="检查URL" value={step.config.url || ''}
                        onChange={(v) => updateStep(i, 'url', v)}
                        onChangeMode={(m) => { const nm = { ...fieldModes, [`${i}:url`]: m }; setFieldModes(nm) }}
                        onChangeVar={(v) => { const nv = { ...fieldBoundVars, [`${i}:url`]: v }; setFieldBoundVars(nv) }}
                        {...getBindingInfo(i, 'url')}
                        variables={registryVars} />
                      <StepField label="重试次数" value={step.config.retries || '3'} onChange={(v) => updateStep(i, 'retries', v)} />
                    </>
                  )}
                  {step.type === 'command' && (
                    <>
                      <StepField label="命令" value={step.config.cmd || ''} onChange={(v) => updateStep(i, 'cmd', v)} />
                      <StepField label="超时(秒)" value={step.config.timeout || '120'} onChange={(v) => updateStep(i, 'timeout', v)} />
                    </>
                  )}
                  {step.type === 'wait' && (
                    <StepField label="等待秒数" value={step.config.seconds || '5'} onChange={(v) => updateStep(i, 'seconds', v)} />
                  )}
                  {(step.type === 'dovo_bluegreen_update' || step.type === 'scripted_service_update' || step.type === 'web_script_update' || step.type === 'docker_compose_update') && (() => {
                    const def = STEP_TYPES.find((s) => s.type === step.type)
                    const labels = STEP_FIELD_LABELS[step.type] || {}
                    const bindable = bindingMeta[step.type] || []
                    const keys = Object.keys({ ...(def?.defaults || {}), ...(step.config || {}) })
                      .filter((k) => k !== 'fields' && k !== 'defaults')
                    return (
                      <>
                        {keys.map((key) => {
                          const label = labels[key] || key
                          const isBindable = bindable.some((b: any) => b.field === key)
                          const rawVal = step.config[key]
                          const val = typeof rawVal === 'string' || typeof rawVal === 'number' ? String(rawVal) : (rawVal == null ? '' : JSON.stringify(rawVal))
                          if (isBindable) {
                            return (
                              <BindingField key={key} label={label} value={val}
                                onChange={(v) => updateStep(i, key, v)}
                                onChangeMode={(m) => { const nm = { ...fieldModes, [`${i}:${key}`]: m }; setFieldModes(nm) }}
                                onChangeVar={(v) => { const nv = { ...fieldBoundVars, [`${i}:${key}`]: v }; setFieldBoundVars(nv) }}
                                {...getBindingInfo(i, key)}
                                variables={registryVars} />
                            )
                          }
                          return (
                            <StepField key={key} label={label} value={val}
                              onChange={(v) => updateStep(i, key, v)} />
                          )
                        })}
                      </>
                    )
                  })()}
                  <StepExecutionPreview step={step} systemVars={systemVars} />
                </div>
              </div>
            ))}
          </div>

          {steps.length > 0 && (
            <div style={{ marginTop: '16px', display: 'flex', gap: '12px', alignItems: 'center' }}>
              <button className="btn btn-success" onClick={handleSave} disabled={saving}>
                {saving ? '保存中...' : (selectedId ? '更新 Pipeline' : '创建 Pipeline')}
              </button>
              {message && <span style={{ color: message.includes('失败') ? 'var(--danger-solid)' : 'var(--success-solid)', fontSize: '14px' }}>{message}</span>}
            </div>
          )}
        </div>
      )}

      <RiskConfirmDialog
        open={Boolean(pendingRiskDelete)}
        title={pendingRiskDelete?.title || '确认删除 Pipeline'}
        description={pendingRiskDelete?.description}
        target={pendingRiskDelete?.target || '-'}
        confirmText={pendingRiskDelete?.confirmText || ''}
        value={riskConfirmValue}
        onValueChange={setRiskConfirmValue}
        onCancel={() => { setPendingRiskDelete(null); setRiskConfirmValue('') }}
        onConfirm={confirmPipelineDelete}
        riskLevel={pendingRiskDelete?.riskLevel || 'high'}
        details={[
          { label: '当前系统', value: systemName || '-' },
          { label: '选中数量', value: pendingRiskDelete?.kind === 'batch' ? String(pendingRiskDelete?.ids?.length || 0) : '1' },
        ]}
        confirmButtonLabel={pendingRiskDelete?.confirmButtonLabel || '确认删除'}
        confirmMode="one-click"
      />
    </div>
  )
}
