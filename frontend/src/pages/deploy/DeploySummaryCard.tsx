import type { ReactNode } from 'react'

export type DeploySummaryItem = {
  label: string
  value: ReactNode
  tone?: 'danger' | 'warning' | 'default'
}

export function DeploySummaryCard({
  title = '发布摘要',
  items,
  extra,
}: {
  title?: string
  items: DeploySummaryItem[]
  extra?: ReactNode
}) {
  return (
    <div className="deploy-summary-card">
      <div className="section-title-row section-title-row--compact" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <h2 style={{ margin: 0 }}>{title}</h2>
        {extra}
      </div>
      <div className="deploy-summary-grid">
        {items.map((item, i) => (
          <div
            key={i}
            className={`deploy-summary-item${item.tone === 'danger' ? ' deploy-summary-item--danger' : ''}${item.tone === 'warning' ? ' deploy-summary-item--warning' : ''}`}
          >
            <label>{item.label}</label>
            <span>{item.value}</span>
          </div>
        ))}
      </div>
    </div>
  )
}