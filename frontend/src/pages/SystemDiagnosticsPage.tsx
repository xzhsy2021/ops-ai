import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ROUTES } from '../routes'
import { systemHealth } from '../api'
import { diagnosticsApi } from '../api/diagnostics'
import { ErrorState, LoadingState, PageSection } from '../components/ui'
import type { AiDiagnosticsPayload, DiagnosticCheck, DiagnosticsPayload, RecentErrorItem, Recommendation } from '../types/diagnostics'

function getData(res: any) {
  return res?.data ?? res
}

function statusLabel(status?: string) {
  if (status === 'ok' || status === 'healthy' || status === 'low') return '正常'
  if (status === 'warn' || status === 'degraded' || status === 'medium') return '警告'
  if (status === 'error' || status === 'unhealthy' || status === 'high' || status === 'critical') return '异常'
  return status || '-'
}

function statusColor(status?: string) {
  if (status === 'ok' || status === 'healthy' || status === 'low') return 'var(--success)'
  if (status === 'warn' || status === 'degraded' || status === 'medium') return 'var(--warning)'
  if (status === 'error' || status === 'unhealthy' || status === 'high' || status === 'critical') return 'var(--danger)'
  return 'var(--text-muted)'
}

function StatusPill({ status }: { status?: string }) {
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      borderRadius: '999px',
      padding: '3px 9px',
      fontSize: '12px',
      fontWeight: 700,
      background: 'var(--bg-page)',
      color: statusColor(status),
      border: '1px solid var(--border-strong)',
      whiteSpace: 'nowrap',
    }}>
      {statusLabel(status)}
    </span>
  )
}

function CheckRow({ name, item }: { name: string; item: DiagnosticCheck }) {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '180px minmax(0, 1fr) 80px',
      gap: '12px',
      alignItems: 'start',
      padding: '10px 0',
      borderBottom: '1px solid var(--border)',
      fontSize: '13px',
    }}>
      <strong style={{ color: 'var(--text-primary)' }}>{name}</strong>
      <div style={{ minWidth: 0 }}>
        <div style={{ color: 'var(--text-secondary)' }}>{item?.message || '-'}</div>
        {item?.path && <div style={{ color: 'var(--text-muted)', fontFamily: 'monospace', wordBreak: 'break-all', marginTop: '4px' }}>{item.path}</div>}
        {item?.items && typeof item.items === 'object' && (
          <div style={{ display: 'grid', gap: '6px', marginTop: '8px' }}>
            {Object.entries(item.items).map(([key, value]: [string, any]) => (
              <div key={key} style={{ background: 'var(--bg-page)', borderRadius: '8px', padding: '8px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px' }}>
                  <span>{key}</span><StatusPill status={value?.status} />
                </div>
                <div style={{ color: 'var(--text-muted)', fontFamily: 'monospace', wordBreak: 'break-all', marginTop: '4px' }}>{value?.path || value?.message || ''}</div>
              </div>
            ))}
          </div>
        )}
      </div>
      <StatusPill status={item?.status} />
    </div>
  )
}

function KeyValue({ label, value }: { label: string; value?: any }) {
  return (
    <div style={{ display: 'grid', gap: '4px', minWidth: 0 }}>
      <div style={{ color: 'var(--text-muted)', fontSize: '12px' }}>{label}</div>
      <strong style={{ fontSize: '13px', wordBreak: 'break-all', fontFamily: typeof value === 'string' && value.includes('/') ? 'monospace' : undefined }}>{value ?? '-'}</strong>
    </div>
  )
}

