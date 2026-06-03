import { TriangleAlert, CircleX, CircleCheck } from 'lucide-react'

type PolicyRule = {
  risk: string
  action: 'confirm' | 'block' | 'allow'
  description: string
  affected_tools?: string[]
}

export function ToolRiskPolicyPanel({ rules }: { rules: PolicyRule[] }) {
  const blocked = rules.filter((r) => r.action === 'block')
  const confirm = rules.filter((r) => r.action === 'confirm')
  const allowed = rules.filter((r) => r.action === 'allow')

  const config = {
    block: { icon: CircleX, label: '阻断', color: 'var(--danger)', bg: 'var(--danger-surface)', border: 'var(--danger-border)' },
    confirm: { icon: TriangleAlert, label: '需确认', color: 'var(--warning)', bg: 'var(--warning-surface)', border: 'var(--warning-border)' },
    allow: { icon: CircleCheck, label: '允许', color: 'var(--success)', bg: 'var(--success-surface)', border: 'var(--success-border)' },
  }

  return (
    <div className="card" style={{ display: 'grid', gap: 12 }}>
      <div className="card-header">
        <div>
          <h2>风险策略</h2>
          <span>{rules.length} 条规则 · 按风险等级自动分级管控</span>
        </div>
      </div>
      <div className="tool-risk-summary">
        {([
          { key: 'block', items: blocked },
          { key: 'confirm', items: confirm },
          { key: 'allow', items: allowed },
        ] as const).map(({ key, items }) => {
          const c = config[key]
          const Icon = c.icon
          return (
            <div
              key={key}
              className="tool-risk-badge"
              style={{
                borderColor: c.border,
                background: c.bg,
                color: c.color,
              }}
            >
              <Icon size={14} />
              <span>{c.label}</span>
              <strong>{items.length}</strong>
            </div>
          )
        })}
      </div>
      <div className="tool-risk-rules">
        {rules.map((rule, i) => {
          const c = config[rule.action]
          const Icon = c.icon
          return (
            <div key={i} className="tool-risk-rule">
              <div className="tool-risk-rule-icon" style={{ color: c.color, background: c.bg }}>
                <Icon size={14} />
              </div>
              <div className="tool-risk-rule-body">
                <div className="tool-risk-rule-title">
                  <span>{rule.description}</span>
                  <span className="tool-risk-rule-tag" style={{ color: c.color, background: c.bg }}>
                    {rule.risk}
                  </span>
                </div>
                {rule.affected_tools && rule.affected_tools.length > 0 && (
                  <small style={{ color: 'var(--text-muted)' }}>
                    影响：{rule.affected_tools.slice(0, 4).join(', ')}
                    {rule.affected_tools.length > 4 && ` 等 ${rule.affected_tools.length} 个工具`}
                  </small>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
