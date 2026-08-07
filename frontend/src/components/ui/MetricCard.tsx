import type { CSSProperties } from 'react'

type MetricTone = 'normal' | 'run' | 'fail' | 'wait'

type MetricCardProps = {
  title: string
  value: string | number
  status?: MetricTone
  className?: string
  style?: CSSProperties
}

/**
 * KPI display card with glow effect.
 * Uses .wx-kpi CSS classes from wenxi-workspace.css.
 */
export function MetricCard({
  title,
  value,
  status = 'normal',
  className = '',
  style,
}: MetricCardProps) {
  const toneClass = status !== 'normal' ? `tone-${status}` : ''
  return (
    <div
      className={`wx-kpi ${toneClass} ${className}`.trim()}
      style={style}
    >
      <span className="label">{title}</span>
      <strong className="value">{value}</strong>
    </div>
  )
}

export default MetricCard
