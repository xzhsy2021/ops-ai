import { Badge } from '../ui/Badge'

type RiskLevel = 'info' | 'warn' | 'alert'

type RiskBadgeProps = {
  level: RiskLevel
  label?: string
  size?: 'sm' | 'md'
  className?: string
}

const riskConfig: Record<RiskLevel, { label: string; variant: 'read' | 'write' | 'destructive' }> = {
  info: { label: 'read', variant: 'read' },
  warn: { label: 'write', variant: 'write' },
  alert: { label: 'destructive', variant: 'destructive' },
}

export function RiskBadge({ level, label, size = 'md', className = '' }: RiskBadgeProps) {
  const config = riskConfig[level]
  return (
    <Badge variant={config.variant} size={size} dot className={className}>
      {label || config.label}
    </Badge>
  )
}