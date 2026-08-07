import { Link } from 'react-router-dom'
import ActionButton from './ActionButton'

/** Explain / Recommend / Action card — Inspection & AI V10 rule */
export default function DiagnosisCard({
  title,
  explain,
  recommend,
  actionLabel,
  actionTo,
  onAction,
  level = 'info',
}: {
  title: string
  explain: string
  recommend?: string
  actionLabel?: string
  actionTo?: string
  onAction?: () => void
  level?: 'info' | 'warn' | 'danger'
}) {
  return (
    <section className={`wx-diagnosis is-${level}`}>
      <header className="wx-diagnosis-head">
        <span className="wx-diagnosis-tag">诊断</span>
        <h3>{title}</h3>
      </header>
      <div className="wx-diagnosis-block">
        <span className="wx-diagnosis-label">说明 Explain</span>
        <p>{explain}</p>
      </div>
      {recommend ? (
        <div className="wx-diagnosis-block">
          <span className="wx-diagnosis-label">建议 Recommend</span>
          <p>{recommend}</p>
        </div>
      ) : null}
      {(actionLabel && (actionTo || onAction)) ? (
        <div className="wx-diagnosis-actions">
          <span className="wx-diagnosis-label">动作 Action</span>
          {actionTo ? (
            <Link to={actionTo} className="wx-action-btn">{actionLabel}</Link>
          ) : (
            <ActionButton onClick={onAction}>{actionLabel}</ActionButton>
          )}
        </div>
      ) : null}
    </section>
  )
}
