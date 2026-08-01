import { Badge } from '../ui/Badge'

type AgentNodeProps = {
  id: string
  label: string
  risk?: 'info' | 'warn' | 'alert'
  status?: 'RUNNING' | 'SUCCESS' | 'QUEUED' | 'FAILED'
  active?: boolean
  onClick?: () => void
  className?: string
}

const statusColors: Record<string, 'read' | 'success' | 'warning' | 'destructive'> = {
  RUNNING: 'read',
  SUCCESS: 'success',
  QUEUED: 'warning',
  FAILED: 'destructive',
}

const riskVariant: Record<string, 'read' | 'write' | 'destructive'> = {
  info: 'read',
  warn: 'write',
  alert: 'destructive',
}

export function AgentNode({
  id,
  label,
  risk = 'info',
  status,
  active = false,
  onClick,
  className = '',
}: AgentNodeProps) {
  return (
    <div
      className={`agent-node ${active ? 'agent-node--active' : ''} ${className}`}
      onClick={onClick}
      style={{
        '--agent-color': risk === 'alert'
          ? 'var(--risk-destructive, #f43f5e)'
          : risk === 'warn'
            ? 'var(--risk-write, #fb923c)'
            : 'var(--risk-read, #38bdf8)',
      } as React.CSSProperties}
    >
      <div className="agent-node-indicator">
        <div className="agent-node-dot" />
        {active && <div className="agent-node-pulse" />}
      </div>
      <div className="agent-node-body">
        <strong className="agent-node-label">{label}</strong>
        <span className="agent-node-id">{id}</span>
      </div>
      <div className="agent-node-meta">
        {status && (
          <Badge variant={statusColors[status] || 'info'} size="sm">
            {status}
          </Badge>
        )}
        <Badge variant={riskVariant[risk] || 'read'} size="sm" dot>
          {risk}
        </Badge>
      </div>
    </div>
  )
}