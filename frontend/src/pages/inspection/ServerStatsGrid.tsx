export type ServerStatsGridProps = {
  serverCount: number
  activeServerCount: number
  disabledServerCount: number
  selectedCount: number
}

export function ServerStatsGrid({ serverCount, activeServerCount, disabledServerCount, selectedCount }: ServerStatsGridProps) {
  return (
          <div className="cc-stat-grid">
            <div className="cc-stat-card">
              <div className="cc-stat-icon">⬡</div>
              <div className="cc-stat-body"><span>服务器总数</span><strong>{serverCount}</strong></div>
            </div>
            <div className="cc-stat-card cc-stat-card--ok">
              <div className="cc-stat-icon">●</div>
              <div className="cc-stat-body"><span>在线 / 启用</span><strong>{activeServerCount}</strong></div>
            </div>
            <div className="cc-stat-card cc-stat-card--warn">
              <div className="cc-stat-icon">○</div>
              <div className="cc-stat-body"><span>停用 / 离线</span><strong>{disabledServerCount}</strong></div>
            </div>
            <div className="cc-stat-card cc-stat-card--info">
              <div className="cc-stat-icon">✓</div>
              <div className="cc-stat-body"><span>当前已选</span><strong>{selectedCount}</strong></div>
            </div>
          </div>
  )
}
