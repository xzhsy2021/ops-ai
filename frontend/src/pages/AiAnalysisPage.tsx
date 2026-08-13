import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { aiAnalysis } from '../api'

export default function AiAnalysisPage() {
  const [items, setItems] = useState<any[]>([])
  useEffect(() => { aiAnalysis.list({ limit: 100 }).then((res: any) => setItems((res?.data || res)?.items || [])) }, [])
  return (
    <div className="page-shell">
      <div className="page-header"><div><h1>AI 分析记录</h1><p>查看已沉淀的 AI 分析、事实、推断、建议和证据链。</p></div></div>
      <div className="card table-wrap"><table className="data-table data-table--compact">
        <thead><tr>
          <th style={{ width: 150 }}>时间</th>
          <th style={{ width: 100 }}>类型</th>
          <th style={{ minWidth: 200 }}>目标</th>
          <th>摘要</th>
          <th style={{ width: 80 }}>置信度</th>
        </tr></thead>
        <tbody>
          {items.map((x) => (
            <tr key={x.id}>
              <td><span className="ellipsis" title={x.created_at} style={{ maxWidth: 140 }}>{x.created_at}</span></td>
              <td>{x.analysis_type}</td>
              <td><span className="ellipsis" title={`${x.target_type}:${x.target_id}`} style={{ maxWidth: 280 }}>{x.target_type}:{x.target_id}</span></td>
              <td>
                <Link to={`/ai/analysis/${x.id}`} className="ellipsis" style={{ maxWidth: 360, display: 'inline-block' }} title={x.summary || x.id}>
                  {x.summary || x.id}
                </Link>
              </td>
              <td>{x.confidence || '-'}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </div>
  )
}
