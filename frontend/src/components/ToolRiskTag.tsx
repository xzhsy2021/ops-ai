export default function ToolRiskTag({ risk, approval }: { risk?: string; approval?: boolean }) {
  const level = (risk || 'low').toLowerCase()
  const cls = level === 'critical' || level === 'high' ? 'status-danger' : level === 'medium' ? 'status-warning' : 'status-success'
  return <span className={`status-pill ${cls}`}>{risk || 'low'}{approval ? ' · 审批' : ''}</span>
}
