import type { ReactNode } from 'react'
import { Server, Cpu, HardDrive } from 'lucide-react'

export type ServerStatusCardProps = {
  name: string
  address?: string
  os?: string
  status: 'online' | 'offline' | 'warning' | 'installing'
  statusLabel?: string
  cpu?: string
  memory?: string
  tags?: string[]
  onClick?: () => void
  selected?: boolean
}

export function ServerStatusCard({
  name,
  address,
  os,
  status,
  statusLabel,
  cpu,
  memory,
  tags,
  onClick,
  selected,
}: ServerStatusCardProps) {
  const color =
    status === 'online'
      ? 'var(--success)'
      : status === 'warning'
        ? 'var(--warning)'
        : status === 'installing'
          ? 'var(--cyan)'
          : 'var(--text-muted)'

  return (
    <button
      className={`server-card server-card--${status}${selected ? ' server-card--selected' : ''}`}
      onClick={onClick}
      type="button"
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div
          style={{
            width: 12,
            height: 12,
            borderRadius: '50%',
            background: color,
            flexShrink: 0,
          }}
        />
        <div style={{ flex: 1, minWidth: 0 }}>
          <strong style={{ display: 'block', color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {name}
          </strong>
          <small style={{ color: 'var(--text-muted)' }}>
            {statusLabel || status}
            {address && <> · {address}</>}
          </small>
        </div>
      </div>
      <div style={{ display: 'grid', gap: 3, fontSize: 12, color: 'var(--text-secondary)' }}>
        {os && (
          <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
            <Server size={12} />
            {os}
          </div>
        )}
        {cpu && (
          <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
            <Cpu size={12} />
            {cpu}
          </div>
        )}
        {memory && (
          <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
            <HardDrive size={12} />
            {memory}
          </div>
        )}
      </div>
      {tags && tags.length > 0 && (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {tags.map((t) => (
            <span key={t} className="tag">{t}</span>
          ))}
        </div>
      )}
    </button>
  )
}

export function ServerQuickStats({
  stats,
}: {
  stats: Array<{ label: string; value: ReactNode; icon?: ReactNode; tone?: 'success' | 'warning' | 'danger' }>
}) {
  return (
    <div className="stat-grid stat-grid--compact">
      {stats.map((s, i) => (
        <div
          key={i}
          className={`stat-card ${s.tone === 'success' ? 'stat-card--success' : s.tone === 'warning' ? 'stat-card--warning' : s.tone === 'danger' ? 'stat-card--danger' : ''}`.trim()}
        >
          <span className="stat-card-label">
            {s.icon && <span style={{ marginRight: 4 }}>{s.icon}</span>}
            {s.label}
          </span>
          <span className="stat-card-value">{s.value}</span>
        </div>
      ))}
    </div>
  )
}

export function ServerBatchActionBar({
  selectedCount,
  actions,
}: {
  selectedCount: number
  actions?: ReactNode
}) {
  if (selectedCount === 0) return null

  return (
    <div className="batch-action-bar">
      <strong>已选择 {selectedCount} 台服务器</strong>
      {actions && <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>{actions}</div>}
    </div>
  )
}