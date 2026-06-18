import { isServerInspectable } from './inspectionHelpers'

export type ServerGroupChipsProps = {
  groupedServers: Record<string, any[]>
  selectedGroups: string[]
  onSelectedGroupsChange: (next: string[]) => void
}

export function ServerGroupChips({ groupedServers, selectedGroups, onSelectedGroupsChange }: ServerGroupChipsProps) {
  if (Object.keys(groupedServers).length === 0) return null

  return (
            <div className="cc-group-chip-row">
              <span className="quick-label">按分组：</span>
              {Object.entries(groupedServers).map(([group, list]) => {
                const inspectableCount = (list as any[]).filter(isServerInspectable).length
                const active = selectedGroups.includes(group)
                return (
                  <span
                    key={group}
                    className={`cc-group-chip${active ? ' cc-group-chip--active' : ''}`}
                    onClick={() => {
                      const next = active ? selectedGroups.filter((x) => x !== group) : Array.from(new Set([...selectedGroups, group]))
                      onSelectedGroupsChange(next)
                    }}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); (e.currentTarget as HTMLSpanElement).click() } }}
                  >
                    {group}
                    <span className="cc-group-chip-count">{inspectableCount}</span>
                  </span>
                )
              })}
              {selectedGroups.length > 0 && (
                <button className="cc-icon-btn" style={{ marginLeft: 4, height: 22, padding: '0 8px', fontSize: 11 }} onClick={() => onSelectedGroupsChange([])}>清空分组</button>
              )}
            </div>
  )
}
