import { useEffect, useMemo, useState } from 'react'
import { mcpAi } from '../api'
import ToolRiskTag from '../components/ToolRiskTag'

export default function McpToolsPage() {
  const [tools, setTools] = useState<any[]>([])
  const [selected, setSelected] = useState<any>(null)
  const [category, setCategory] = useState('')
  const [risk, setRisk] = useState('')
  const [loading, setLoading] = useState(false)
  const load = () => {
    setLoading(true)
    mcpAi.tools({ category, risk, include_schema: true, limit: 300 }).then((res: any) => {
      const data = res?.data || res
      setTools(data?.tools || [])
    }).finally(() => setLoading(false))
  }
  useEffect(load, [category, risk])
  const categories = useMemo(() => Array.from(new Set(tools.map((t) => t.category).filter(Boolean))), [tools])
  return (
    <div className="page-shell">
      <div className="page-header">
        <div><h1>MCP 工具目录</h1><p>查看工具风险、AI 调用策略、输入输出 Schema 和示例提示。</p></div>
        <button className="btn-primary" onClick={load}>刷新</button>
      </div>
      <div className="toolbar">
        <select value={category} onChange={(e) => setCategory(e.target.value)}><option value="">全部分类</option>{categories.map((c) => <option key={c} value={c}>{c}</option>)}</select>
        <select value={risk} onChange={(e) => setRisk(e.target.value)}><option value="">全部风险</option><option value="low">low</option><option value="medium">medium</option><option value="high">high</option><option value="critical">critical</option></select>
      </div>
      <div className="grid two-columns">
        <div className="card">
          <h2>工具列表 {loading ? '加载中...' : `(${tools.length})`}</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>工具</th><th>分类</th><th>风险</th><th>AI</th></tr></thead>
              <tbody>
                {tools.map((t) => (
                  <tr key={t.name} onClick={() => setSelected(t)} style={{ cursor: 'pointer' }}>
                    <td><strong>{t.name}</strong><div className="muted">{t.title || t.description}</div></td>
                    <td>{t.category}</td>
                    <td><ToolRiskTag risk={t.risk} approval={t.requires_human_approval} /></td>
                    <td>{t.ai_auto_callable ? '自动' : t.ai_callable ? '可用' : '禁用'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div className="card">
          <h2>工具详情</h2>
          {!selected ? <p className="muted">点击左侧工具查看详情。</p> : (
            <div>
              <h3>{selected.name}</h3>
              <p>{selected.description}</p>
              <div className="kv-grid">
                <span>AI 等级</span><strong>{selected.ai_level || '-'}</strong>
                <span>AI 可调用</span><strong>{String(selected.ai_callable)}</strong>
                <span>AI 自动调用</span><strong>{String(selected.ai_auto_callable)}</strong>
                <span>人工审批</span><strong>{String(selected.requires_human_approval)}</strong>
                <span>敏感级别</span><strong>{selected.data_sensitivity || '-'}</strong>
              </div>
              <h4>使用场景</h4>
              <ul>{(selected.recommended_use_cases || []).map((x: string) => <li key={x}>{x}</li>)}</ul>
              {!(selected.recommended_use_cases || []).length && <p className="muted">未提供推荐使用场景。</p>}
              <h4>示例问题</h4>
              <ul>{(selected.example_prompts || []).map((x: string) => <li key={x}>{x}</li>)}</ul>
              {(selected.keywords || []).length > 0 && <><h4>检索关键词</h4><p>{selected.keywords.join('、')}</p></>}
              <h4>Input Schema</h4>
              <pre className="code-block">{JSON.stringify(selected.input_schema || {}, null, 2)}</pre>
              <h4>Output Schema</h4>
              <pre className="code-block">{JSON.stringify(selected.output_schema || {}, null, 2)}</pre>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