function Recommendations({ items }: { items: Recommendation[] }) {
  if (!items?.length) return null
  return (
    <PageSection title="问题建议" subtitle="根据健康检查、构建信息、最近错误与 MCP 自检生成的下一步处理建议。">
      <div style={{ display: 'grid', gap: '10px' }}>
        {items.map((item) => (
          <div key={item.key} style={{ border: '1px solid var(--border)', borderRadius: '12px', padding: '12px', background: 'var(--bg-page)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '10px', alignItems: 'flex-start' }}>
              <div>
                <strong>{item.title}</strong>
                <div style={{ color: 'var(--text-muted)', marginTop: '4px', fontSize: '13px' }}>{item.reason}</div>
              </div>
              <StatusPill status={item.severity} />
            </div>
            {item.actions?.length > 0 && (
              <ol style={{ margin: '10px 0 0 18px', padding: 0, color: 'var(--text-secondary)', fontSize: '13px' }}>
                {item.actions.map((action, index) => <li key={index} style={{ marginBottom: '4px' }}>{action}</li>)}
              </ol>
            )}
          </div>
        ))}
      </div>
    </PageSection>
  )
}

function RecentErrors({ items }: { items: RecentErrorItem[] }) {
  if (!items?.length) {
    return <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>未发现最近错误或警告日志。</div>
  }
  return (
    <div style={{ display: 'grid', gap: '8px' }}>
      {items.map((item, index) => (
        <details key={`${item.source}-${item.line_no}-${index}`} style={{ border: '1px solid var(--border)', borderRadius: '10px', padding: '10px', background: 'var(--bg-page)' }}>
          <summary style={{ cursor: 'pointer', display: 'grid', gridTemplateColumns: '80px minmax(0, 1fr) 120px', gap: '10px', alignItems: 'center', fontSize: '13px' }}>
            <StatusPill status={item.level} />
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.summary || '-'}</span>
            <span style={{ color: 'var(--text-muted)', textAlign: 'right' }}>{item.source || '-'}</span>
          </summary>
          <div style={{ display: 'grid', gap: '6px', marginTop: '10px', fontSize: '12px', color: 'var(--text-muted)' }}>
            <div>时间：{item.time || '-'}</div>
            <div>模块：{item.module || '-'}</div>
            <div>位置：{item.source || '-'}:{item.line_no || '-'}</div>
            <pre style={{ margin: 0, whiteSpace: 'pre-wrap', color: 'var(--text-secondary)' }}>{item.raw || item.summary || '-'}</pre>
          </div>
        </details>
      ))}
    </div>
  )
}

function AiDiagnosticsPanel({ data, loading, error, onAnalyze }: { data: AiDiagnosticsPayload | null; loading: boolean; error: string; onAnalyze: (focus?: string) => void }) {
  return (
    <PageSection title="AI 诊断助手" subtitle="基于诊断报告、最近错误、MCP 工具目录、工具调用审计和任务中心生成只读分析，不会执行发布、恢复、删除、SQL 或终端动作。">
      <div style={{ display: 'grid', gap: '12px' }}>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <button className="btn btn-primary" onClick={() => onAnalyze('general')} disabled={loading}>{loading ? '分析中...' : '运行只读分析'}</button>
          <button className="btn" onClick={() => onAnalyze('frontend')} disabled={loading} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)' }}>聚焦前端/白屏</button>
          <button className="btn" onClick={() => onAnalyze('mcp')} disabled={loading} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)' }}>聚焦 MCP</button>
          <button className="btn" onClick={() => onAnalyze('deploy')} disabled={loading} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)' }}>聚焦发布</button>
          <button className="btn" onClick={() => onAnalyze('backup')} disabled={loading} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)' }}>聚焦备份恢复</button>
        </div>
        {error && <ErrorState title="AI 诊断分析失败" description={error} />}
        {data ? (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '10px' }}>
              <KeyValue label="分析状态" value={statusLabel(data.status)} />
              <KeyValue label="最高风险" value={statusLabel(data.severity)} />
              <KeyValue label="发现项" value={data.summary?.finding_count ?? 0} />
              <KeyValue label="阻断项" value={data.summary?.blocker_count ?? 0} />
              <KeyValue label="只读工具" value={data.summary?.safe_read_tools ?? 0} />
              <KeyValue label="受控写工具" value={data.summary?.guarded_write_tools ?? 0} />
            </div>
            <div style={{ border: '1px solid var(--border)', borderRadius: '12px', padding: '12px', background: 'var(--bg-page)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '10px', alignItems: 'center' }}>
                <strong>{data.headline || '诊断分析完成'}</strong>
                <StatusPill status={data.severity} />
              </div>
            </div>
            <div style={{ display: 'grid', gap: '10px' }}>
              {(data.findings || []).map((finding) => (
                <details key={finding.key} open={finding.severity === 'high' || finding.severity === 'critical'} style={{ border: '1px solid var(--border)', borderRadius: '12px', padding: '12px', background: 'var(--bg-page)' }}>
                  <summary style={{ cursor: 'pointer', display: 'flex', justifyContent: 'space-between', gap: '10px', alignItems: 'center' }}>
                    <strong>{finding.title}</strong>
                    <StatusPill status={finding.severity} />
                  </summary>
                  <div style={{ color: 'var(--text-secondary)', fontSize: '13px', marginTop: '8px' }}>{finding.detail}</div>
                  {finding.related_tools?.length ? <div style={{ marginTop: '8px', color: 'var(--text-muted)', fontSize: '12px' }}>相关只读工具：{finding.related_tools.join(' / ')}</div> : null}
                  {finding.next_steps?.length ? (
                    <ol style={{ margin: '8px 0 0 18px', padding: 0, color: 'var(--text-secondary)', fontSize: '13px' }}>
                      {finding.next_steps.map((step, index) => <li key={index}>{step}</li>)}
                    </ol>
                  ) : null}
                </details>
              ))}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '12px' }}>
              <div style={{ border: '1px solid var(--border)', borderRadius: '12px', padding: '12px', background: 'var(--bg-page)' }}>
                <strong>推荐 MCP 只读工具链</strong>
                <ol style={{ margin: '10px 0 0 18px', padding: 0, fontSize: '13px', color: 'var(--text-secondary)' }}>
                  {(data.safe_mcp_toolchain || []).map((step) => (
                    <li key={`${step.order}-${step.tool}`} style={{ marginBottom: '6px' }}>
                      <code>{step.tool}</code>：{step.purpose}
                    </li>
                  ))}
                </ol>
              </div>
              <div style={{ border: '1px solid var(--border)', borderRadius: '12px', padding: '12px', background: 'var(--bg-page)' }}>
                <strong>安全边界</strong>
                <div style={{ display: 'grid', gap: '6px', marginTop: '10px', fontSize: '13px', color: 'var(--text-secondary)' }}>
                  <div>自动允许风险：<strong>{(data.guardrails?.ai_auto_allowed_risks || []).join(' / ')}</strong></div>
                  <div>必须人工确认：<strong>{(data.guardrails?.requires_human_confirmation || []).join(' / ')}</strong></div>
                  <div>必须任务中心：<strong>{(data.guardrails?.must_use_task_center || []).join(' / ')}</strong></div>
                  <div>模式：<strong>{data.guardrails?.mode || 'read_only_analysis'}</strong></div>
                </div>
              </div>
            </div>
          </>
        ) : (
          <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>点击“运行只读分析”生成 AI 诊断视角与安全 MCP 工具链。</div>
        )}
      </div>
    </PageSection>
  )
}

