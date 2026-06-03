import { useToastStore } from '../stores/toastStore'
import { CircleCheck, CircleX, TriangleAlert, Info, X } from 'lucide-react'

const ICON_MAP = {
  success: CircleCheck,
  error: CircleX,
  warning: TriangleAlert,
  info: Info,
} as const

const COLOR_MAP = {
  success: 'var(--success)',
  error: 'var(--danger)',
  warning: 'var(--warning)',
  info: 'var(--cyan)',
} as const

export function ToastContainer() {
  const toasts = useToastStore((s) => s.toasts)
  const removeToast = useToastStore((s) => s.removeToast)

  if (toasts.length === 0) return null

  return (
    <div className="toast-container">
      {toasts.map((toast) => {
        const Icon = ICON_MAP[toast.type]
        return (
          <div key={toast.id} className={`toast toast--${toast.type}`}>
            <span className="toast-icon">
              <Icon size={18} color={COLOR_MAP[toast.type]} />
            </span>
            <div className="toast-body">
              <strong>{toast.title}</strong>
              {toast.message && <p>{toast.message}</p>}
            </div>
            <button
              className="toast-close"
              onClick={() => removeToast(toast.id)}
              aria-label="关闭"
            >
              <X size={14} />
            </button>
          </div>
        )
      })}
    </div>
  )
}