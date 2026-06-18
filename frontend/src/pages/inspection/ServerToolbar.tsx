import { isServerInspectable } from './inspectionHelpers'

export type ServerToolbarProps = {
  filter: string
  servers: any[]
  selectedServerId: string
  activeServerCount: number
  selectedTargetCount: number
  running: boolean
  onFilterChange: (next: string) => void
  onSelectOnlineServers: () => void
  onClearSelection: () => void
  onExpandAll: () => void
  onCollapseAll: () => void
  onSelectedServerChange: (next: string) => void
  onRunServer: () => void
  onRunSelectedServers: () => void
}

export function ServerToolbar({
  filter,
  servers,
  selectedServerId,
  activeServerCount,
  selectedTargetCount,
  running,
  onFilterChange,
  onSelectOnlineServers,
  onClearSelection,
  onExpandAll,
  onCollapseAll,
  onSelectedServerChange,
  onRunServer,
  onRunSelectedServers,
}: ServerToolbarProps) {
  return (
          <div className="cc-toolbar">
            <div className="cc-toolbar-search">
              <span className="cc-toolbar-search-icon">⌕</span>
              <input
                value={filter}
                onChange={(e) => onFilterChange(e.target.value)}
                placeholder="搜索名称 / IP"
                aria-label="搜索服务器"
              />
              {filter && (
                <button className="cc-icon-btn" style={{ padding: '0 8px', height: 20, fontSize: 10 }} onClick={() => onFilterChange('')}>清空</button>
              )}
            </div>
            <div className="cc-toolbar-divider" />
            <div className="cc-toolbar-actions">
              <button className="cc-icon-btn" onClick={onSelectOnlineServers} disabled={running || activeServerCount === 0}>全选在线</button>
              <button className="cc-icon-btn" onClick={onClearSelection}>清空选择</button>
              <button className="cc-icon-btn" onClick={onExpandAll}>展开全部</button>
              <button className="cc-icon-btn" onClick={onCollapseAll}>收起全部</button>
            </div>
            <div className="cc-toolbar-cta">
              <span className="cc-toolbar-summary">单台</span>
              <select className="cc-select" value={selectedServerId} onChange={(e) => onSelectedServerChange(e.target.value)}>
                {servers.filter(isServerInspectable).map((s) => <option key={s.id || s.name} value={s.id || s.name}>{s.name || s.id}</option>)}
              </select>
              <button className="cc-icon-btn cc-icon-btn--success" onClick={onRunServer} disabled={running || !selectedServerId}>{running ? '巡检中…' : '巡检单台'}</button>
              <button className="cc-icon-btn" onClick={onRunSelectedServers} disabled={running || selectedTargetCount === 0}>{running ? '巡检中…' : `巡检选中 (${selectedTargetCount})`}</button>
            </div>
          </div>
  )
}
