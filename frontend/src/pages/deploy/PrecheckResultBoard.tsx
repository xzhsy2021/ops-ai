import { TriangleAlert, CircleX, Lightbulb, CircleCheck } from 'lucide-react'

export type PrecheckItem = {
  level: 'blocked' | 'warning' | 'suggestion' | 'pass'
  message: string
  detail?: string
}

export function PrecheckResultBoard({
  items,
  collapsedPass = true,
}: {
  items: PrecheckItem[]
  collapsedPass?: boolean
}) {
  const blocked = items.filter((i) => i.level === 'blocked')
  const warnings = items.filter((i) => i.level === 'warning')
  const suggestions = items.filter((i) => i.level === 'suggestion')
  const passed = items.filter((i) => i.level === 'pass')

  if (!items.length) return null

  return (
    <div className="precheck-board">
      {blocked.length > 0 && (
        <div className="precheck-category precheck-category--blocked">
          <div className="precheck-category-header">
            <CircleX size={16} />
            阻断项 ({blocked.length})
          </div>
          {blocked.map((item, i) => (
            <div key={i} className="precheck-item">
              <span>{item.message}</span>
              {item.detail && <small style={{ color: 'var(--text-muted)' }}>{item.detail}</small>}
            </div>
          ))}
        </div>
      )}
      {warnings.length > 0 && (
        <div className="precheck-category precheck-category--warning">
          <div className="precheck-category-header">
            <TriangleAlert size={16} />
            警告项 ({warnings.length})
          </div>
          {warnings.map((item, i) => (
            <div key={i} className="precheck-item">
              <span>{item.message}</span>
              {item.detail && <small style={{ color: 'var(--text-muted)' }}>{item.detail}</small>}
            </div>
          ))}
        </div>
      )}
      {suggestions.length > 0 && (
        <div className="precheck-category precheck-category--suggestion">
          <div className="precheck-category-header">
            <Lightbulb size={16} />
            建议项 ({suggestions.length})
          </div>
          {suggestions.map((item, i) => (
            <div key={i} className="precheck-item">
              <span>{item.message}</span>
              {item.detail && <small style={{ color: 'var(--text-muted)' }}>{item.detail}</small>}
            </div>
          ))}
        </div>
      )}
      {passed.length > 0 && !collapsedPass && (
        <div className="precheck-category precheck-category--pass">
          <div className="precheck-category-header">
            <CircleCheck size={16} />
            通过项 ({passed.length})
          </div>
          {passed.map((item, i) => (
            <div key={i} className="precheck-item">
              <span>{item.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}