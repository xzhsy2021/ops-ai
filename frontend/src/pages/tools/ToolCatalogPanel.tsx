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

export function ToolCatalogPanel({
  tools,
  categories,
  loading,
  onSelectTool,
}: {
  tools: ToolInfo[]
  categories: string[]
  loading?: boolean
  onSelectTool: (tool: ToolInfo) => void
}) {
  const [keyword, setKeyword] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [riskFilter, setRiskFilter] = useState('')

  const filtered = tools.filter((t) => {
    if (categoryFilter && t.category !== categoryFilter) return false
    if (riskFilter && t.risk !== riskFilter) return false
    if (keyword.trim()) {
      const q = keyword.trim().toLowerCase()
      return `${t.name} ${t.title || ''} ${t.description} ${t.category} ${t.risk}`.toLowerCase().includes(q)
    }
    return true
  })

  return (
    <div className="card" style={{ display: 'grid', gap: 12 }}>
      <div className="card-header">
        <div>
          <h2>工具目录</h2>
          <span>{tools.length} 个工具，当前筛选 {filtered.length} 个</span>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <input
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder="搜索工具..."
          style={{ flex: 1, minWidth: 160 }}
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
      </div>
      <div className="tool-list">
        {loading && tools.length === 0 && <div style={{ padding: 24, color: 'var(--text-muted)', textAlign: 'center' }}>加载中...</div>}
        {!loading && filtered.length === 0 && <div style={{ padding: 24, color: 'var(--text-muted)', textAlign: 'center' }}>没有匹配的工具</div>}
        {filtered.map((tool) => (
          <button key={tool.name} className="tool-card" onClick={() => onSelectTool(tool)}>
            <strong>{tool.title || tool.name}</strong>
            <small>{tool.description}</small>
            <div className="tag-row">
              <span className={`tag ${tool.write ? 'tag-danger' : tool.risk === 'high' || tool.risk === 'critical' ? 'tag-warning' : 'tag-success'}`}>
                {riskLabel(tool.risk)}
              </span>
              {tool.category && <span className="tag">{tool.category}</span>}
              {!tool.available && <span className="tag tag-danger">不可用</span>}
              {tool.requires_confirmation && <span className="tag tag-warning">需确认</span>}
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}