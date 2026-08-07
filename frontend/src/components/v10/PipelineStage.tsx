export type PipelineStatus = 'pending' | 'running' | 'success' | 'failed' | 'current'

const LABEL: Record<string, string> = {
  pending: '待执行',
  running: '进行中',
  success: '完成',
  failed: '失败',
  current: '当前',
}

/** Deploy stage chip — Stage / Status for V10 rule */
export default function PipelineStage({
  name,
  status = 'pending',
  index,
}: {
  name: string
  status?: PipelineStatus
  index?: number
}) {
  return (
    <div className={`wx-pipe-stage is-${status}`} data-status={status}>
      {typeof index === 'number' ? <span className="wx-pipe-idx">{index + 1}</span> : null}
      <div className="wx-pipe-body">
        <strong>{name}</strong>
        <span>{LABEL[status] || status}</span>
      </div>
    </div>
  )
}

export function PipelineStageRail({
  steps,
  activeIndex,
}: {
  steps: Array<{ key: string; title: string }>
  activeIndex: number
}) {
  return (
    <div className="wx-pipe-rail" role="list" aria-label="发布阶段">
      {steps.map((s, i) => {
        let status: PipelineStatus = 'pending'
        if (i < activeIndex) status = 'success'
        else if (i === activeIndex) status = 'current'
        return (
          <div key={s.key} className="wx-pipe-rail-item" role="listitem">
            <PipelineStage name={s.title} status={status} index={i} />
            {i < steps.length - 1 ? <div className="wx-pipe-connector" aria-hidden /> : null}
          </div>
        )
      })}
    </div>
  )
}