const SectionCard = PageSection

export default function SystemDiagnosticsPage() {
  const [data, setData] = useState<DiagnosticsPayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [aiData, setAiData] = useState<AiDiagnosticsPayload | null>(null)
  const [aiLoading, setAiLoading] = useState(false)
  const [aiError, setAiError] = useState('')
  const [snapshot, setSnapshot] = useState<any>(null)

  const refresh = async () => {
    setLoading(true)
    setError('')
    try {
      const [res, snapRes] = await Promise.all([
        diagnosticsApi.get(),
        systemHealth.snapshot().catch(() => ({ data: null })),
      ])
      setData(getData(res))
      setSnapshot(snapRes?.data || snapRes || null)
    } catch (e: any) {
      setError(String(e?.message || e || '诊断失败'))
    } finally {
      setLoading(false)
    }
  }

  const copyReport = () => {
    const text = JSON.stringify(data || {}, null, 2)
    void navigator.clipboard?.writeText(text)
  }

  const analyzeWithAi = async (focus = 'general') => {
    setAiLoading(true)
    setAiError('')
    try {
      const res = await diagnosticsApi.aiDiagnostics({ mode: 'summary', focus })
      setAiData(getData(res))
    } catch (e: any) {
      setAiError(String(e?.message || e || 'AI 诊断分析失败'))
    } finally {
      setAiLoading(false)
    }
  }

  useEffect(() => { refresh() }, [])

  const sections = data?.sections || {}
  const overview = sections.overview || {}
  const startupChecks = sections.startup?.checks || {}
  const healthChecks = sections.health?.checks || {}
  const storage = sections.storage || {}
  const runtime = sections.runtime || {}
  const mcp = sections.mcp || {}
  const performance = sections.performance || {}
  const buildInfo = sections.build_info || {}
  const recentErrors = sections.recent_errors || {}
  const recommendations = data?.recommendations || []
  const currentUrl = typeof window !== 'undefined' ? window.location.href : '-'

  return (
    <div style={{ display: 'grid', gap: '20px' }}>
      <div className="page-header">
        <div>
          <h1>安装诊断</h1>
          <p>用于 Windows / 单进程 / 升级后的本地自检，快速定位前端构建、静态资源、数据库、Worker、最近错误和 MCP 问题。</p>
        </div>
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
          <Link className="btn" to={ROUTES.system} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)' }}>返回系统状态</Link>
          <Link className="btn btn-primary" to={ROUTES.inspection} style={{ textDecoration: 'none' }}>进入巡检中心</Link>
          <a className="btn" href={diagnosticsApi.reportExportUrl()} target="_blank" rel="noreferrer" style={{ background: 'var(--border-strong)', color: 'var(--text-primary)', textDecoration: 'none' }}>导出 JSON 报告</a>
          <a className="btn" href={diagnosticsApi.exportUrl()} target="_blank" rel="noreferrer" style={{ background: 'var(--border-strong)', color: 'var(--text-primary)', textDecoration: 'none' }}>导出诊断包</a>
          <button className="btn" onClick={copyReport} disabled={!data} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)' }}>复制诊断报告</button>
          <button className="btn btn-primary" onClick={refresh} disabled={loading}>{loading ? '诊断中...' : '重新诊断'}</button>
        </div>
      </div>

      {error && <ErrorState title="诊断失败" description={error} />}

      <SectionCard title="快速探针" subtitle="只读探针快捷入口，用于快速检查系统关键状态。写操作请通过任务中心或巡检中心执行。">
        <div style={{ display: 'grid', gap: '12px' }}>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            <Link className="btn" to={ROUTES.inspection} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)', textDecoration: 'none' }}>
              ◉ 巡检报告
            </Link>
            <Link className="btn" to={ROUTES.system} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)', textDecoration: 'none' }}>
              ◈ 系统状态
            </Link>
            <Link className="btn" to={ROUTES.tasks} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)', textDecoration: 'none' }}>
              ⌘ 任务中心
            </Link>
            <Link className="btn" to={ROUTES.reports} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)', textDecoration: 'none' }}>
              📊 报告中心
            </Link>
            <Link className="btn" to={ROUTES.audit} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)', textDecoration: 'none' }}>
              📋 审计日志
            </Link>
          </div>
          <div style={{ color: 'var(--text-muted)', fontSize: '12px', lineHeight: 1.5 }}>
            诊断页提供后端运行自检（数据库、密钥、目录、备份、Worker、MCP 工具目录等），均为只读操作。
            如需执行服务器巡检或项目巡检，请使用上方"巡检中心"入口。
          </div>
        </div>
      </SectionCard>

      <SectionCard title="巡检入口" subtitle="安装诊断用于定位平台自身运行问题；巡检中心用于服务器安全、项目安全、风险闭环和巡检报告。">
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', gap: '12px', alignItems: 'center' }}>
          <div style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>如果需要执行服务器巡检、项目巡检或综合巡检，请进入巡检中心。巡检结果会沉淀到风险问题和报告中心。</div>
          <Link className="btn btn-primary" to={ROUTES.inspection} style={{ textDecoration: 'none' }}>打开巡检中心</Link>
        </div>
      </SectionCard>

      {data ? (
        <>
          <div className="card" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '14px' }}>
            <div><div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>总体状态</div><strong style={{ fontSize: '22px', color: statusColor(data.status) }}>{statusLabel(data.status)}</strong></div>
            <div><div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>启动模式</div><strong>{data.startup_mode || overview.startup_mode || '-'}</strong></div>
            <div><div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>平台</div><strong>{data.platform?.system || data.platform?.platform || '-'}</strong></div>
            <div><div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>Python</div><strong>{data.python?.version || '-'}</strong></div>
            <div><div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>最近错误</div><strong style={{ color: recentErrors.error_count ? 'var(--danger)' : 'var(--success)' }}>{recentErrors.error_count ?? 0}</strong></div>
            <div><div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>MCP 工具</div><strong>{mcp.tool_count ?? '-'}</strong></div>
          </div>

          <SectionCard title="系统概览" subtitle="定位运行目录、数据库路径、日志目录、构建模式与当前访问上下文。">
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '12px' }}>
              <KeyValue label="当前访问地址" value={currentUrl} />
              <KeyValue label="运行环境" value={overview.environment || '-'} />
              <KeyValue label="项目根目录" value={overview.project_root || '-'} />
              <KeyValue label="APP_DATA_DIR" value={overview.app_data_dir || '-'} />
              <KeyValue label="数据库路径" value={overview.database_path || '-'} />
              <KeyValue label="备份目录" value={overview.backup_dir || '-'} />
              <KeyValue label="日志目录" value={overview.log_dir || '-'} />
              <KeyValue label="前端服务模式" value={overview.serve_frontend || '-'} />
            </div>
          </SectionCard>

          <Recommendations items={recommendations} />

          <AiDiagnosticsPanel data={aiData} loading={aiLoading} error={aiError} onAnalyze={analyzeWithAi} />

          <SectionCard title="最近错误与警告" subtitle={`扫描 ${recentErrors.scanned_files?.length || 0} 个日志文件，错误 ${recentErrors.error_count || 0} 条，警告 ${recentErrors.warning_count || 0} 条。`}>
            <RecentErrors items={recentErrors.items || []} />
          </SectionCard>

          <SectionCard title="启动自检" subtitle="启动脚本与后端启动时最容易出问题的检查项。">
            {Object.entries(startupChecks).map(([name, item]: [string, any]) => <CheckRow key={name} name={name} item={item} />)}
          </SectionCard>

          <SectionCard title="运行健康" subtitle="数据库、密钥、目录、备份、发布 Worker 与磁盘状态。">
            {Object.entries(healthChecks).map(([name, item]: [string, any]) => <CheckRow key={name} name={name} item={item} />)}
          </SectionCard>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '16px' }}>
            {snapshot && (
              <SectionCard title="运行时快照" subtitle={`生成于 ${snapshot.generated_at || '-'}，缓存: ${snapshot.cache?.hit ? '命中' : '未命中'}`}>
                <div style={{ display: 'grid', gap: '8px', fontSize: '13px' }}>
                  <div>运行时长：<strong>{snapshot.runtime?.process?.uptime_seconds ? `${Math.floor(snapshot.runtime.process.uptime_seconds / 3600)}h ${Math.floor((snapshot.runtime.process.uptime_seconds % 3600) / 60)}m` : '-'}</strong></div>
                  <div>托管存储：<strong>{snapshot.storage?.total_managed_size_human || '-'}</strong></div>
                  <div>24h 工具调用：<strong>{snapshot.runtime?.recent_tool_calls_24h ?? '-'}</strong></div>
                  <div>进行中发布：<strong style={{ color: snapshot.runtime?.deploy_tasks_by_status?.running ? 'var(--warning)' : 'var(--success)' }}>{snapshot.runtime?.deploy_tasks_by_status?.running ?? 0}</strong></div>
                  <div>缓存 TTL：<strong>{snapshot.cache?.ttl_seconds ?? '-'}s</strong></div>
                </div>
              </SectionCard>
            )}
            <SectionCard title="资源占用" subtitle="轻量化运行指标，用于判断是否存在轮询、终端或日志膨胀。">
              <div style={{ display: 'grid', gap: '8px', fontSize: '13px' }}>
                <div>进程 PID：<strong>{runtime.process?.pid || '-'}</strong></div>
                <div>运行时长：<strong>{runtime.process?.uptime_seconds || 0}</strong> 秒</div>
                <div>活动终端：<strong>{runtime.terminal?.active_sessions || 0}</strong></div>
                <div>近 24h MCP 调用：<strong>{runtime.recent_tool_calls_24h ?? '-'}</strong></div>
                <div>SSH 连接：<strong>{runtime.ssh_pool?.active_connections ?? '-'}</strong></div>
              </div>
            </SectionCard>

            <SectionCard title="存储占用" subtitle="data 目录与数据库、日志、备份、runtime 占用。">
              <div style={{ display: 'grid', gap: '8px', fontSize: '13px' }}>
                <div>data 目录：<span style={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>{storage.app_data_dir || '-'}</span></div>
                <div>托管总占用：<strong>{storage.total_managed_size_human || '-'}</strong></div>
                {Object.entries(storage.buckets || {}).slice(0, 8).map(([key, value]: [string, any]) => (
                  <div key={key} style={{ display: 'flex', justifyContent: 'space-between', gap: '8px' }}><span>{key}</span><strong>{value?.size_human || '-'}</strong></div>
                ))}
              </div>
            </SectionCard>

            <SectionCard title="性能摘要" subtitle="前端构建体积、缓存策略、慢请求日志和日志缓冲配置。">
              <div style={{ display: 'grid', gap: '8px', fontSize: '13px' }}>
                <div>dist 文件数：<strong>{performance.frontend_dist?.file_count ?? '-'}</strong></div>
                <div>assets 文件数：<strong>{performance.frontend_assets?.file_count ?? '-'}</strong></div>
                <div>慢请求阈值：<strong>{performance.config?.slow_request_ms ?? '-'} ms</strong></div>
                <div>资源统计缓存：<strong>{performance.config?.resource_cache_ttl_seconds ?? '-'} 秒</strong></div>
                <div>日志默认 Tail：<strong>{performance.config?.max_log_tail_lines ?? '-'}</strong> 行</div>
                {performance.recent_slow_requests?.length > 0 && (
                  <details>
                    <summary style={{ cursor: 'pointer', color: 'var(--warning)' }}>最近慢请求 {performance.recent_slow_requests.length} 条</summary>
                    <pre style={{ whiteSpace: 'pre-wrap', maxHeight: '180px', overflow: 'auto', color: 'var(--text-muted)' }}>{performance.recent_slow_requests.join('\n')}</pre>
                  </details>
                )}
              </div>
            </SectionCard>

            <SectionCard title="构建版本" subtitle="用于判断源码、dist、前端 JS 是否处于同一发布批次。">
              <div style={{ display: 'grid', gap: '8px', fontSize: '13px' }}>
                <div>状态：<StatusPill status={buildInfo.status} /> <span style={{ color: 'var(--text-muted)' }}>{buildInfo.message || '-'}</span></div>
                <div>前端版本：<strong>{buildInfo.frontend?.version || '-'}</strong></div>
                <div>前端构建时间：<strong>{buildInfo.frontend?.built_at || '-'}</strong></div>
                <div>后端启动时间：<strong>{buildInfo.backend?.started_at || '-'}</strong></div>
                <div>dist 资源：<strong>{buildInfo.frontend?.js_assets ?? '-'} JS / {buildInfo.frontend?.css_assets ?? '-'} CSS</strong></div>
                <div>dist 过期：<strong style={{ color: buildInfo.frontend?.dist_stale ? 'var(--warning)' : 'var(--success)' }}>{buildInfo.frontend?.dist_stale ? '是，请重新构建' : '否'}</strong></div>
                <div>最新源码：<span style={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>{buildInfo.frontend?.latest_source_file || '-'}</span></div>
              </div>
            </SectionCard>

            <SectionCard title="MCP 自检" subtitle="工具 schema、权限策略、风险工具和审计表可用性。">
              <div style={{ display: 'grid', gap: '8px', fontSize: '13px' }}>
                <div>状态：<StatusPill status={mcp.status} /></div>
                <div>工具数量：<strong>{mcp.tool_count ?? '-'}</strong></div>
                <div>写工具：<strong>{mcp.write_tools ?? '-'}</strong></div>
                <div>高风险工具：<strong>{mcp.high_risk_tools ?? '-'}</strong></div>
                <div>调用日志记录：<strong>{mcp.tool_call_log_count ?? '-'}</strong></div>
                <div>Capability Version：<span style={{ fontFamily: 'monospace' }}>{mcp.capability_version || '-'}</span></div>
                {mcp.schema_errors?.length > 0 && <pre style={{ whiteSpace: 'pre-wrap', color: 'var(--warning)' }}>{mcp.schema_errors.join('\n')}</pre>}
              </div>
            </SectionCard>
          </div>
        </>
      ) : (
        <LoadingState message="加载诊断信息..." />
      )}
    </div>
  )
}
