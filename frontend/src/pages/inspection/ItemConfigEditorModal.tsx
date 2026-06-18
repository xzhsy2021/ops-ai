import { useState } from 'react'

export type ItemConfigEditorModalProps = {
  item: any
  onClose: () => void
  onSave: (patch: any) => void
  onToggleEnabled: (id: string) => void
}

export function ItemConfigEditorModal({ item, onClose, onSave, onToggleEnabled }: ItemConfigEditorModalProps) {
  const [name, setName] = useState(item.item_name || '')
  const [desc, setDesc] = useState(item.description || '')
  const [enabled, setEnabled] = useState(item.enabled !== false)
  const [sortOrder, setSortOrder] = useState(item.sort_order || 0)
  const [rules, setRules] = useState<any[]>(item.rules || [])

  function toggleRule(idx: number) {
    setRules((prev) => prev.map((r, i) => i === idx ? { ...r, enabled: r.enabled === false ? true : false } : r))
  }

  function quickToggle() {
    onToggleEnabled(item.id)
    setEnabled(!enabled)
  }

  return (
    <div className="inspection-modal-overlay" onClick={onClose}>
      <div className="panel-card inspection-rule-modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 720 }}>
        <div className="inspection-modal-header">
          <div>
            <h2 style={{ margin: 0 }}>配置巡检项目</h2>
            <p className="muted" style={{ margin: '4px 0 0' }}>可启用/禁用项目，可编辑名称描述，可调整关联规则。</p>
          </div>
          <button className="btn btn-subtle" onClick={onClose}>关闭</button>
        </div>
        <div className="inspection-rule-form-grid">
          <label>编码（不可改）<input value={item.item_code} disabled /></label>
          <label>名称<input value={name} onChange={(e) => setName(e.target.value)} /></label>
          <label>状态<select value={enabled ? 'true' : 'false'} onChange={(e) => setEnabled(e.target.value === 'true')}><option value="true">启用</option><option value="false">禁用</option></select></label>
          <label>执行顺序<input type="number" value={sortOrder} onChange={(e) => setSortOrder(Number(e.target.value))} /></label>
          <label style={{ gridColumn: '1 / -1' }}>描述<textarea value={desc} onChange={(e) => setDesc(e.target.value)} rows={2} /></label>
        </div>
        <div className="rule-section rule-section--notes" style={{ marginTop: 12 }}>
          <header className="rule-section__head">
            <span className="rule-section__icon" aria-hidden>📋</span>
            <h3>关联规则 <small>· 已启用 {rules.filter((r) => r.enabled !== false).length} / {rules.length} · 勾选框可快速启用/禁用</small></h3>
          </header>
          <div className="rule-section__body" style={{ display: 'grid', gap: 6 }}>
            {rules.length === 0 && <small className="muted">该项目尚未关联任何规则</small>}
            {rules.map((r, idx) => {
              const isOn = r.enabled !== false
              return (
                <div
                  key={r.id || r.rule_code}
                  className={`rule-row ${isOn ? 'is-on' : 'is-off'}`}
                >
                  <input type="checkbox" checked={isOn} onChange={() => toggleRule(idx)} />
                  <span className="rule-row__main">
                    <code>{r.rule_code}</code>
                    <small className="muted">{r.rule_name || r.rule_code}</small>
                  </span>
                  <span className="rule-row__meta">
                    {r.risk_level && <span className={`rule-row__risk rule-row__risk--${String(r.risk_level).toLowerCase()}`}>{r.risk_level}</span>}
                    <small className="muted">顺序 {r.sort_order || 0}</small>
                  </span>
                </div>
              )
            })}
          </div>
        </div>
        <div className="inspection-modal-actions">
          <button className="btn btn-subtle" onClick={quickToggle}>{enabled ? '快速禁用' : '快速启用'}</button>
          <button className="btn btn-subtle" onClick={onClose}>取消</button>
          <button className="btn primary" onClick={() => onSave({ item_name: name, description: desc, enabled, sort_order: sortOrder, rules })}>保存</button>
        </div>
      </div>
    </div>
  )
}
