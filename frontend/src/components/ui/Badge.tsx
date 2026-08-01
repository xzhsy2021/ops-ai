import type { ReactNode } from 'react'

type BadgeVariant = 'read' | 'write' | 'destructive' | 'info' | 'success' | 'warning'

type BadgeProps = {
  children: ReactNode
  variant?: BadgeVariant
  size?: 'sm' | 'md'
  dot?: boolean
  className?: string
}

const variantColors: Record<BadgeVariant, string> = {
  read: 'var(--g-blue, #4d8dff)',
  write: 'var(--g-purple, #a06bff)',
  destructive: 'var(--danger, #fb7185)',
  info: 'var(--accent, #35e07c)',
  success: 'var(--success, #34d399)',
  warning: 'var(--warning, #fbbf24)',
}

const variantSurfaces: Record<BadgeVariant, string> = {
  read: 'color-mix(in srgb, var(--g-blue, #4d8dff) 15%, transparent)',
  write: 'color-mix(in srgb, var(--g-purple, #a06bff) 15%, transparent)',
  destructive: 'var(--danger-surface, rgba(76,5,25,0.62))',
  info: 'var(--accent-soft, rgba(53,224,124,0.14))',
  success: 'var(--success-surface, rgba(6,78,59,0.55))',
  warning: 'var(--warning-surface, rgba(67,20,7,0.62))',
}

export function Badge({
  children,
  variant = 'info',
  size = 'md',
  dot = false,
  className = '',
}: BadgeProps) {
  return (
    <span
      className={`badge badge--${variant} badge--${size} ${className}`}
      style={{
        '--badge-color': variantColors[variant],
        '--badge-surface': variantSurfaces[variant],
      } as React.CSSProperties}
    >
      {dot && <span className="badge-dot" />}
      {children}
    </span>
  )
}