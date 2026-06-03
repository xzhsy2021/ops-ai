import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { aiAnalysis } from '../api'

export default function AiAnalysisPage() {
  const [items, setItems] = useState<any[]>([])
  useEffect(() => { aiAnalysis.list({ limit: 100 }).then((res: any) => setItems((res?.data || res)?.items || [])) }, [])
  return (
    <div className="page-shell">
      <div className="page-header"><div><h1>AI 分析记录</h1><p>查看已沉淀的 AI 分析、事实、推断、建议和证据链。</p></div></div>
      <div className="card table-wrap"><table><thead><tr><th>时间</th><th>类型</th><th>目标</th><th>摘要</th><th>置信度</th></tr></thead><tbody>
        {items.map((x) => <tr key={x.id}><td>{x.created_at}</td><td>{x.analysis_type}</td><td>{x.target_type}:{x.target_id}</td><td><Link to={`/ai/analysis/${x.id}`}>{x.summary || x.id}</Link></td><td>{x.confidence || '-'}</td></tr>)}
      </tbody></table></div>
    </div>
  )
}
