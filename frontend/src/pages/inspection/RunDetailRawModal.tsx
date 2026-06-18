export type RunDetailRawModalProps = {
  runId: string
  items: any[]
  onClose: () => void
}

export function RunDetailRawModal({ runId, items, onClose }: RunDetailRawModalProps) {
  return (
    <div className="inspection-modal-overlay" onClick={onClose}>
      <div className="panel-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 960, maxHeight: '85vh', overflow: 'auto' }}>
        <div className="inspection-modal-header">
          <div>
            <h2 style={{ margin: 0 }}>巡检原始数据</h2>
            <p className="muted" style={{ margin: '4px 0 0' }}>运行 ID: {runId} · 共 {items.length} 项</p>
          </div>
          <button className="btn btn-subtle" onClick={onClose}>关闭</button>
        </div>
        <div style={{ display: 'grid', gap: 12 }}>
          {items.map((it) => (
            <div key={it.id} className="mini-card">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <strong>{it.item_name || it.item_code}</strong>
                <small className="muted">[{it.category}]</small>
                <span className={`status-pill status-${(it.status || 'normal').toLowerCase()}`} style={{ fontSize: 11 }}>{it.status}</span>
                {it.risk_level && it.risk_level !== 'NONE' && <span style={{ fontSize: 11, color: it.risk_level === 'HIGH' ? '#c0392b' : it.risk_level === 'MEDIUM' ? '#d35400' : '#7f8c8d' }}>风险: {it.risk_level}</span>}
              </div>
              {it.message && <div style={{ marginTop: 6 }}><strong>结果描述：</strong>{it.message}{it.parsed_facts?.summary ? <><br /><small style={{ color: 'var(--accent, #3182ce)' }}>📊 量化指标: {it.parsed_facts.summary}</small></> : ''}</div>}
              {it.suggestion && <div style={{ marginTop: 4, color: '#2c3e50' }}><strong>建议：</strong>{it.suggestion}</div>}
              {it.raw_output && (
                <details open style={{ marginTop: 6 }}>
                  <summary style={{ cursor: 'pointer', fontWeight: 'bold' }}>原始输出（点击折叠/展开）</summary>
                  <pre style={{ background: '#1e1e1e', color: '#d4d4d4', padding: 10, borderRadius: 4, overflow: 'auto', fontSize: 12, marginTop: 6, maxHeight: 280 }}>{it.raw_output}</pre>
                </details>
              )}
            </div>
          ))}
        </div>
        <div className="inspection-modal-actions">
          <button className="btn primary" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  )
}
