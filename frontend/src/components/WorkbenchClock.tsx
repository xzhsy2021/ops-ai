import { useEffect, useMemo, useState } from 'react'

function readNow() {
  return new Date()
}

export default function WorkbenchClock() {
  const [now, setNow] = useState(readNow)

  useEffect(() => {
    const timer = window.setInterval(() => setNow(readNow()), 30_000)
    return () => window.clearInterval(timer)
  }, [])

  const { timeText, dateText } = useMemo(() => {
    const timeFormatter = new Intl.DateTimeFormat('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
    const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      weekday: 'short',
    })
    return {
      timeText: timeFormatter.format(now),
      dateText: dateFormatter.format(now),
    }
  }, [now])

  return (
    <div className="runtime-clock" aria-label={`本地运行时间 ${dateText} ${timeText}`}>
      <span className="runtime-clock-pulse" aria-hidden="true" />
      <span className="runtime-clock-copy">
        <strong className="runtime-clock-time">{timeText}</strong>
        <small className="runtime-clock-date">{dateText} · Local</small>
      </span>
    </div>
  )
}
