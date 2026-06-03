import type { ReactNode } from 'react'
import { Zap, ShieldAlert, Pencil, Layers } from 'lucide-react'

export function ToolOverviewPanel({
  stats,
  actions,
}: {
  stats: Array<{ label: string; value: ReactNode; tone?: 'success' | 'warning' | 'danger' }>
  actions?: ReactNode
}) {
  const icons = [Layers, Zap, ShieldAlert, Pencil]
  const toneBorder = {
    success: '1px solid var(--success-border)',
    warning: '1px solid var(--warning-border)',
    danger: '1px solid var(--danger-border)',
  }
  const toneBg = {
    success: 'var(--success-surface)',
    warning: 'var(--warning-surface)',
    danger: 'var(--danger-surface)',
  }
  const toneColor = {
    success: 'var(--success)',
    warning: 'var(--warning)',
    danger: 'var(--danger)',
  }

  return (
    <div className="card" style={{ display: 'grid', gap: 12 }}>
      <div className="card-header">
        <h2>概览与访问</h2>
      </div>
      <div className="tool-overview-grid">
        {stats.map((s, i) => {
          const Icon = icons[i % icons.length]
          return (
            <div
              key={i}
              className="tool-overview-stat"
              style={{
                border: toneBorder[s.tone as keyof typeof toneBorder] || '1px solid var(--border)',
                background: toneBg[s.tone as keyof typeof toneBg] || 'var(--bg-page)',
              }}
            >
              <div className="tool-overview-stat-icon" style={{ color: toneColor[s.tone as keyof typeof toneColor] || 'var(--brand)' }}>
                <Icon size={18} />
              </div>
              <div className="tool-overview-stat-body">
                <span className="tool-overview-stat-label">{s.label}</span>
                <span className="tool-overview-stat-value" style={{ color: toneColor[s.tone as keyof typeof toneColor] || 'var(--text-strong)' }}>
                  {s.value}
                </span>
              </div>
            </div>
          )
        })}
      </div>
      {actions && <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>{actions}</div>}
    </div>
  )
}
