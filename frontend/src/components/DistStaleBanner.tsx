import { useEffect, useState } from 'react'
import { systemHealth } from '../api'

const DISMISS_KEY = 'ops_dist_stale_dismissed_until'

function getDismissedUntil(): number {
  try {
    return Number(localStorage.getItem(DISMISS_KEY)) || 0
  } catch {
    return 0
  }
}

function setDismissed(until: number) {
  try {
    localStorage.setItem(DISMISS_KEY, String(until))
  } catch { /* localStorage unavailable */ }
}

export function DistStaleBanner() {
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const check = async () => {
      try {
        const body = await systemHealth.frontendStatus()
        const stale = body?.data?.dist_stale === true
        const dismissedUntil = getDismissedUntil()
        if (stale && Date.now() > dismissedUntil) {
          setVisible(true)
        }
      } catch { /* network issue, ignore */ }
    }
    check()
  }, [])

  const handleDismiss = () => {
    setDismissed(Date.now() + 24 * 60 * 60 * 1000)
    setVisible(false)
  }

  if (!visible) return null

  return (
    <div className="dist-stale-banner">
      <span>前端构建可能已过期，部分页面可能显示旧版 UI。</span>
      <code>cd frontend &amp;&amp; npm run build</code>
      <button className="btn btn-subtle" onClick={handleDismiss}>24h 不再提醒</button>
    </div>
  )
}