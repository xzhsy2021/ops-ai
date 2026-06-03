import { useEffect, useState } from 'react'
import { mcpAi } from '../api'
import ToolRiskTag from '../components/ToolRiskTag'

export default function McpAuditPage() {
  const [items, setItems] = useState<any[]>([])
  useEffect(() => {
    mcpAi.workflow('ops.list_tool_calls', { limit: 100 }).then((res: any) => {
      const data = res?.data || res
      const result = data?.result || data
      setItems(Array.isArray(result) ? result : result?.items || [])
    }).catch(() => setItems([]))
  }, [])
  return (
    <div className="page-shell">
      <div className="page-header"><div><h1>MCP 调用审计</h1><p>查看 AI/MCP 工具调用、风险等级、状态和耗时。</p></div></div>
      <div className="card table-wrap">
        <table><thead><tr><th>时间</th><th>工具</th><th>状态</th><th>风险</th><th>来源</th><th>耗时</th></tr></thead><tbody>
          {items.map((x) => <tr key={x.id}><td>{x.created_at}</td><td>{x.tool_name}</td><td>{x.status}</td><td><ToolRiskTag risk={x.risk_level} /></td><td>{x.client_name || x.username || x.token_owner || '-'}</td><td>{x.duration_ms || '-'} ms</td></tr>)}
        </tbody></table>
      </div>
    </div>
  )
}
