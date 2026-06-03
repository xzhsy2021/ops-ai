import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { aiAnalysis } from '../api'
import AiEvidenceView from '../components/AiEvidenceView'

export default function AiAnalysisDetailPage() {
  const { id } = useParams()
  const [data, setData] = useState<any>(null)
  const [report, setReport] = useState<any>(null)
  useEffect(() => { if (id) aiAnalysis.get(id).then((res: any) => setData(res?.data || res)) }, [id])
  const gen = () => { if (id) aiAnalysis.generateReport(id).then((res: any) => setReport(res?.data || res)) }
  return (
    <div className="page-shell">
      <div className="page-header"><div><h1>AI 分析详情</h1><p>{data?.summary || id}</p></div><button className="btn-primary" onClick={gen}>生成报告</button></div>
      {report && <div className="card"><strong>报告已生成：</strong>{report?.report?.id || JSON.stringify(report)}</div>}
      {data ? <><div className="card"><pre className="code-block">{JSON.stringify(data, null, 2)}</pre></div><AiEvidenceView data={data} /></> : <div className="card">加载中...</div>}
    </div>
  )
}
