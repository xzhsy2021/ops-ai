export type ServerCategoryChipsProps = {
  items: any[]
  itemConfigs: any[]
  selected: string[]
  onChange: (next: string[]) => void
  onConfigEdit?: (item: any) => void
}

export function ServerCategoryChips({ items, itemConfigs, selected, onChange, onConfigEdit }: ServerCategoryChipsProps) {
  return (
    <div className="inspection-category-chips">
      <span className="chip-label">?????</span>
      {items.map((item: any) => {
        const cfg = itemConfigs.find((c: any) => c.item_code === item.code)
        const enabled = cfg ? cfg.enabled !== false : true
        const active = selected.includes(item.code)
        const chipClass = `chip${active ? ' chip--active' : ''}${enabled ? '' : ' chip--disabled'}`
        const nextSelection = active ? selected.filter((x) => x !== item.code) : [...selected, item.code]
        return (
          <span
            key={item.code}
            className={chipClass}
            title={enabled ? item.description : '???'}
            onClick={() => enabled && onChange(nextSelection)}
            role="button"
            tabIndex={enabled ? 0 : -1}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); enabled && onChange(nextSelection) } }}
          >
            {item.name}
            {cfg && <small>{cfg.rules?.length || 0} ??</small>}
            {onConfigEdit && cfg && (
              <span className="chip-actions">
                <button onClick={(e) => { e.stopPropagation(); onConfigEdit(cfg) }}>??</button>
              </span>
            )}
          </span>
        )
      })}
      {items.length > 0 && (
        <>
          <div className="toolbar-divider" />
          <button className="btn btn-subtle" style={{ padding: '3px 10px', fontSize: 11 }} onClick={() => onChange(items.map((x: any) => x.code))}>??</button>
          <button className="btn btn-subtle" style={{ padding: '3px 10px', fontSize: 11 }} onClick={() => onChange([])}>??</button>
          <span className="toolbar-summary" style={{ marginLeft: 'auto' }}><strong>{selected.length}</strong> / {items.length} ??</span>
        </>
      )}
    </div>
  )
}
