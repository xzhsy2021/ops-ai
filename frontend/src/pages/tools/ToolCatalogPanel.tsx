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
  selectedToolName,
  toolDetail,
  onSelectTool,
  onOpenPlayground,
}: {
  tools: ToolInfo[]
  categories: string[]
  loading?: boolean
  selectedToolName?: string
  toolDetail?: any
  onSelectTool: (tool: ToolInfo) => void
  onOpenPlayground?: (tool: ToolInfo) => void
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

  const selectedTool = filtered.find((tool) => tool.name === selectedToolName) || tools.find((tool) => tool.name === selectedToolName) || filtered[0] || null
  const copyToolName = async () => {
    if (!selectedTool?.name) return
    await navigator.clipboard?.writeText(selectedTool.name)
  }

  return (
    <div className="tool-directory-workspace">
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
        </div>
        <div className="tool-list">
          {loading && tools.length === 0 && <div className="tool-empty">加载中...</div>}
          {!loading && filtered.length === 0 && <div className="tool-empty">没有匹配的工具</div>}
          {filtered.map((tool) => (
            <button key={tool.name} className={`tool-card ${selectedTool?.name === tool.name ? 'active' : ''}`} onClick={() => onSelectTool(tool)}>
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

      <div className="card tool-directory-detail">
        {!selectedTool ? (
          <div className="tool-empty">选择左侧工具查看 Schema、权限和调用入口。</div>
        ) : (
          <>
            <div className="card-header">
              <div>
                <h2>{selectedTool.title || selectedTool.name}</h2>
                <span>{selectedTool.description}</span>
              </div>
              <div className="section-actions">
                <button className="btn btn-subtle" type="button" onClick={copyToolName}>复制工具名</button>
                {onOpenPlayground && <button className="btn primary" type="button" onClick={() => onOpenPlayground(selectedTool)}>调试</button>}
              </div>
            </div>
            <div className="tag-row">
              <span className={`tag ${selectedTool.write ? 'tag-danger' : selectedTool.risk === 'high' || selectedTool.risk === 'critical' ? 'tag-warning' : 'tag-success'}`}>{riskLabel(selectedTool.risk)}</span>
              {selectedTool.write && <span className="tag tag-danger">写操作</span>}
              {selectedTool.requires_confirmation && <span className="tag tag-warning">需确认</span>}
              {selectedTool.category && <span className="tag">{selectedTool.category}</span>}
              {!selectedTool.available && <span className="tag tag-danger">不可用</span>}
            </div>
            {selectedTool.blocked_reason && (
              <div className="alert alert-warning">
                <strong>阻断原因：</strong>{selectedTool.blocked_reason}
              </div>
            )}
            <div className="tool-detail-meta">
              <span>权限范围 <strong>{(selectedTool.scopes || []).join(', ') || '-'}</strong></span>
              <span>状态 <strong>{selectedTool.available === false ? '不可用' : '可用'}</strong></span>
              {toolDetail?.recent_stats && <span>最近成功率 <strong>{toolDetail.recent_stats.success_rate != null ? `${Math.round(toolDetail.recent_stats.success_rate * 100)}%` : '-'}</strong></span>}
              {toolDetail?.recent_stats && <span>平均耗时 <strong>{toolDetail.recent_stats.avg_duration_ms || '-'}ms</strong></span>}
            </div>
            <div className="tool-schema-grid">
              <div>
                <strong>输入 Schema</strong>
                <pre className="code-block small">{JSON.stringify(selectedTool.input_schema || {}, null, 2)}</pre>
              </div>
              <div>
                <strong>输出 Schema</strong>
                <pre className="code-block small">{JSON.stringify(selectedTool.output_schema || {}, null, 2)}</pre>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
