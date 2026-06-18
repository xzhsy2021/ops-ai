export type CategoryOptionsProps = {
  items: any[]
  selected: string[]
  onChange: (next: string[]) => void
  itemConfigs?: any[]
  onConfigEdit?: (item: any) => void
}

export function CategoryOptions({ items, selected, onChange, itemConfigs, onConfigEdit }: CategoryOptionsProps) {
  const allCodes = items.map((x) => x.code)
  const checkedAll = allCodes.length > 0 && allCodes.every((x) => selected.includes(x))
  // 合并 itemConfigs 用于获取启用状态
  const cfgMap = new Map<string, any>()
  ;(itemConfigs || []).forEach((c: any) => cfgMap.set(c.item_code, c))
  return (
    <div style={{ display: 'grid', gap: 8 }}>
      <label className="check-row">
        <input type="checkbox" checked={checkedAll} onChange={(e) => onChange(e.target.checked ? allCodes : [])} />
        <strong>全选</strong>
        <small className="muted">（{selected.length}/{items.length}）</small>
      </label>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 10 }}>
        {items.map((item) => {
          const cfg = cfgMap.get(item.code)
          const enabled = cfg ? cfg.enabled !== false : true
          const active = selected.includes(item.code)
          return (
            <div
              key={item.code}
              className={`mini-card category-card${active ? ' category-card--active' : ''}${enabled ? '' : ' category-card--disabled'}`}
              style={{ cursor: 'pointer', display: 'flex', alignItems: 'flex-start', gap: 8, opacity: enabled ? 1 : 0.5 }}
              onClick={() => onChange(active ? selected.filter((x) => x !== item.code) : [...selected, item.code])}
            >
              <input
                type="checkbox"
                checked={selected.includes(item.code)}
                onChange={(e) => onChange(e.target.checked ? [...selected, item.code] : selected.filter((x) => x !== item.code))}
                style={{ marginTop: 4 }}
              />
              <span style={{ flex: 1 }}>
                <strong>{item.name}</strong>
                {item.custom && <small className="muted" style={{ marginLeft: 6, color: '#3182ce', fontWeight: 600 }}>[自定义]</small>}
                {cfg && !enabled && <small className="muted" style={{ marginLeft: 6, color: '#999' }}>(已禁用)</small>}
                <br />
                <small className="muted">{item.description}</small>
                <div style={{ marginTop: 4, display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                  {cfg && (
                    <small className="muted">{cfg.rules?.length || 0} 个规则</small>
                  )}
                  {onConfigEdit && cfg && (
                    <button
                      className="btn btn-subtle"
                      style={{ fontSize: 11, padding: '2px 6px' }}
                      onClick={(e) => { e.stopPropagation(); e.preventDefault(); onConfigEdit(cfg) }}
                    >配置</button>
                  )}
                </div>
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
