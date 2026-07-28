import { useState } from 'react'

export type ToolInfo = {
  name: string
  title?: string
  description: string
  risk: string
  category: string
  scopes: string[]
  write: boolean
  read_only?: boolean
  requires_confirmation: boolean
  enabled?: boolean
  available?: boolean
  blocked_reason?: string
  input_schema?: any
  output_schema?: any
}

function riskLabel(risk?: string) {
  const map: Record<string, string> = {
    critical: '严重风险', high: '高风险', medium: '中风险',
    low: '低风险', read: '只读', safe: '安全',
    dangerous: '需确认', blocked: '已拦截',
  }
  return map[risk || ''] || risk || '未分级'
}

function riskTone(risk?: string, write?: boolean) {
  if (write) return 'danger'
  if (risk === 'critical' || risk === 'high') return 'warning'
  return 'success'
}

export function ToolCatalogPanel({
  tools,
  categories,
  loading,
  selectedToolName,
  onSelectTool,
  onOpenDetail,
  onOpenPlayground,
}: {
  tools: ToolInfo[]
  categories: string[]
  loading?: boolean
  selectedToolName?: string
  onSelectTool?: (tool: ToolInfo) => void
  onOpenDetail?: (tool: ToolInfo) => void
  onOpenPlayground?: (tool: ToolInfo) => void
}) {
  const [keyword, setKeyword] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [riskFilter, setRiskFilter] = useState('')
  const [writeFilter, setWriteFilter] = useState<'all' | 'write' | 'read'>('all')
  const [availableFilter, setAvailableFilter] = useState<'all' | 'available' | 'blocked'>('all')

  const filtered = tools.filter((t) => {
    if (categoryFilter && t.category !== categoryFilter) return false
    if (riskFilter && t.risk !== riskFilter) return false
    if (writeFilter === 'write' && !t.write) return false
    if (writeFilter === 'read' && t.write) return false
    if (availableFilter === 'available' && t.available === false) return false
    if (availableFilter === 'blocked' && t.available !== false) return false
    if (keyword.trim()) {
      const q = keyword.trim().toLowerCase()
      return `${t.name} ${t.title || ''} ${t.description} ${t.category} ${t.risk}`.toLowerCase().includes(q)
    }
    return true
  })

  return (
    <div className="card tool-directory-master">
      <div className="card-header">
        <div>
          <h2>工具目录</h2>
          <span>{tools.length} 个工具，当前筛选 {filtered.length} 个</span>
        </div>
      </div>
      <div className="tool-directory-filters">
        <input
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder="搜索工具..."
        />
        <select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}>
          <option value="">全部分类</option>
          {categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={riskFilter} onChange={(e) => setRiskFilter(e.target.value)}>
          <option value="">全部风险</option>
          <option value="critical">严重风险</option>
          <option value="high">高风险</option>
          <option value="medium">中风险</option>
          <option value="low">低风险</option>
          <option value="read">只读</option>
        </select>
        <select value={writeFilter} onChange={(e) => setWriteFilter(e.target.value as any)}>
          <option value="all">全部读写</option>
          <option value="write">仅写操作</option>
          <option value="read">仅只读</option>
        </select>
        <select value={availableFilter} onChange={(e) => setAvailableFilter(e.target.value as any)}>
          <option value="all">全部可用性</option>
          <option value="available">仅可用</option>
          <option value="blocked">已阻断</option>
        </select>
      </div>
      <div className="tool-list">
        {loading && tools.length === 0 && <div className="tool-empty">加载中...</div>}
        {!loading && filtered.length === 0 && <div className="tool-empty">没有匹配的工具</div>}
        {filtered.map((tool) => (
          <div
            key={tool.name}
            className={`tool-card ${selectedToolName === tool.name ? 'active' : ''}`}
          >
            <button className="tool-card-main" onClick={() => onOpenDetail?.(tool)}>
              <strong>{tool.title || tool.name}</strong>
              <small>{tool.description}</small>
              <div className="tag-row">
                <span className={`tag tag-${riskTone(tool.risk, tool.write)}`}>
                  {riskLabel(tool.risk)}
                </span>
                {tool.category && <span className="tag">{tool.category}</span>}
                {!tool.available && <span className="tag tag-danger">不可用</span>}
                {tool.requires_confirmation && <span className="tag tag-warning">需确认</span>}
                {tool.write && <span className="tag tag-danger">写操作</span>}
              </div>
            </button>
            <div className="tool-card-actions">
              {onSelectTool && (
                <button className="btn btn-subtle btn-sm" onClick={() => onSelectTool(tool)}>
                  选择
                </button>
              )}
              {onOpenPlayground && (
                <button className="btn btn-primary btn-sm" onClick={() => onOpenPlayground(tool)}>
                  调试
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
