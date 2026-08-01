import type { ReactNode } from 'react'

type TimelineEvent = {
  id: string
  time: string
  title: string
  description?: string
  icon?: ReactNode
  variant?: 'info' | 'success' | 'warning' | 'danger'
  active?: boolean
}

type TimelineProps = {
  events: TimelineEvent[]
  className?: string
}

const variantColors = {
  info: 'var(--risk-read, #38bdf8)',
  success: 'var(--status-success, #34d399)',
  warning: 'var(--status-warning, #fbbf24)',
  danger: 'var(--risk-destructive, #f43f5e)',
}

export function Timeline({ events, className = '' }: TimelineProps) {
  if (events.length === 0) {
    return (
      <div className={`timeline ${className}`} style={{ padding: 'var(--density-padding, 20px)', color: 'var(--text-muted)', textAlign: 'center', fontSize: 13 }}>
        暂无事件记录
      </div>
    )
  }

  return (
    <div className={`timeline ${className}`}>
      {events.map((event, idx) => {
        const color = variantColors[event.variant || 'info']
        const isLast = idx === events.length - 1

        return (
          <div
            key={event.id}
            className={`timeline-item ${event.active ? 'timeline-item--active' : ''}`}
            style={{
              '--tl-color': color,
            } as React.CSSProperties}
          >
            <div className="timeline-line">
              <div className="timeline-dot" style={{ borderColor: color, background: color }} />
              {!isLast && <div className="timeline-connector" style={{ background: 'var(--border, rgba(148,163,184,0.12))' }} />}
            </div>
            <div className="timeline-content">
              <div className="timeline-header">
                <time className="timeline-time">{event.time}</time>
                {event.icon && <span className="timeline-icon">{event.icon}</span>}
              </div>
              <strong className="timeline-title">{event.title}</strong>
              {event.description && <p className="timeline-desc">{event.description}</p>}
            </div>
          </div>
        )
      })}
    </div>
  )
}