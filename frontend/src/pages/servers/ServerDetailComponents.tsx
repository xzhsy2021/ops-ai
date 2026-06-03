import type { ReactNode } from 'react'

export function ServerInfoHeader({
  name,
  status,
  address,
  os,
  tags,
  actions,
}: {
  name: string
  status: 'online' | 'offline' | 'warning' | 'installing'
  address?: string
  os?: string
  tags?: string[]
  actions?: ReactNode
}) {
  const color =
    status === 'online'
      ? 'var(--success)'
      : status === 'warning'
        ? 'var(--warning)'
        : status === 'installing'
          ? 'var(--cyan)'
          : 'var(--text-muted)'

  return (
    <div className="card">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <div style={{ width: 14, height: 14, borderRadius: '50%', background: color, flexShrink: 0 }} />
          <h1 style={{ margin: 0, fontSize: 20 }}>{name}</h1>
          <span className={`tag ${status === 'online' ? 'tag-success' : status === 'offline' ? 'tag-danger' : status === 'warning' ? 'tag-warning' : 'tag-info'}`}>
            {status === 'online' ? '在线' : status === 'offline' ? '离线' : status === 'warning' ? '异常' : '安装中'}
          </span>
          {address && <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>{address}</span>}
          {os && <span className="tag">{os}</span>}
          {tags?.map((t) => <span key={t} className="tag">{t}</span>)}
        </div>
        {actions && <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>{actions}</div>}
      </div>
    </div>
  )
}

export function ServerTabPanel({
  label,
  active,
  children,
}: {
  label: string
  active?: boolean
  children: ReactNode
}) {
  return (
    <div data-tab={label} className={active ? '' : 'tab-hidden'}>
      {children}
    </div>
  )
}

export function ServerHealthOverview({
  metrics,
}: {
  metrics: Array<{
    name: string
    value: number
    max?: number
    unit?: string
    status?: 'normal' | 'warning' | 'danger'
  }>
}) {
  return (
    <div className="health-overview">
      {metrics.map((m) => {
        const pct = m.max ? Math.min(100, Math.round((m.value / m.max) * 100)) : 0
        return (
          <div key={m.name} className="health-bar-item">
            <div className="health-bar-label">
              <span>{m.name}</span>
              <span>
                {m.value}{m.unit || ''}
                {m.max !== undefined && <> / {m.max}{m.unit || ''}</>}
              </span>
            </div>
            <div className="health-bar-track">
              <div
                className={`health-bar-fill health-bar-fill--${m.status || 'normal'}`}
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        )
      })}
    </div>
  )
}