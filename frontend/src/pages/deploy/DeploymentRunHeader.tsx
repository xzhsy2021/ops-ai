import type { ReactNode } from 'react'

export type DeploymentRunStat = {
  label: string
  value: ReactNode
  tone?: 'success' | 'danger' | 'default'
}

export function DeploymentRunHeader({
  stats,
  actions,
}: {
  stats: DeploymentRunStat[]
  actions?: ReactNode
}) {
  return (
    <div className="deployment-run-header">
      {stats.map((s, i) => (
        <div
          key={i}
          className={`deployment-run-stat${s.tone === 'success' ? ' deployment-run-stat--success' : ''}${s.tone === 'danger' ? ' deployment-run-stat--danger' : ''}`}
        >
          <label>{s.label}</label>
          <span>{s.value}</span>
        </div>
      ))}
      {actions && <div className="deployment-run-actions">{actions}</div>}
    </div>
  )
}