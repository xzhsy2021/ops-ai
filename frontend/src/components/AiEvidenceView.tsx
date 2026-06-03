export default function AiEvidenceView({ data }: { data: any }) {
  const output = data?.output || data || {}
  const sections = [
    ['facts', '事实'],
    ['inferences', '推断'],
    ['recommendations', '建议'],
    ['evidence', '证据'],
  ] as const
  return (
    <div className="page-section">
      <div className="section-title">AI 证据链</div>
      {sections.map(([key, label]) => {
        const items = Array.isArray(output[key]) ? output[key] : []
        return (
          <div key={key} className="card" style={{ marginBottom: 12 }}>
            <h3>{label}</h3>
            {items.length === 0 ? <p className="muted">暂无</p> : (
              <ul className="compact-list">
                {items.map((item: any, idx: number) => (
                  <li key={`${key}-${idx}`}>
                    <strong>{item.title || item.claim || item.action || item.id || `${label}${idx + 1}`}</strong>
                    <div className="muted">{item.content || item.description || item.suggestion || item.ref || JSON.stringify(item)}</div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )
      })}
    </div>
  )
}
