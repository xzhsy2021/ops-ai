export default function RiskLevelTag({ level }: { level?: string }) {
  const v = (level || 'LOW').toUpperCase()
  const cls = v === 'HIGH' ? 'status-danger' : v === 'MEDIUM' ? 'status-warning' : 'status-success'
  return <span className={`status-pill ${cls}`}>{v}</span>
}
