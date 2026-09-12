import { toneFromStatus, type FolderTone } from './FolderStage'
import { parseBackendTime } from '../../utils/datetime.js'

export type GanttItem = {
  id: string
  title: string
  status?: string
  started_at?: string
  finished_at?: string
  onClick?: () => void
}

function toMs(v?: string) {
  if (!v) return NaN
  const d = parseBackendTime(v)
  return d ? d.getTime() : NaN
}

/** Bottom timeline for current page tasks — business gantt, not decoration */
export function GanttStrip({ items, title = '本页时间线' }: { items: GanttItem[]; title?: string }) {
  const parsed = items
    .map((it) => {
      const start = toMs(it.started_at)
      const end = toMs(it.finished_at)
      const a = Number.isFinite(start) ? start : Number.isFinite(end) ? end - 60_000 : Date.now() - 60_000
      const b = Number.isFinite(end) ? end : Date.now()
      return { ...it, a, b: Math.max(b, a + 5_000) }
    })
    .filter((it) => Number.isFinite(it.a))

  if (!parsed.length) {
    return (
      <div className="wx-gantt">
        <div className="wx-gantt-head"><span>{title}</span></div>
        <div className="wx-gantt-empty">暂无带时间戳的任务可展示</div>
      </div>
    )
  }

  const min = Math.min(...parsed.map((p) => p.a))
  const max = Math.max(...parsed.map((p) => p.b))
  const span = Math.max(max - min, 60_000)

  return (
    <div className="wx-gantt">
      <div className="wx-gantt-head">
        <span>{title}</span>
        <span>{parsed.length} 条</span>
      </div>
      <div className="wx-gantt-track">
        {parsed.slice(0, 24).map((p) => {
          const left = ((p.a - min) / span) * 100
          const width = Math.max(((p.b - p.a) / span) * 100, 1.5)
          const tone: FolderTone | 'danger' = ['failed', 'error'].includes(String(p.status || '').toLowerCase())
            ? 'danger'
            : toneFromStatus(p.status)
          return (
            <button
              key={p.id}
              type="button"
              className={`wx-gantt-bar tone-${tone}`}
              style={{ left: `${left}%`, width: `${width}%` }}
              title={p.title}
              onClick={p.onClick}
            >
              {p.title}
            </button>
          )
        })}
      </div>
    </div>
  )
}

export default GanttStrip
