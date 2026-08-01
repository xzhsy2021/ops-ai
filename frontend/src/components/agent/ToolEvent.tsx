import { Badge } from '../ui/Badge'
import type { ReactNode } from 'react'

type ToolEventProps = {
  tool: string
  risk: 'info' | 'warn' | 'alert'
  status: 'RUNNING' | 'SUCCESS' | 'QUEUED' | 'FAILED'
  source?: string
  target?: string
  timestamp?: string
  duration?: string
  icon?: ReactNode
  className?: string
}

const riskVariant: Record<string, 'read' | 'write' | 'destructive'> = {
  info: 'read',
  warn: 'write',
  alert: 'destructive',
}

const statusVariant: Record<string, 'read' | 'success' | 'warning' | 'destructive'> = {
  RUNNING: 'read',
  SUCCESS: 'success',
  QUEUED: 'warning',
  FAILED: 'destructive',
}

export function ToolEvent({
  tool,
  risk,
  status,
  source,
  target,
  timestamp,
  duration,
  icon,
  className = '',
}: ToolEventProps) {
  return (
    <div className={`tool-event tool-event--${status.toLowerCase()} ${className}`}>
      <div className="tool-event-line">
        <div className="tool-event-dot" style={{
          background: risk === 'alert' ? 'var(--risk-destructive, #f43f5e)' : risk === 'warn' ? 'var(--risk-write, #fb923c)' : 'var(--risk-read, #38bdf8)',
        }} />
        <div className="tool-event-connector" />
      </div>
      <div className="tool-event-body">
        <div className="tool-event-header">
          {icon && <span className="tool-event-icon">{icon}</span>}
          <code className="tool-event-tool">{tool}</code>
          <div className="tool-event-badges">
            <Badge variant={riskVariant[risk]} size="sm" dot>{risk}</Badge>
            <Badge variant={statusVariant[status]} size="sm">{status}</Badge>
          </div>
        </div>
        <div className="tool-event-meta">
          {source && <span>source: {source}</span>}
          {target && <span>target: {target}</span>}
          {timestamp && <time>{timestamp}</time>}
          {duration && <span>{duration}</span>}
        </div>
      </div>
    </div>
  )
}