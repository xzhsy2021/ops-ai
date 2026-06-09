import { useState } from 'react'
import { mcpAi } from '../api'
import AiEvidenceView from '../components/AiEvidenceView'

const workflows = [
  { tool: 'ops.workflow.inspect', title: '服务器巡检工作流', fields: ['request'] },
  { tool: 'ops.workflow.generate_project_health_brief', title: '项目健康分析', fields: ['project_id'] },
  { tool: 'ops.workflow.analyze_failed_deploy', title: '发布失败分析', fields: ['deployment_id'] },
  { tool: 'ops.workflow.inspect_project_security', title: '项目安全巡检分析', fields: ['project_id'] },
  { tool: 'ops.workflow.triage_open_risks', title: '未闭环风险分流', fields: [] },
  { tool: 'ops.workflow.generate_monthly_ops_report', title: '月度运维报告', fields: ['month'] },
]

export default function AiWorkflowsPage() {
  const [tool, setTool] = useState(workflows[0].tool)
  const [form, setForm] = useState<any>({ time_range: '7d', save_analysis: true, generate_report: false })
  const [result, setResult] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const current = workflows.find((x) => x.tool === tool) || workflows[0]
  const run = () => {
    setLoading(true)
    mcpAi.workflow(tool, form).then((res: any) => setResult((res?.data || res)?.result || res?.data || res)).finally(() => setLoading(false))
  }
  return (
    <div className="page-shell">
      <div className="page-header"><div><h1>AI 工作流</h1><p>通过场景化 MCP Workflow 生成证据化分析，避免 AI 随机调用底层工具。</p></div><button className="btn-primary" onClick={run} disabled={loading}>{loading ? '执行中...' : '执行工作流'}</button></div>
      <div className="grid two-columns">
        <div className="card">
          <h2>工作流参数</h2>
          <label>工作流</label>
          <select value={tool} onChange={(e) => setTool(e.target.value)}>{workflows.map((w) => <option key={w.tool} value={w.tool}>{w.title}</option>)}</select>
          {current.fields.map((field) => <div key={field}><label>{field}</label><input value={form[field] || ''} onChange={(e) => setForm({ ...form, [field]: e.target.value })} placeholder={field} /></div>)}
          <label>时间范围</label><input value={form.time_range || ''} onChange={(e) => setForm({ ...form, time_range: e.target.value })} />
          <label><input type="checkbox" checked={!!form.save_analysis} onChange={(e) => setForm({ ...form, save_analysis: e.target.checked })} /> 保存 AI 分析</label>
        </div>
        <div className="card">
          <h2>执行结果</h2>
          {!result ? <p className="muted">暂无结果。</p> : <><p>{result.summary}</p><pre className="code-block">{JSON.stringify(result, null, 2)}</pre></>}
        </div>
      </div>
      {result && <AiEvidenceView data={result} />}
    </div>
  )
}
