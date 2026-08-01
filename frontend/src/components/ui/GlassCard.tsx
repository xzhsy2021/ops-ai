import type { ReactNode, CSSProperties } from 'react'

type GlassCardProps = {
  children: ReactNode
  className?: string
  style?: CSSProperties
  title?: string
  subtitle?: string
  actions?: ReactNode
  badge?: ReactNode
  padding?: 'sm' | 'md' | 'lg'
  onClick?: () => void
}

const paddingMap = {
  sm: 'var(--density-padding, 16px)',
  md: 'var(--density-padding, 20px)',
  lg: 'var(--density-padding-lg, 28px)',
}

export function GlassCard({
  children,
  className = '',
  style,
  title,
  subtitle,
  actions,
  badge,
  padding = 'md',
  onClick,
}: GlassCardProps) {
  return (
    <div
      className={`glass-card ${className}`}
      style={{ padding: paddingMap[padding], ...style }}
      onClick={onClick}
    >
      {(title || subtitle || actions || badge) && (
        <div className="glass-card-header">
          <div className="glass-card-title-group">
            {title && <h3 className="glass-card-title">{title}</h3>}
            {subtitle && <span className="glass-card-subtitle">{subtitle}</span>}
          </div>
          <div className="glass-card-actions">
            {badge}
            {actions}
          </div>
        </div>
      )}
      <div className="glass-card-body">
        {children}
      </div>
    </div>
  )
}