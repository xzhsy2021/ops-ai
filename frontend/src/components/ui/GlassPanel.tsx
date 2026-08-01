import type { ReactNode, CSSProperties } from 'react'

type GlassPanelProps = {
  children: ReactNode
  className?: string
  style?: CSSProperties
  as?: 'div' | 'section' | 'aside' | 'header' | 'footer'
  hover?: boolean
  onClick?: () => void
}

export function GlassPanel({
  children,
  className = '',
  style,
  as: Tag = 'div',
  hover = false,
  onClick,
}: GlassPanelProps) {
  const cls = [
    'glass-panel',
    hover && 'glass-panel--hoverable',
    className,
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <Tag className={cls} style={style} onClick={onClick}>
      {children}
    </Tag>
  )
}