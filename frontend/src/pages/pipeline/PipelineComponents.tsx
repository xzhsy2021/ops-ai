import type { PipelineData, StepConfig } from './pipelineConfig'
import {
  STEP_TYPES,
  STRATEGIES,
  BUILTIN_STEP_TYPES,
  STEP_FIELD_LABELS,
  STEP_RUNBOOKS,
  normalizeConfigForView,
  resolvePlaceholdersInValue,
  collectUsedVarNames,
} from './pipelineConfig'

function stringifyConfigValue(value: any) {
  if (Array.isArray(value)) return value.join(', ')
  if (value && typeof value === 'object') return JSON.stringify(value)
  return String(value ?? '')
}

export function PipelineStepPreview({
  step,
  systemVars,
}: {
  step: StepConfig
  systemVars: Record<string, string>
}) {
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
                  $&#123;{v}&#125; → {systemVars[v]}
                </span>
              ))}
              {unresolvedVars.map((v) => (
                <span key={v} style={{ padding: '2px 8px', borderRadius: '10px', background: 'var(--warning-surface)', color: 'var(--warning)' }}>
                  $&#123;{v}&#125; 未在系统变量中定义
                </span>
              ))}
            </div>
          )}
          <pre style={{ margin: '8px 0 0', padding: '10px', borderRadius: '8px', background: 'var(--bg-surface)', color: 'var(--text-muted)', fontSize: '12px', overflow: 'auto', maxHeight: '180px' }}>
            {JSON.stringify(resolvedConfig, null, 2)}
          </pre>
        </details>
      </div>
    </details>
  )
}

export function PipelineList({
  pipelines,
  systemFilter,
  onSystemFilter,
  onSelect,
  onDelete,
  onDuplicate,
  loading,
}: {
  pipelines: PipelineData[]
  systemFilter: string
  onSystemFilter: (v: string) => void
  onSelect: (p: PipelineData) => void
  onDelete: (id: string, name: string) => void
  onDuplicate: (p: PipelineData) => void
  loading?: boolean
}) {
  const systems = [...new Set(pipelines.map((p) => p.system_name).filter(Boolean))].sort()
  const filtered = systemFilter
    ? pipelines.filter((p) => p.system_name === systemFilter)
    : pipelines

  return (
    <div className="card" style={{ display: 'grid', gap: 12 }}>
      <div className="card-header">
        <h2>流水线列表</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          {systems.length > 0 && (
            <select value={systemFilter} onChange={(e) => onSystemFilter(e.target.value)}>
              <option value="">全部系统</option>
              {systems.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          )}
        </div>
      </div>
      {loading && pipelines.length === 0 && (
        <div style={{ padding: 24, color: 'var(--text-muted)', textAlign: 'center' }}>加载中...</div>
      )}
      {!loading && filtered.length === 0 && (
        <div style={{ padding: 24, color: 'var(--text-muted)', textAlign: 'center' }}>
          {systemFilter ? `系统 "${systemFilter}" 暂无流水线` : '暂无流水线'}
        </div>
      )}
      {filtered.map((p) => (
        <div key={p.id} className="pipeline-card" onClick={() => onSelect(p)}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <strong style={{ color: 'var(--text-primary)' }}>{p.name}</strong>
            <small style={{ color: 'var(--text-muted)' }}>
              {p.system_name} · {STRATEGIES.find((s) => s.value === p.strategy)?.label || p.strategy} · {(p.steps || []).length} 步骤
            </small>
            {p.description && <p style={{ color: 'var(--text-secondary)', fontSize: 13, margin: '4px 0 0' }}>{p.description}</p>}
          </div>
          <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
            <button className="btn btn-subtle" onClick={(e) => { e.stopPropagation(); onDuplicate(p) }}>复制</button>
            <button className="btn btn-subtle btn-danger" onClick={(e) => { e.stopPropagation(); onDelete(p.id, p.name) }}>删除</button>
          </div>
        </div>
      ))}
    </div>
  )
}

export function PipelineEditor({
  pipeline,
  systemVars,
  onSave,
  onBack,
  saving,
}: {
  pipeline: PipelineData
  systemVars: Record<string, string>
  onSave: (p: PipelineData) => void
  onBack: () => void
  saving?: boolean
}) {
  return (
    <div className="card" style={{ display: 'grid', gap: 12 }}>
      <div className="card-header">
        <div>
          <button className="btn btn-subtle" onClick={onBack}>← 返回列表</button>
          <h2 style={{ margin: '8px 0 0' }}>{pipeline.name}</h2>
        </div>
        <button className="btn btn-primary" onClick={() => onSave(pipeline)} disabled={saving}>
          {saving ? '保存中...' : '保存流水线'}
        </button>
      </div>
      <div style={{ color: 'var(--text-muted)', fontSize: 13 }}>
        {pipeline.system_name} · {STRATEGIES.find((s) => s.value === pipeline.strategy)?.label || pipeline.strategy}
        {pipeline.description && <span> · {pipeline.description}</span>}
      </div>
      <div style={{ display: 'grid', gap: 8 }}>
        {pipeline.steps.map((step, i) => (
          <div key={step.id || i} style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 10, background: 'var(--bg-page)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <strong style={{ color: 'var(--text-primary)' }}>
                {i + 1}. {step.name || STEP_TYPES.find((t) => t.type === step.type)?.label || step.type}
              </strong>
              <span className="tag">{STEP_TYPES.find((t) => t.type === step.type)?.label || step.type}</span>
            </div>
            <PipelineStepPreview step={step} systemVars={systemVars} />
          </div>
        ))}
      </div>
    </div>
  )
}

export function PipelineTemplatePanel({
  templates,
  onImport,
  onExport,
}: {
  templates: PipelineData[]
  onImport: (template: PipelineData) => void
  onExport: () => void
}) {
  return (
    <div className="card" style={{ display: 'grid', gap: 8 }}>
      <div className="card-header">
        <h3>流水线模板</h3>
        <button className="btn btn-subtle" onClick={onExport}>导出当前</button>
      </div>
      {templates.length === 0 && (
        <div style={{ padding: 16, color: 'var(--text-muted)', textAlign: 'center', fontSize: 13 }}>
          暂无模板。保存已有流水线后可在此复用。
        </div>
      )}
      {templates.map((t) => (
        <div key={t.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, padding: '8px 10px', border: '1px solid var(--border)', borderRadius: 8, background: 'var(--bg-page)' }}>
          <div>
            <strong style={{ color: 'var(--text-primary)', fontSize: 13 }}>{t.name}</strong>
            <small style={{ color: 'var(--text-muted)', display: 'block', fontSize: 12 }}>{t.system_name} · {(t.steps || []).length} 步骤</small>
          </div>
          <button className="btn btn-subtle" onClick={() => onImport(t)}>导入</button>
        </div>
      ))}
    </div>
  )
}