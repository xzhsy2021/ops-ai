import { useEffect } from 'react'

/** Lightweight theme feedback toast (video settings feel, no modal stack) */
export function ThemeToast({
  open,
  label,
  onDone,
}: {
  open: boolean
  label: string
  onDone?: () => void
}) {
  useEffect(() => {
    if (!open) return
    const t = window.setTimeout(() => onDone?.(), 1600)
    return () => window.clearTimeout(t)
  }, [open, onDone])

  if (!open) return null
  return (
    <div className="wx-theme-toast" role="status" aria-live="polite">
      <span className="wx-theme-toast-dot" />
      已切换主题：<strong>{label}</strong>
    </div>
  )
}

export default ThemeToast
