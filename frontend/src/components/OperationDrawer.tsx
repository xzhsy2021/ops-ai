import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { X } from 'lucide-react'

export type OperationDrawerProps = {
  open: boolean
  title: string
  width?: number | string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
}

export function OperationDrawer({ open, title, width, onClose, children, footer }: OperationDrawerProps) {
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="drawer-overlay" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="drawer-panel" role="dialog" aria-modal="true" aria-label={title} style={{ width: width || 520 }}>
        <div className="drawer-header">
          <strong>{title}</strong>
          <button className="drawer-close-btn" onClick={onClose} aria-label="关闭">
            <X size={18} />
          </button>
        </div>
        <div className="drawer-body">{children}</div>
        {footer && <div className="drawer-footer">{footer}</div>}
      </div>
    </div>
  )
}