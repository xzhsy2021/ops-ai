import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { inspection, reports } from '../api'
import { ROUTES } from '../routes'
import { EmptyState, FavoriteButton, PageHeader, RiskBadge, StatusBadge } from '../components/ui'

type TabKey = 'overview' | 'server' | 'project' | 'combined' | 'runs' | 'ledger' | 'issues' | 'rules'

function formatTime(value?: string | null) {
  if (!value) return '-'
  try { return new Date(value).toLocaleString() } catch { return value }
}

function riskLabel(level?: string) {
  const map: Record<string, string> = { HIGH: '高危', MEDIUM: '中危', LOW: '低危', NONE: '无风险' }
  return map[(level || '').toUpperCase()] || level || '-'
}

function scoreTone(score?: number) {
  const s = Number(score || 0)
  if (s >= 90) return 'success'
  if (s >= 70) return 'warning'
  return 'danger'
}

function normalizeServerStatus(s: any) {
  const raw = String(s?.status ?? (s?.enabled === false ? 'disabled' : 'online')).toLowerCase()
  if (['disabled', 'disable', 'stopped', 'stop', 'inactive', '停用'].includes(raw)) return 'disabled'
  if (['offline', 'down', '离线'].includes(raw)) return 'offline'
  return 'online'
}

function isServerInspectable(s: any) {
  return s?.inspectable !== false && normalizeServerStatus(s) === 'online'
}

function serverStatusText(s: any) {
  const status = normalizeServerStatus(s)
  if (status === 'disabled') return '停用'
  if (status === 'offline') return '离线'
  return '在线/启用'
}

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100, 200]
const DEFAULT_PAGE_SIZE = 10

function PaginationControls({ total, limit, offset, onChange, onPageSizeChange, pageSizeOptions = PAGE_SIZE_OPTIONS }: { total: number; limit: number; offset: number; onChange: (nextOffset: number) => void; onPageSizeChange?: (next: number) => void; pageSizeOptions?: number[] }) {
  const safeTotal = Math.max(0, Number(total || 0))
  const safeLimit = Math.max(1, Number(limit || DEFAULT_PAGE_SIZE))
  const safeOffset = Math.max(0, Number(offset || 0))
  const page = Math.floor(safeOffset / safeLimit) + 1
  const pages = Math.max(1, Math.ceil(safeTotal / safeLimit))
  const from = safeTotal === 0 ? 0 : safeOffset + 1
  const to = Math.min(safeOffset + safeLimit, safeTotal)
  return (
    <div className="pagination-bar" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginTop: 12, flexWrap: 'wrap' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <small className="muted">第 {page}/{pages} 页 · 显示 {from}-{to} / 共 {safeTotal} 条</small>
        {onPageSizeChange && (
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--muted, #666)' }}>
            每页
            <select
              value={safeLimit}
              onChange={(e) => {
                const next = Number(e.target.value)
                onPageSizeChange(next)
                onChange(0)
              }}
              style={{ padding: '2px 6px' }}
            >
              {pageSizeOptions.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
            条
          </label>
        )}
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn btn-subtle" disabled={safeOffset <= 0} onClick={() => onChange(0)}>首页</button>
        <button className="btn btn-subtle" disabled={safeOffset <= 0} onClick={() => onChange(Math.max(0, safeOffset - safeLimit))}>上一页</button>
        <button className="btn btn-subtle" disabled={safeOffset + safeLimit >= safeTotal} onClick={() => onChange(safeOffset + safeLimit)}>下一页</button>
        <button className="btn btn-subtle" disabled={safeOffset + safeLimit >= safeTotal} onClick={() => onChange(Math.max(0, (pages - 1) * safeLimit))}>末页</button>
      </div>
    </div>
  )
}


function extractShellCommand(raw?: string | null, command?: string | null) {
  if (command) return command
  const text = String(raw || '')
  if (!text) return ''
  if (text.startsWith('$ ')) {
    const idx = text.indexOf('\nexit=')
    return idx > 0 ? text.slice(2, idx).trim() : text.slice(2).split('\n#')[0].trim()
  }
  return ''
}

function formatExecutionEntry(l: any) {
  const command = extractShellCommand(l.raw_output, l.command)
  const raw = String(l.raw_output || '')
  const output = raw && raw !== command ? raw : ''
  return [
    `[${formatTime(l.time || l.created_at)}] ${l.category || ''} / ${l.item_name || ''} / ${l.status || ''} / ${l.risk_level || ''}`,
    l.message || '',
    command ? `可直接验证的 Shell 命令:\n${command}` : '',
    output ? `执行输出:\n${output}` : '',
  ].filter(Boolean).join('\n')
}

function categoryOptions(items: any[], selected: string[], onChange: (next: string[]) => void, itemConfigs?: any[], onConfigEdit?: (item: any) => void) {
  const allCodes = items.map((x) => x.code)
  const checkedAll = allCodes.length > 0 && allCodes.every((x) => selected.includes(x))
  // 合并 itemConfigs 用于获取启用状态
  const cfgMap = new Map<string, any>()
  ;(itemConfigs || []).forEach((c: any) => cfgMap.set(c.item_code, c))
  return (
    <div style={{ display: 'grid', gap: 8 }}>
      <label className="check-row">
        <input type="checkbox" checked={checkedAll} onChange={(e) => onChange(e.target.checked ? allCodes : [])} />
        <strong>全选</strong>
        <small className="muted">（{selected.length}/{items.length}）</small>
      </label>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 10 }}>
        {items.map((item) => {
          const cfg = cfgMap.get(item.code)
          const enabled = cfg ? cfg.enabled !== false : true
          return (
            <div key={item.code} className="mini-card" style={{ cursor: 'pointer', display: 'flex', alignItems: 'flex-start', gap: 8, opacity: enabled ? 1 : 0.5 }}>
              <input
                type="checkbox"
                checked={selected.includes(item.code)}
                onChange={(e) => onChange(e.target.checked ? [...selected, item.code] : selected.filter((x) => x !== item.code))}
                style={{ marginTop: 4 }}
              />
              <span style={{ flex: 1 }}>
                <strong>{item.name}</strong>
                {cfg && !enabled && <small className="muted" style={{ marginLeft: 6, color: '#999' }}>(已禁用)</small>}
                <br />
                <small className="muted">{item.description}</small>
                <div style={{ marginTop: 4, display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                  {cfg && (
                    <small className="muted">{cfg.rules?.length || 0} 个规则</small>
                  )}
                  {onConfigEdit && cfg && (
                    <button
                      className="btn btn-subtle"
                      style={{ fontSize: 11, padding: '2px 6px' }}
                      onClick={(e) => { e.preventDefault(); onConfigEdit(cfg) }}
                    >配置</button>
                  )}
                </div>
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default function InspectionCenterPage() {
  const [tab, setTab] = useState<TabKey>('overview')
  const [overview, setOverview] = useState<any>({})
  const [categories, setCategories] = useState<any>({ server: [], project: [] })
  const [itemConfigs, setItemConfigs] = useState<any>({ server: [], project: [] })
  const [itemConfigEditor, setItemConfigEditor] = useState<{ open: boolean; item: any }>({ open: false, item: null })
  const [runDetailRaw, setRunDetailRaw] = useState<{ open: boolean; runId: string; items: any[] }>({ open: false, runId: '', items: [] })
  const [servers, setServers] = useState<any[]>([])
  const [projects, setProjects] = useState<any[]>([])
  const [runs, setRuns] = useState<any[]>([])
  const [runsTotal, setRunsTotal] = useState(0)
  const [runOffset, setRunOffset] = useState(0)
  const [runPageSize, setRunPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [ledgerData, setLedgerData] = useState<any>({ summary: {}, items: [] })
  const [ledgerOffset, setLedgerOffset] = useState(0)
  const [ledgerPageSize, setLedgerPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [inspectionReports, setInspectionReports] = useState<any[]>([])
  const [inspectionReportsTotal, setInspectionReportsTotal] = useState(0)
  const [reportOffset, setReportOffset] = useState(0)
  const [reportPageSize, setReportPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [ledgerPeriod, setLedgerPeriod] = useState('daily')
  const [issues, setIssues] = useState<any[]>([])
  const [rules, setRules] = useState<any[]>([])
  const [serverId, setServerId] = useState('')
  const [selectedServerIds, setSelectedServerIds] = useState<string[]>([])
  const [projectId, setProjectId] = useState('')
  const [serverCats, setServerCats] = useState<string[]>([])
  const [projectCats, setProjectCats] = useState<string[]>([])
  const [currentResult, setCurrentResult] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [runFilter, setRunFilter] = useState('')
  const [issueFilter, setIssueFilter] = useState('OPEN')
  const [activeRunIds, setActiveRunIds] = useState<string[]>([])
  const [selectedRunIds, setSelectedRunIds] = useState<string[]>([])
  const [selectedLedgerRunIds, setSelectedLedgerRunIds] = useState<string[]>([])
  const [selectedReportIds, setSelectedReportIds] = useState<string[]>([])
  const [selectedIssueIds, setSelectedIssueIds] = useState<string[]>([])
  const [projectRelations, setProjectRelations] = useState<any[]>([])
  const [projectPathEditor, setProjectPathEditor] = useState<{ open: boolean; form: any }>({ open: false, form: {} })
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({})
  const [selectedGroups, setSelectedGroups] = useState<string[]>([])
  const [batchConcurrency, setBatchConcurrency] = useState(3)
  const [batchSize, setBatchSize] = useState(5)
  const [commandTimeoutSeconds, setCommandTimeoutSeconds] = useState(20)
  const [runTimeoutSeconds, setRunTimeoutSeconds] = useState(180)
  const [ruleEditor, setRuleEditor] = useState<{ open: boolean; mode: 'create' | 'edit'; originalCode: string; form: any }>({
    open: false,
    mode: 'create',
    originalCode: '',
    form: {},
  })

  async function loadBase() {
    setLoading(true)
    setError('')
    try {
      const [ov, cat, srv, prj, runRes, ledgerRes, issueRes, ruleRes, reportRes, itemCfgRes]: any[] = await Promise.all([
        inspection.overview(),
        inspection.categories(),
        inspection.servers(),
        inspection.projects(),
        inspection.runs({ limit: runPageSize, offset: runOffset }),
        inspection.ledger({ period: ledgerPeriod, limit: ledgerPageSize, offset: ledgerOffset }),
        inspection.issues({ status: issueFilter || undefined, limit: 50 }),
        inspection.rules({}),
        reports.list({ report_type: 'inspection', limit: reportPageSize, offset: reportOffset }),
        inspection.listItemConfigs('SERVER'),
      ])
      const ovData = ov.data || {}
      const catData = cat.data || { server: [], project: [] }
      const srvItems = srv.data || []
      const prjItems = prj.data || []
      setOverview(ovData)
      setCategories(catData)
      setServers(srvItems)
      setProjects(prjItems)
      setRuns(runRes.data?.items || [])
      setRunsTotal(Number(runRes.data?.total || 0))
      setLedgerData(ledgerRes.data || { summary: {}, items: [] })
      setIssues(issueRes.data?.items || [])
      setRules(ruleRes.data?.items || [])
      setInspectionReports(reportRes.data?.items || [])
      setInspectionReportsTotal(Number(reportRes.data?.total || 0))
      setItemConfigs((prev: any) => ({ ...prev, server: itemCfgRes.data?.items || [] }))
      const firstInspectable = srvItems.find((s: any) => isServerInspectable(s)) || srvItems[0]
      if (!serverId && firstInspectable?.id) setServerId(firstInspectable.id)
      if (selectedServerIds.length === 0 && firstInspectable?.id) setSelectedServerIds([firstInspectable.id])
      if (!projectId && prjItems[0]?.id) setProjectId(prjItems[0].id)
      if (serverCats.length === 0) setServerCats((catData.server || []).map((x: any) => x.code))
      if (projectCats.length === 0) setProjectCats((catData.project || []).map((x: any) => x.code))
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally { setLoading(false) }
  }

  async function reloadRuns(scope?: string, nextOffset = runOffset) {
    const res: any = await inspection.runs({ scope_type: scope || runFilter || undefined, limit: runPageSize, offset: nextOffset })
    setRuns(res.data?.items || [])
    setRunsTotal(Number(res.data?.total || 0))
  }

  async function reloadLedger(period = ledgerPeriod, nextOffset = ledgerOffset) {
    const res: any = await inspection.ledger({ period, limit: ledgerPageSize, offset: nextOffset })
    setLedgerData(res.data || { summary: {}, items: [] })
  }

  async function reloadInspectionReports(nextOffset = reportOffset) {
    const res: any = await reports.list({ report_type: 'inspection', limit: reportPageSize, offset: nextOffset })
    setInspectionReports(res.data?.items || [])
    setInspectionReportsTotal(Number(res.data?.total || 0))
  }

  async function generatePeriodicInspectionReport(period = ledgerPeriod) {
    setError(''); setMessage('')
    try {
      const titleMap: Record<string, string> = { daily: '每日巡检台账', weekly: '每周巡检报表', monthly: '月度安全巡检报告' }
      const res: any = await inspection.generatePeriodicReport({ period, format: 'md', title: titleMap[period] || '巡检台账与报表' })
      setMessage(`已生成${titleMap[period] || '巡检报表'}：${res.data?.report?.title || res.data?.report?.id || ''}`)
      await reloadLedger(period)
    } catch (e: any) { setError(e?.message || String(e)) }
  }

  async function reloadIssues() {
    const res: any = await inspection.issues({ status: issueFilter || undefined, limit: 100 })
    setIssues(res.data?.items || [])
  }

  async function reloadProjectRelations(pid = projectId) {
    if (!pid) {
      setProjectRelations([])
      return []
    }
    try {
      const res: any = await inspection.relations({ project_id: pid })
      const items = res.data?.items || []
      setProjectRelations(items)
      return items
    } catch {
      setProjectRelations([])
      return []
    }
  }

  function toggleRunSelection(runId: string, checked: boolean) {
    setSelectedRunIds((prev) => checked ? Array.from(new Set([...prev, runId])) : prev.filter((x) => x !== runId))
  }

  function toggleLedgerRunSelection(runId: string, checked: boolean) {
    setSelectedLedgerRunIds((prev) => checked ? Array.from(new Set([...prev, runId])) : prev.filter((x) => x !== runId))
  }

  function toggleReportSelection(reportId: string, checked: boolean) {
    setSelectedReportIds((prev) => checked ? Array.from(new Set([...prev, reportId])) : prev.filter((x) => x !== reportId))
  }

  function openProjectPathEditor() {
    const project = projects.find((p) => p.id === projectId) || {}
    const rel = projectRelations[0] || {}
    setProjectPathEditor({
      open: true,
      form: {
        id: rel.id && !String(rel.id).startsWith('config::') ? rel.id : '',
        project_id: projectId,
        server_id: rel.server_id || (project.servers || [])[0] || '',
        deploy_role: rel.deploy_role || 'APP',
        deploy_path: rel.deploy_path || project.deploy_path || '',
        config_path: rel.config_path || project.config_path || '',
        log_path: rel.log_path || project.log_path || '',
        backup_path: rel.backup_path || project.backup_path || '',
        runtime_user: rel.runtime_user || project.runtime_user || '',
        main_port: rel.main_port || project.main_port || '',
        active: rel.active !== false,
      },
    })
  }

  function updateProjectPathForm(patch: any) {
    setProjectPathEditor((prev) => ({ ...prev, form: { ...prev.form, ...patch } }))
  }

  async function saveProjectPathConfig() {
    const form = projectPathEditor.form || {}
    if (!form.project_id) return setError('项目不能为空')
    if (!form.server_id) return setError('部署服务器不能为空，请先为项目选择或配置服务器')
    try {
      const payload = { ...form, active: form.active !== false }
      const res: any = form.id ? await inspection.updateRelation(form.id, payload) : await inspection.saveRelation(payload)
      setMessage(`项目部署路径配置已保存：${res.data?.server_id || form.server_id}`)
      setProjectPathEditor({ open: false, form: {} })
      await reloadProjectRelations(form.project_id)
      const prj: any = await inspection.projects()
      setProjects(prj.data || [])
    } catch (e: any) { setError(e?.message || String(e)) }
  }

  async function runServer() {
    if (!serverId) return setError('请选择服务器')
    setRunning(true); setError(''); setMessage('')
    try {
      const res: any = await inspection.startServer({ server_id: serverId, categories: serverCats, command_timeout_seconds: commandTimeoutSeconds, run_timeout_seconds: runTimeoutSeconds } as any)
      const runId = res.data?.run?.id
      setCurrentResult(res.data)
      if (runId) setActiveRunIds([runId])
      setMessage('服务器巡检已启动，正在实时输出执行过程')
      setTab('runs')
      await reloadRuns('SERVER')
    } catch (e: any) { setError(e?.message || String(e)); setRunning(false) }
  }

  async function runServersBatch(groupsOverride?: string[]) {
    const groupsList = (groupsOverride && groupsOverride.length > 0) ? groupsOverride : selectedGroups
    const ids = selectedServerIds.length > 0 ? selectedServerIds : (serverId ? [serverId] : [])
    if (!groupsList.length && ids.length === 0) return setError('请选择至少一台服务器或一个分组')
    setRunning(true); setError(''); setMessage('')
    try {
      const res: any = await inspection.startServersBatch({ server_ids: ids, groups: groupsList, categories: serverCats, concurrency: batchConcurrency, batch_size: batchSize, command_timeout_seconds: commandTimeoutSeconds, run_timeout_seconds: runTimeoutSeconds, skip_disabled: true } as any)
      const runIds = res.data?.run_ids || []
      if (runIds.length > 0) setActiveRunIds(runIds)
      if (runIds[0]) {
        const detail: any = await inspection.runDetail(runIds[0])
        setCurrentResult(detail.data)
      } else {
        setCurrentResult(res.data)
      }
      const targetLabel = groupsList.length > 0 ? `分组 ${groupsList.join('/')}` : `${ids.length} 台服务器`
      const skippedText = res.data?.skipped_count ? `；已自动跳过 ${res.data.skipped_count} 台停用/离线服务器` : ''
      setMessage((res.data?.summary || `已启动 ${targetLabel} 巡检，正在实时输出执行过程`) + skippedText)
      setTab('runs')
      await reloadRuns('SERVER')
    } catch (e: any) { setError(e?.message || String(e)); setRunning(false) }
  }

  function buildRuleForm(rule?: any) {
    const config = rule?.config && typeof rule.config === 'object' ? rule.config : {}
    return {
      rule_code: rule?.rule_code || '',
      rule_name: rule?.rule_name || '',
      scope_type: rule?.scope_type || 'SERVER',
      category: rule?.category || 'CUSTOM',
      risk_level: rule?.risk_level || 'MEDIUM',
      enabled: rule?.enabled !== false,
      description: rule?.description || '',
      suggestion: rule?.suggestion || '',
      rule_content: rule?.rule_content || config.content || '',
      config_text: JSON.stringify(config, null, 2),
    }
  }

  function openCreateRule() {
    setRuleEditor({ open: true, mode: 'create', originalCode: '', form: buildRuleForm({ rule_code: `CUSTOM_${Date.now()}`, rule_name: '自定义巡检规则' }) })
  }

  async function openItemConfigEditor(cfg: any) {
    setItemConfigEditor({ open: true, item: cfg })
  }

  async function saveItemConfig(patch: any) {
    if (!itemConfigEditor.item) return
    try {
      await inspection.updateItemConfig(itemConfigEditor.item.id, patch)
      setMessage('巡检项目配置已保存')
      // 重新加载
      const res: any = await inspection.listItemConfigs('SERVER')
      setItemConfigs((prev: any) => ({ ...prev, server: res.data?.items || [] }))
      setItemConfigEditor({ open: false, item: null })
    } catch (e: any) { setError(e?.message || String(e)) }
  }

  async function toggleItemConfigEnabled(itemId: string) {
    try {
      await inspection.toggleItemConfig(itemId)
      const res: any = await inspection.listItemConfigs('SERVER')
      setItemConfigs((prev: any) => ({ ...prev, server: res.data?.items || [] }))
      setMessage('状态已更新')
    } catch (e: any) { setError(e?.message || String(e)) }
  }

  async function openRunDetailRaw(runId: string) {
    try {
      const res: any = await inspection.getRunRawOutput(runId)
      setRunDetailRaw({ open: true, runId, items: res.data?.items || [] })
    } catch (e: any) { setError(e?.message || String(e)) }
  }

  function editRule(rule: any) {
    const code = rule.rule_code || rule.id
    if (!code) return setError('规则编码缺失')
    setRuleEditor({ open: true, mode: 'edit', originalCode: code, form: buildRuleForm(rule) })
  }

  function updateRuleForm(patch: any) {
    setRuleEditor((prev) => ({ ...prev, form: { ...prev.form, ...patch } }))
  }

  async function submitRuleForm() {
    const form = ruleEditor.form || {}
    const code = String(form.rule_code || '').trim().toUpperCase().replace(/\s+/g, '_')
    if (!code) return setError('规则编码不能为空')
    let config: any = {}
    try {
      config = form.config_text ? JSON.parse(form.config_text) : {}
    } catch {
      return setError('规则配置 JSON 格式不正确')
    }
    const payload = {
      rule_code: code,
      rule_name: form.rule_name || code,
      scope_type: form.scope_type || 'BOTH',
      category: form.category || 'CUSTOM',
      risk_level: form.risk_level || 'MEDIUM',
      enabled: form.enabled !== false,
      description: form.description || '',
      suggestion: form.suggestion || '',
      rule_content: form.rule_content || '',
      config,
    }
    try {
      const res: any = ruleEditor.mode === 'create'
        ? await inspection.createRule(payload)
        : await inspection.updateRule(ruleEditor.originalCode, payload)
      setRuleEditor({ open: false, mode: 'create', originalCode: '', form: {} })
      setMessage(`规则已${ruleEditor.mode === 'create' ? '新增' : '保存'}：${res.data?.rule_name || code}`)
      const refreshed: any = await inspection.rules({})
      setRules(refreshed.data?.items || [])
    } catch (e: any) { setError(e?.message || String(e)) }
  }

  async function toggleRule(rule: any) {
    const code = rule.rule_code || rule.id
    if (!code) return setError('规则编码缺失')
    try {
      const res: any = await inspection.updateRule(code, { enabled: rule.enabled === false })
      setMessage(`规则已${res.data?.enabled ? '启用' : '停用'}：${res.data?.rule_name || code}`)
      const refreshed: any = await inspection.rules({})
      setRules(refreshed.data?.items || [])
    } catch (e: any) { setError(e?.message || String(e)) }
  }

  async function deleteRule(rule: any) {
    const code = rule.rule_code || rule.id
    if (!code) return setError('规则编码缺失')
    if (!window.confirm(`确认删除巡检规则「${rule.rule_name || code}」？内置规则会被软删除，可通过重新新增同编码规则恢复。`)) return
    try {
      await inspection.deleteRule(code)
      setMessage(`规则已删除：${rule.rule_name || code}`)
      const refreshed: any = await inspection.rules({})
      setRules(refreshed.data?.items || [])
    } catch (e: any) { setError(e?.message || String(e)) }
  }

  async function runProject() {
    if (!projectId) return setError('请选择项目')
    setRunning(true); setError(''); setMessage('')
    try {
      const res: any = await inspection.startProject({ project_id: projectId, categories: projectCats, include_server_summary: true })
      const runId = res.data?.run?.id
      setCurrentResult(res.data)
      if (runId) setActiveRunIds([runId])
      setMessage('项目巡检已启动，正在实时输出执行过程')
      setTab('runs')
      await reloadRuns('PROJECT')
    } catch (e: any) { setError(e?.message || String(e)); setRunning(false) }
  }

  async function runCombined() {
    if (!projectId) return setError('请选择项目')
    setRunning(true); setError(''); setMessage('')
    try {
      const res: any = await inspection.runCombined(projectId, { categories: [...serverCats, ...projectCats], generate_report: true })
      setCurrentResult(res.data)
      setMessage('项目综合巡检已完成，并已尝试生成报告')
      await loadBase()
      setTab('runs')
    } catch (e: any) { setError(e?.message || String(e)) } finally { setRunning(false) }
  }

  async function generateReport(runId: string) {
    setError(''); setMessage('')
    try {
      const detail: any = await inspection.runDetail(runId)
      const status = detail.data?.run?.status
      if (status === 'RUNNING' || status === 'PENDING') {
        setCurrentResult(detail.data)
        setActiveRunIds((prev) => Array.from(new Set([...prev, runId])))
        return setError('巡检仍在执行中，请等待完成后再生成报告。')
      }
      const res: any = await inspection.generateReport(runId, { format: 'md' })
      setMessage(`巡检报告已生成：${res.data?.report?.title || res.data?.report?.id}`)
      await loadBase()
    } catch (e: any) { setError(e?.message || String(e)) }
  }

  async function generateSelectedReports() {
    const ids = selectedRunIds
    if (ids.length === 0) return setError('请先在巡检记录中勾选需要合并生成报告的记录')
    setError(''); setMessage('')
    try {
      const details: any[] = []
      for (const id of ids) {
        const detail: any = await inspection.runDetail(id)
        details.push(detail.data)
        const status = detail.data?.run?.status
        if (status === 'RUNNING' || status === 'PENDING') {
          setCurrentResult(detail.data)
          setActiveRunIds((prev) => Array.from(new Set([...prev, id])))
          return setError(`巡检 ${id} 仍在执行中，请等待完成后再生成合并报告。`)
        }
        if ((detail.data?.items || []).length === 0) {
          return setError(`巡检 ${id} 结果为空，请重新执行后再生成合并报告。`)
        }
      }
      const res: any = await inspection.generateReports({ run_ids: ids, format: 'md', title: `巡检合并报告（${ids.length} 条记录）` })
      setMessage(`巡检合并报告已生成：${res.data?.report?.title || res.data?.report?.id}`)
      await loadBase()
    } catch (e: any) { setError(e?.message || String(e)) }
  }

  async function deleteInspectionRuns(ids: string[]) {
    const normalized = Array.from(new Set((ids || []).filter(Boolean)))
    if (normalized.length === 0) return setError('请选择要删除的巡检记录')
    if (!window.confirm(`确认删除 ${normalized.length} 条巡检历史？相关巡检项、风险问题、证据和未被其他记录引用的巡检报告也会删除。`)) return
    // 检查选中的记录里是否有 RUNNING/PENDING（孤儿执行中记录），如有则提示用户二次确认强制删除
    const selectedRows = runs.filter((r: any) => normalized.includes(r.id))
    const stuck = selectedRows.filter((r: any) => ['RUNNING', 'PENDING'].includes(String(r.status || '').toUpperCase()))
    let force = false
    if (stuck.length > 0) {
      const proceed = window.confirm(`其中 ${stuck.length} 条记录状态为「执行中/等待中」，可能为孤儿记录（后台执行器已停止但状态未更新）。\n\n是否「强制删除」这些记录？\n\n选择「确定」= 强制删除（含执行中记录）\n选择「取消」= 仅删除非执行中的记录`)
      if (!proceed) {
        // 取消强删：用 force=false 让后端跳过
        force = false
        // 直接 return
        return
      }
      force = true
    }
    setError(''); setMessage('')
    try {
      const res: any = await inspection.deleteRuns({ run_ids: normalized, delete_reports: true, force })
      const skippedNote = (res.data?.skipped || []).length ? `，跳过 ${(res.data?.skipped || []).length} 条执行中记录（未强制删除）` : ''
      const forceNote = force ? '（含执行中/等待中）' : ''
      setMessage(`已删除 ${res.data?.deleted || 0} 条巡检记录${forceNote}，删除报告 ${res.data?.deleted_reports || 0} 份${skippedNote}`)
      setSelectedRunIds([])
      setSelectedLedgerRunIds([])
      if (currentResult?.run?.id && normalized.includes(currentResult.run.id)) setCurrentResult(null)
      await Promise.all([reloadRuns(), reloadLedger(), reloadInspectionReports(), reloadIssues()])
    } catch (e: any) { setError(e?.message || String(e)) }
  }

  async function deleteIssue(issueId: string) {
    if (!window.confirm('确认删除这条风险问题？删除后不可恢复。')) return
    setError(''); setMessage('')
    try {
      await inspection.deleteIssue(issueId)
      setMessage('风险问题已删除')
      setSelectedIssueIds((prev: string[]) => prev.filter((x: string) => x !== issueId))
      await reloadIssues()
    } catch (e: any) { setError(e?.message || String(e)) }
  }

  async function deleteIssues(issueIds: string[]) {
    const normalized = Array.from(new Set((issueIds || []).filter(Boolean)))
    if (normalized.length === 0) return setError('请选择要删除的风险问题')
    if (!window.confirm(`确认删除 ${normalized.length} 条风险问题？删除后不可恢复。`)) return
    setError(''); setMessage('')
    try {
      let ok = 0
      for (const id of normalized) {
        try { await inspection.deleteIssue(id); ok++ } catch (_e) { /* 单条失败不中断 */ }
      }
      setMessage(`已删除 ${ok}/${normalized.length} 条风险问题`)
      setSelectedIssueIds([])
      await reloadIssues()
    } catch (e: any) { setError(e?.message || String(e)) }
  }

  async function deleteLedgerHistoryByPeriod() {
    const label = ledgerPeriod === 'daily' ? '今日台账' : ledgerPeriod === 'weekly' ? '本周台账/报表' : '本月台账/报表'
    if (!window.confirm(`确认删除${label}范围内的巡检历史？此操作会同步删除对应巡检记录、台账行、问题证据以及未被引用的巡检报告。`)) return
    setError(''); setMessage('')
    try {
      const res: any = await inspection.deleteLedger({ period: ledgerPeriod, delete_reports: true })
      setMessage(`已删除 ${res.data?.deleted || 0} 条${label}历史，删除报告 ${res.data?.deleted_reports || 0} 份`)
      setSelectedLedgerRunIds([])
      if (currentResult?.run?.id && (res.data?.run_ids || []).includes(currentResult.run.id)) setCurrentResult(null)
      await Promise.all([reloadRuns(), reloadLedger(), reloadInspectionReports(), reloadIssues()])
    } catch (e: any) { setError(e?.message || String(e)) }
  }

  async function deleteInspectionReports(ids: string[]) {
    const normalized = Array.from(new Set((ids || []).filter(Boolean)))
    if (normalized.length === 0) return setError('请选择要删除的巡检报表')
    if (!window.confirm(`确认删除 ${normalized.length} 份巡检报表？对应文件也会删除。`)) return
    setError(''); setMessage('')
    try {
      for (const id of normalized) await reports.delete(id)
      setMessage(`已删除 ${normalized.length} 份巡检报表`)
      setSelectedReportIds([])
      await reloadInspectionReports()
      await reloadRuns()
      await reloadLedger()
    } catch (e: any) { setError(e?.message || String(e)) }
  }

  async function updateIssue(issueId: string, status: string) {
    setError(''); setMessage('')
    try {
      await inspection.updateIssue(issueId, { status })
      setMessage('问题状态已更新')
      await reloadIssues()
    } catch (e: any) { setError(e?.message || String(e)) }
  }

  useEffect(() => { loadBase() }, [])
  useEffect(() => { reloadIssues().catch(() => undefined) }, [issueFilter])
  useEffect(() => { reloadRuns().catch(() => undefined) }, [runFilter, runOffset, runPageSize])
  useEffect(() => { reloadProjectRelations().catch(() => undefined) }, [projectId])
  useEffect(() => { reloadLedger().catch(() => undefined) }, [ledgerPeriod, ledgerOffset, ledgerPageSize])
  useEffect(() => { reloadInspectionReports().catch(() => undefined) }, [reportOffset, reportPageSize])

  useEffect(() => {
    if (activeRunIds.length === 0) return
    const timer = window.setInterval(async () => {
      try {
        const details = await Promise.all(activeRunIds.map((id) => inspection.runDetail(id).catch(() => null)))
        const valid = details.filter(Boolean) as any[]
        if (valid[0]?.data) setCurrentResult(valid[0].data)
        const stillRunning = valid
          .map((x: any) => x.data?.run)
          .filter((r: any) => r && (r.status === 'RUNNING' || r.status === 'PENDING'))
          .map((r: any) => r.id)
        const completed = valid.length > 0 && stillRunning.length === 0
        await reloadRuns()
        if (completed) {
          setActiveRunIds([])
          setRunning(false)
          setMessage('巡检执行完成，可查看详情或生成报告。')
          await reloadIssues()
        } else {
          setActiveRunIds(stillRunning)
        }
      } catch {
        // keep polling; transient errors should not hide the running stream
      }
    }, 2000)
    return () => window.clearInterval(timer)
  }, [activeRunIds.join('|')])

  const selectedProject = useMemo(() => {
    const base = projects.find((p) => p.id === projectId) || null
    if (!base) return null
    const rel = projectRelations[0]
    if (!rel) return base
    return {
      ...base,
      servers: projectRelations.map((r) => r.server_id).filter(Boolean),
      deploy_path: rel.deploy_path || base.deploy_path || '',
      config_path: rel.config_path || base.config_path || '',
      log_path: rel.log_path || base.log_path || '',
      backup_path: rel.backup_path || base.backup_path || '',
      runtime_user: rel.runtime_user || base.runtime_user || '',
      main_port: rel.main_port || base.main_port || '',
      relation_configured: true,
    }
  }, [projects, projectId, projectRelations])
  const selectedProjectPathMissing = Boolean(selectedProject && (!selectedProject.deploy_path || !selectedProject.log_path || !selectedProject.backup_path))
  const groupedServers = useMemo(() => {
    const groups: Record<string, any[]> = {}
    for (const s of servers) {
      const group = s.group || s.env || '未分组'
      if (!groups[group]) groups[group] = []
      groups[group].push(s)
    }
    return groups
  }, [servers])

  const activeServerIds = useMemo(() => servers.filter(isServerInspectable).map((s) => s.id || s.name).filter(Boolean), [servers])
  const disabledServerCount = useMemo(() => servers.filter((s) => !isServerInspectable(s)).length, [servers])

  return (
    <div className="page-container inspection-page">
      <PageHeader
        title="巡检中心"
        description="单项目轻量巡检：平台本地/SSH 执行只读检查，保留台账与报告，AI 仅做摘要、解释和建议。"
        actions={<div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}><FavoriteButton url={ROUTES.inspection} label="巡检中心" category="inspection" /><Link className="btn btn-subtle" to={ROUTES.reports}>报告中心</Link><button className="btn primary" onClick={loadBase} disabled={loading}>{loading ? '加载中...' : '刷新'}</button></div>}
      />

      {error && <div className="alert alert-error" style={{ marginBottom: 12 }}>{error}</div>}
      {message && <div className="alert alert-success" style={{ marginBottom: 12 }}>{message}</div>}

      <div className="tab-bar inspection-tabs" style={{ marginBottom: 16 }}>
        {[
          ['overview', '巡检总览'], ['server', '服务器巡检'], ['project', '项目巡检'], ['runs', '巡检记录'], ['ledger', '台账报表'], ['issues', '风险问题'], ['rules', '巡检规则'],
        ].map(([key, label]) => <button key={key} className={`tab-btn${tab === key ? ' tab-btn--active' : ''}`} onClick={() => setTab(key as TabKey)}>{label}</button>)}
      </div>

      {tab === 'overview' && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12, marginBottom: 16 }}>
            <div className="mini-card"><div className="muted">服务器资产</div><strong>{overview.server_count || servers.length || 0}</strong></div>
            <div className="mini-card"><div className="muted">项目资产</div><strong>{overview.project_count || projects.length || 0}</strong></div>
            <div className="mini-card"><div className="muted">待处理风险</div><strong>{overview.open_issue_count || 0}</strong></div>
            <div className="mini-card"><div className="muted">高危 / 中危 / 低危</div><strong>{overview.high_issue_count || 0} / {overview.medium_issue_count || 0} / {overview.low_issue_count || 0}</strong></div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
            <section className="panel-card">
              <h2>基础巡检状态</h2>
              <p className="muted">当前采用单项目轻量架构，不部署服务器侧 Agent；巡检由平台本地/SSH 只读命令完成，AI 只负责摘要、解释、建议和报告生成。</p>
              <div className="mini-card"><strong>最近服务器巡检</strong><div>{overview.latest_server_run?.summary || '暂无服务器巡检记录'}</div></div>
              <div className="mini-card" style={{ marginTop: 8 }}><strong>最近项目巡检</strong><div>{overview.latest_project_run?.summary || '暂无项目巡检记录'}</div></div>
            </section>
            <section className="panel-card">
              <h2>最近风险</h2>
              {(overview.recent_issues || []).length === 0 ? <EmptyState title="暂无待处理风险" description="执行一次服务器巡检或项目巡检后会在这里展示风险。" /> : (
                <div className="table-scroll"><table className="data-table"><thead><tr><th>等级</th><th>标题</th><th>对象</th><th>状态</th></tr></thead><tbody>{overview.recent_issues.map((i: any) => <tr key={i.id}><td><RiskBadge level={i.risk_level} label={riskLabel(i.risk_level)} /></td><td>{i.title}</td><td>{i.project_id || i.server_id || '-'}</td><td><StatusBadge value={i.status} /></td></tr>)}</tbody></table></div>
              )}
            </section>
          </div>
        </>
      )}

      {tab === 'server' && (
        <section className="panel-card">
          <h2>服务器巡检</h2>
          <p className="muted">只执行只读命令，覆盖登录、账号、命令、进程端口、防火墙、磁盘、服务、备份等基础项。</p>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 16 }}>
            <label>单台服务器 <select value={serverId} onChange={(e) => setServerId(e.target.value)}>{servers.map((s) => <option key={s.id || s.name} value={s.id || s.name} disabled={!isServerInspectable(s)}>{s.name || s.id} · {s.host || '-'} · {serverStatusText(s)}</option>)}</select></label>
            <button className="btn primary" onClick={runServer} disabled={running || !serverId}>{running ? '巡检中...' : '一键巡检选中服务器'}</button>
            <button className="btn" onClick={runServersBatch} disabled={running || selectedServerIds.length === 0}>{running ? '巡检中...' : `一键巡检 ${selectedServerIds.length || 0} 台服务器`}</button>
            <button className="btn btn-subtle" onClick={() => { setSelectedServerIds(activeServerIds); setMessage(`已选择全部在线/启用服务器 ${activeServerIds.length} 台，停用/离线 ${disabledServerCount} 台会自动跳过。`) }} disabled={running || activeServerIds.length === 0}>选择全部在线/启用</button>
          </div>
          <div className="mini-card" style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
            <strong>并发与超时限制</strong>
            <label>并发 <input type="number" min={1} max={8} value={batchConcurrency} onChange={(e) => setBatchConcurrency(Math.max(1, Math.min(8, Number(e.target.value || 1))))} style={{ width: 72 }} /></label>
            <label>分批大小 <input type="number" min={1} max={20} value={batchSize} onChange={(e) => setBatchSize(Math.max(1, Math.min(20, Number(e.target.value || 1))))} style={{ width: 72 }} /></label>
            <label>单命令超时(秒) <input type="number" min={5} max={120} value={commandTimeoutSeconds} onChange={(e) => setCommandTimeoutSeconds(Math.max(5, Math.min(120, Number(e.target.value || 20))))} style={{ width: 86 }} /></label>
            <label>单服务器总超时(秒) <input type="number" min={30} max={1800} value={runTimeoutSeconds} onChange={(e) => setRunTimeoutSeconds(Math.max(30, Math.min(1800, Number(e.target.value || 180))))} style={{ width: 92 }} /></label>
            <small className="muted">共 {servers.length} 台，在线/启用 {activeServerIds.length} 台，停用/离线 {disabledServerCount} 台。</small>
          </div>
          <div className="mini-card" style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <strong>批量服务器选择（按分组折叠）</strong>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button className="btn btn-subtle" onClick={() => setSelectedServerIds(activeServerIds)}>全选在线/启用</button>
                <button className="btn btn-subtle" onClick={() => setSelectedServerIds([])}>清空</button>
                <button className="btn btn-subtle" onClick={() => setExpandedGroups(Object.fromEntries(Object.keys(groupedServers).map((g) => [g, true])))}>展开全部</button>
                <button className="btn btn-subtle" onClick={() => setExpandedGroups(Object.fromEntries(Object.keys(groupedServers).map((g) => [g, false])))}>收起全部</button>
              </div>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', marginTop: 10 }}>
              <strong>按分组直接巡检：</strong>
              {Object.keys(groupedServers).map((g) => {
                const checked = selectedGroups.includes(g)
                return (
                  <label key={g} className="check-row" style={{ padding: '2px 8px', border: '1px solid var(--border-color, #d9d9d9)', borderRadius: 6 }}>
                    <input type="checkbox" checked={checked} onChange={(e) => setSelectedGroups(e.target.checked ? Array.from(new Set([...selectedGroups, g])) : selectedGroups.filter((x) => x !== g))} />
                    <span style={{ marginLeft: 4 }}>{g}</span>
                  </label>
                )
              })}
              <button className="btn btn-subtle" onClick={() => setSelectedGroups([])}>清空分组</button>
              <button className="btn" onClick={() => runServersBatch()} disabled={running || (selectedGroups.length === 0 && selectedServerIds.length === 0)}>{running ? '巡检中...' : `按所选分组发起巡检（${selectedGroups.length}）`}</button>
            </div>
            <div style={{ display: 'grid', gap: 10, marginTop: 10 }}>
              {Object.entries(groupedServers).map(([group, list]) => {
                const ids = (list as any[]).filter(isServerInspectable).map((s) => s.id || s.name).filter(Boolean)
                const allChecked = ids.length > 0 && ids.every((id) => selectedServerIds.includes(id))
                const someChecked = ids.some((id) => selectedServerIds.includes(id))
                const expanded = expandedGroups[group] !== false
                return (
                  <div key={group} className="mini-card" style={{ padding: 10 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                      <label className="check-row" style={{ margin: 0 }}>
                        <input
                          type="checkbox"
                          checked={allChecked}
                          ref={(el) => { if (el) el.indeterminate = !allChecked && someChecked }}
                          onChange={(e) => setSelectedServerIds(e.target.checked ? Array.from(new Set([...selectedServerIds, ...ids])) : selectedServerIds.filter((x) => !ids.includes(x)))}
                        />
                        <span><strong>{group}</strong> <small className="muted">在线/启用 {ids.length} 台，停用/离线 {(list as any[]).length - ids.length} 台，已选 {ids.filter((id) => selectedServerIds.includes(id)).length} 台</small></span>
                      </label>
                      <button className="btn btn-subtle" onClick={() => setExpandedGroups({ ...expandedGroups, [group]: !expanded })}>{expanded ? '收起' : '展开'}</button>
                    </div>
                    {expanded && <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 8, marginTop: 8 }}>
                      {(list as any[]).map((s) => { const id = s.id || s.name; const inspectable = isServerInspectable(s); return <label key={id} className="check-row" style={{ opacity: inspectable ? 1 : 0.55 }}><input type="checkbox" disabled={!inspectable} checked={inspectable && selectedServerIds.includes(id)} onChange={(e) => setSelectedServerIds(e.target.checked ? Array.from(new Set([...selectedServerIds, id])) : selectedServerIds.filter((x) => x !== id))} /> <span><strong>{s.name || id}</strong> <small className="muted">{s.host || s.ip || '-'} · {serverStatusText(s)}</small></span></label> })}
                    </div>}
                  </div>
                )
              })}
            </div>
          </div>
          {categoryOptions(categories.server || [], serverCats, setServerCats, itemConfigs.server, openItemConfigEditor)}
        </section>
      )}

      {tab === 'project' && (
        <section className="panel-card">
          <h2>项目巡检</h2>
          <p className="muted">项目巡检面向当前项目，自动使用部署路径、日志路径、备份路径和关联服务器；综合巡检作为项目巡检内的快捷动作。</p>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 16 }}>
            <label>项目 <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>{projects.map((p) => <option key={p.id} value={p.id}>{p.name || p.id}</option>)}</select></label>
            <button className="btn primary" onClick={runProject} disabled={running || !projectId}>{running ? '巡检中...' : '一键项目巡检'}</button><button className="btn btn-subtle" onClick={runCombined} disabled={running || !projectId}>{running ? '巡检中...' : '项目综合巡检并生成报告'}</button>
          </div>
          {selectedProject && <div className={`mini-card inspection-project-config-card${selectedProjectPathMissing ? ' inspection-project-config-card--warning' : ''}`} style={{ marginBottom: 12 }}>
            <div className="inspection-project-config-head">
              <div>
                <strong>部署服务器</strong>
                <div>{(selectedProject.servers || []).join(', ') || '未配置'}</div>
              </div>
              <button className="btn btn-subtle" onClick={openProjectPathEditor}>{selectedProjectPathMissing ? '配置部署路径' : '修改部署配置'}</button>
            </div>
            <div className="inspection-path-grid">
              <div><span>部署路径</span><strong>{selectedProject.deploy_path || '未配置'}</strong></div>
              <div><span>配置路径</span><strong>{selectedProject.config_path || '未配置'}</strong></div>
              <div><span>日志路径</span><strong>{selectedProject.log_path || '未配置'}</strong></div>
              <div><span>备份路径</span><strong>{selectedProject.backup_path || '未配置'}</strong></div>
              <div><span>运行用户</span><strong>{selectedProject.runtime_user || '未配置'}</strong></div>
              <div><span>主端口</span><strong>{selectedProject.main_port || '未配置'}</strong></div>
            </div>
            {selectedProjectPathMissing && <div className="alert alert-warning" style={{ marginTop: 10 }}>项目巡检依赖部署路径、日志路径和备份路径。请先配置这些路径，巡检命令中的 &lt;deploy_path&gt;、&lt;log_path&gt;、&lt;backup_path&gt; 会按配置自动替换。</div>}
          </div>}
          {categoryOptions(categories.project || [], projectCats, setProjectCats)}
        </section>
      )}

      {tab === 'combined' && (
        <section className="panel-card">
          <h2>项目综合巡检</h2>
          <p className="muted">综合巡检会先检查项目关联服务器基础风险，再检查项目文件、配置、接口、白名单、备份和运行环境，形成项目是否可安全稳定运行的最终结论。</p>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 16 }}>
            <label>项目 <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>{projects.map((p) => <option key={p.id} value={p.id}>{p.name || p.id}</option>)}</select></label>
            <button className="btn primary" onClick={runCombined} disabled={running || !projectId}>{running ? '巡检中...' : '一键综合巡检并生成报告'}</button>
          </div>
          {selectedProject && <div className="mini-card" style={{ marginBottom: 12 }}><strong>综合对象</strong><div>{selectedProject.name || selectedProject.id}</div><small className="muted">关联服务器：{(selectedProject.servers || []).join(', ') || '未配置'}；部署路径：{selectedProject.deploy_path || '未配置'}；日志路径：{selectedProject.log_path || '未配置'}；综合评分会同时计算服务器底座风险和项目业务风险。</small></div>}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 12 }}>
            <div>{categoryOptions(categories.server || [], serverCats, setServerCats, itemConfigs.server, openItemConfigEditor)}</div>
            <div>{categoryOptions(categories.project || [], projectCats, setProjectCats)}</div>
          </div>
        </section>
      )}

      {tab === 'runs' && (
        <section className="panel-card">
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <h2>巡检记录</h2>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <button className="btn btn-subtle" disabled={selectedRunIds.length === 0} onClick={generateSelectedReports}>合并生成报告（{selectedRunIds.length}）</button>
              <button className="btn btn-danger" disabled={selectedRunIds.length === 0} onClick={() => deleteInspectionRuns(selectedRunIds)}>删除选中（{selectedRunIds.length}）</button>
              <button className="btn btn-subtle" disabled={selectedRunIds.length === 0} onClick={() => setSelectedRunIds([])}>清空选择</button>
              <label>范围 <select value={runFilter} onChange={(e) => { setRunFilter(e.target.value); setRunOffset(0); setSelectedRunIds([]) }}><option value="">全部</option><option value="SERVER">服务器</option><option value="PROJECT">项目</option><option value="PROJECT_COMBINED">综合</option></select></label>
            </div>
          </div>
          {runs.length === 0 ? <EmptyState title="暂无巡检记录" /> : <><div className="table-scroll"><table className="data-table"><thead><tr><th><input type="checkbox" checked={runs.length > 0 && runs.every((r: any) => selectedRunIds.includes(r.id))} onChange={(e) => setSelectedRunIds(e.target.checked ? runs.map((r: any) => r.id) : [])} /></th><th>时间</th><th>范围</th><th>对象</th><th>评分</th><th>风险</th><th>状态</th><th>操作</th></tr></thead><tbody>{runs.map((r: any) => <tr key={r.id}><td><input type="checkbox" checked={selectedRunIds.includes(r.id)} onChange={(e) => toggleRunSelection(r.id, e.target.checked)} /></td><td>{formatTime(r.created_at)}</td><td>{r.scope_type}</td><td>{r.project_id || r.server_id || '-'}</td><td><span className={`status-badge status-badge--${scoreTone(r.score)}`}>{r.score}</span></td><td>{r.high_count}/{r.medium_count}/{r.low_count}</td><td><StatusBadge value={r.status} /></td><td><button className="btn btn-subtle" onClick={() => inspection.runDetail(r.id).then((res: any) => { setCurrentResult(res.data); const st = res.data?.run?.status; if (st === 'RUNNING' || st === 'PENDING') setActiveRunIds((prev) => Array.from(new Set([...prev, r.id]))); })}>详情</button><button className="btn btn-subtle" onClick={() => openRunDetailRaw(r.id)}>原始数据</button><button className="btn btn-subtle" onClick={() => generateReport(r.id)}>生成报告</button><button className="btn btn-danger" onClick={() => deleteInspectionRuns([r.id])}>删除</button></td></tr>)}</tbody></table></div><PaginationControls total={runsTotal} limit={runPageSize} offset={runOffset} onChange={setRunOffset} onPageSizeChange={setRunPageSize} /></>}
          {currentResult && <div className="mini-card" style={{ marginTop: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <strong>当前详情：{currentResult.run?.summary || currentResult.summary || '-'}</strong>
              <span><StatusBadge value={currentResult.run?.status || '-'} /></span>
            </div>
            <div style={{ marginTop: 8, display: 'grid', gap: 8 }}>
              {(currentResult.run?.status === 'RUNNING' || currentResult.run?.status === 'PENDING') && <div className="alert alert-info">巡检执行中，页面每 2 秒自动刷新执行过程。已完成 {(currentResult.items || []).length} 项。</div>}
              {currentResult.ledger && <div className="stats-grid"><div className="stat-card"><span>巡检项</span><strong>{currentResult.ledger.item_count}</strong><small>已入台账</small></div><div className="stat-card"><span>未闭环</span><strong>{currentResult.ledger.open_issue_count}</strong><small>待处理/处理中</small></div><div className="stat-card"><span>已闭环</span><strong>{currentResult.ledger.closed_issue_count}</strong><small>已修复/验证/忽略</small></div><div className="stat-card"><span>执行耗时</span><strong>{currentResult.ledger.duration_text}</strong><small>{currentResult.ledger.trigger_type || '-'}</small></div></div>}
              {(currentResult.category_summary || []).length > 0 && <div className="table-scroll"><table className="data-table"><thead><tr><th>分类</th><th>总数</th><th>通过</th><th>风险</th><th>错误</th><th>高/中/低</th></tr></thead><tbody>{(currentResult.category_summary || []).map((c: any) => <tr key={c.category}><td>{c.category}</td><td>{c.total}</td><td>{c.pass}</td><td>{c.risk}</td><td>{c.error}</td><td>{c.high}/{c.medium}/{c.low}</td></tr>)}</tbody></table></div>}
              <div className="table-scroll"><table className="data-table"><thead><tr><th>分类</th><th>巡检项</th><th>等级</th><th>结果</th><th>可验证 Shell 命令</th><th>建议</th></tr></thead><tbody>{(currentResult.items || []).length === 0 ? <tr><td colSpan={6}>暂无巡检项结果；如果状态为 RUNNING，请等待执行输出。</td></tr> : (currentResult.items || []).map((i: any) => <tr key={i.id}><td>{i.category}</td><td>{i.item_name}</td><td><RiskBadge level={i.risk_level} label={riskLabel(i.risk_level)} /></td><td>{i.message}</td><td><pre className="inspection-command-cell">{extractShellCommand(i.raw_output, i.command) || '-'}</pre></td><td>{i.suggestion}</td></tr>)}</tbody></table></div>
              <div>
                <strong>执行过程</strong>
                <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 320, overflow: 'auto', background: 'rgba(15,23,42,.05)', borderRadius: 12, padding: 12 }}>
{(currentResult.logs || currentResult.items || []).map((l: any) => formatExecutionEntry(l)).join('\n\n---\n\n') || '暂无执行输出'}
                </pre>
              </div>
            </div>
          </div>}
        </section>
      )}

      {tab === 'ledger' && (
        <section className="panel-card">
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <div>
              <h2>巡检台账与报表</h2>
              <p className="muted">按巡检方案保留每日台账、每周报表、月度安全报告口径，沉淀巡检记录、风险统计和整改闭环。台账来源于巡检记录，删除台账历史会同步删除对应巡检记录。</p>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <label>周期 <select value={ledgerPeriod} onChange={(e) => { setLedgerPeriod(e.target.value); setLedgerOffset(0); setSelectedLedgerRunIds([]); reloadLedger(e.target.value, 0) }}><option value="daily">每日台账</option><option value="weekly">每周报表</option><option value="monthly">月度报告</option></select></label>
              <button className="btn btn-subtle" onClick={() => reloadLedger()}>刷新台账</button>
              <button className="btn primary" onClick={() => generatePeriodicInspectionReport(ledgerPeriod)}>生成{ledgerPeriod === 'daily' ? '每日台账' : ledgerPeriod === 'weekly' ? '每周报表' : '月度报告'}</button>
              <button className="btn btn-danger" disabled={selectedLedgerRunIds.length === 0} onClick={() => deleteInspectionRuns(selectedLedgerRunIds)}>删除选中台账（{selectedLedgerRunIds.length}）</button>
              <button className="btn btn-danger" disabled={Number(ledgerData.total || 0) === 0} onClick={deleteLedgerHistoryByPeriod}>删除当前周期历史</button>
            </div>
          </div>
          <div className="stats-grid" style={{ marginTop: 12 }}>
            <div className="stat-card"><span>巡检记录</span><strong>{ledgerData.summary?.run_count || 0}</strong><small>当前周期</small></div>
            <div className="stat-card"><span>平均评分</span><strong>{ledgerData.summary?.avg_score || 0}</strong><small>安全评分</small></div>
            <div className="stat-card"><span>高 / 中 / 低</span><strong>{ledgerData.summary?.high_count || 0}/{ledgerData.summary?.medium_count || 0}/{ledgerData.summary?.low_count || 0}</strong><small>风险数量</small></div>
            <div className="stat-card"><span>未闭环</span><strong>{ledgerData.summary?.open_issue_count || 0}</strong><small>OPEN + PROCESSING</small></div>
          </div>
          {(ledgerData.items || []).length === 0 ? <EmptyState title="暂无台账记录" description="执行巡检后会自动沉淀到台账中，可生成每日/每周/月度报表。" /> : <><div className="table-scroll" style={{ marginTop: 12 }}><table className="data-table"><thead><tr><th><input type="checkbox" checked={(ledgerData.items || []).length > 0 && (ledgerData.items || []).every((row: any) => selectedLedgerRunIds.includes(row.run_id))} onChange={(e) => setSelectedLedgerRunIds(e.target.checked ? (ledgerData.items || []).map((row: any) => row.run_id) : [])} /></th><th>时间</th><th>范围</th><th>对象</th><th>状态</th><th>评分</th><th>巡检项</th><th>高/中/低</th><th>未闭环</th><th>报告</th><th>操作</th></tr></thead><tbody>{(ledgerData.items || []).map((row: any) => <tr key={row.run_id}><td><input type="checkbox" checked={selectedLedgerRunIds.includes(row.run_id)} onChange={(e) => toggleLedgerRunSelection(row.run_id, e.target.checked)} /></td><td>{formatTime(row.inspection_time)}</td><td>{row.scope_type}</td><td>{row.target}</td><td><StatusBadge value={row.status} /></td><td><span className={`status-badge status-badge--${scoreTone(row.score)}`}>{row.score}</span></td><td>{row.item_count}</td><td>{row.high_count}/{row.medium_count}/{row.low_count}</td><td>{row.open_issue_count}</td><td>{row.report_id || '-'}</td><td><button className="btn btn-subtle" onClick={() => inspection.runDetail(row.run_id).then((res: any) => { setCurrentResult(res.data); setTab('runs') })}>查看详情</button><button className="btn btn-danger" onClick={() => deleteInspectionRuns([row.run_id])}>删除</button></td></tr>)}</tbody></table></div><PaginationControls total={Number(ledgerData.total || 0)} limit={ledgerPageSize} offset={ledgerOffset} onChange={setLedgerOffset} onPageSizeChange={setLedgerPageSize} /></>}

          <div className="mini-card" style={{ marginTop: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <strong>巡检报表</strong>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button className="btn btn-subtle" onClick={() => reloadInspectionReports()}>刷新报表</button>
                <button className="btn btn-danger" disabled={selectedReportIds.length === 0} onClick={() => deleteInspectionReports(selectedReportIds)}>删除选中报表（{selectedReportIds.length}）</button>
              </div>
            </div>
            {inspectionReports.length === 0 ? <EmptyState title="暂无巡检报表" description="生成单次/合并/周期巡检报告后会在这里分页展示。" /> : <><div className="table-scroll" style={{ marginTop: 8 }}><table className="data-table"><thead><tr><th><input type="checkbox" checked={inspectionReports.length > 0 && inspectionReports.every((r: any) => selectedReportIds.includes(r.id))} onChange={(e) => setSelectedReportIds(e.target.checked ? inspectionReports.map((r: any) => r.id) : [])} /></th><th>生成时间</th><th>标题</th><th>对象</th><th>格式</th><th>大小</th><th>状态</th><th>操作</th></tr></thead><tbody>{inspectionReports.map((r: any) => <tr key={r.id}><td><input type="checkbox" checked={selectedReportIds.includes(r.id)} onChange={(e) => toggleReportSelection(r.id, e.target.checked)} /></td><td>{formatTime(r.created_at || r.generated_at)}</td><td>{r.title || r.id}</td><td>{r.target_id || '-'}</td><td>{r.format || '-'}</td><td>{r.size_bytes ? `${Math.ceil(Number(r.size_bytes) / 1024)} KB` : '-'}</td><td><StatusBadge value={r.status || 'ready'} /></td><td><button className="btn btn-danger" onClick={() => deleteInspectionReports([r.id])}>删除</button></td></tr>)}</tbody></table></div><PaginationControls total={inspectionReportsTotal} limit={reportPageSize} offset={reportOffset} onChange={setReportOffset} onPageSizeChange={setReportPageSize} /></>}
          </div>
          <div className="mini-card" style={{ marginTop: 12 }}>
            <strong>报表口径</strong>
            <ul className="muted" style={{ margin: '8px 0 0', paddingLeft: 18 }}>
              <li>每日台账：登录异常、命令高危操作、接口异常、服务状态、备份任务执行异常，无风险也保留正常记录。</li>
              <li>每周报表：汇总程序文件、配置文件、白名单、端口、权限、备份文件检查结果和整改进度。</li>
              <li>月度报告：复盘当月安全风险、入侵尝试、漏洞问题、整改情况，并输出规则优化建议。</li>
            </ul>
          </div>
        </section>
      )}

      {tab === 'issues' && (
        <section className="panel-card">
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <h2>风险问题</h2>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <label>状态 <select value={issueFilter} onChange={(e) => setIssueFilter(e.target.value)}><option value="">全部</option><option value="OPEN">待处理</option><option value="PROCESSING">处理中</option><option value="FIXED">已修复</option><option value="VERIFIED">已验证</option><option value="IGNORED">已忽略</option></select></label>
              <button className="btn btn-danger" disabled={selectedIssueIds.length === 0} onClick={() => deleteIssues(selectedIssueIds)}>删除选中（{selectedIssueIds.length}）</button>
              <button className="btn btn-subtle" disabled={selectedIssueIds.length === 0} onClick={() => setSelectedIssueIds([])}>清空选择</button>
            </div>
          </div>
          {issues.length === 0 ? <EmptyState title="暂无风险问题" description="巡检发现的高/中/低风险会进入这里进行闭环。" /> : <div className="table-scroll"><table className="data-table"><thead><tr><th><input type="checkbox" checked={issues.length > 0 && issues.every((i) => selectedIssueIds.includes(i.id))} onChange={(e) => setSelectedIssueIds(e.target.checked ? issues.map((i) => i.id) : [])} /></th><th>等级</th><th>标题</th><th>对象</th><th>描述</th><th>状态</th><th>操作</th></tr></thead><tbody>{issues.map((i) => <tr key={i.id}><td><input type="checkbox" checked={selectedIssueIds.includes(i.id)} onChange={(e) => setSelectedIssueIds((prev) => e.target.checked ? Array.from(new Set([...prev, i.id])) : prev.filter((x) => x !== i.id))} /></td><td><RiskBadge level={i.risk_level} label={riskLabel(i.risk_level)} /></td><td>{i.title}</td><td>{i.project_id || i.server_id || '-'}</td><td>{i.description}</td><td><StatusBadge value={i.status} /></td><td><button className="btn btn-subtle" onClick={() => updateIssue(i.id, 'PROCESSING')}>处理中</button><button className="btn btn-subtle" onClick={() => updateIssue(i.id, 'FIXED')}>已修复</button><button className="btn btn-subtle" onClick={() => updateIssue(i.id, 'VERIFIED')}>已验证</button><button className="btn btn-subtle" onClick={() => updateIssue(i.id, 'IGNORED')}>忽略</button><button className="btn btn-danger" onClick={() => deleteIssue(i.id)}>删除</button></td></tr>)}</tbody></table></div>}
        </section>
      )}

      {tab === 'rules' && (
        <section className="panel-card">
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <div>
              <h2>巡检规则</h2>
              <p className="muted">支持新增、删除、重命名、启停、风险等级、规则内容、说明、建议和 JSON 配置编辑。内置规则首次编辑会自动落库。</p>
            </div>
            <button className="btn primary" onClick={openCreateRule}>新增规则</button>
          </div>
          {rules.length === 0 ? <EmptyState title="暂无规则" /> : <div className="table-scroll"><table className="data-table"><thead><tr><th>范围</th><th>分类</th><th>规则</th><th>等级</th><th>状态</th><th>内容/说明</th><th>建议</th><th>操作</th></tr></thead><tbody>{rules.map((r: any) => <tr key={r.id || r.rule_code}><td>{r.scope_type}</td><td>{r.category}</td><td>{r.rule_name || r.rule_code}<br /><small className="muted">{r.rule_code}</small>{r.builtin && <><br /><small className="muted">内置</small></>}</td><td><RiskBadge level={r.risk_level} label={riskLabel(r.risk_level)} /></td><td><StatusBadge value={r.enabled === false ? 'DISABLED' : 'ENABLED'} /></td><td><div style={{ maxWidth: 360 }}><strong>{r.rule_content || '-'}</strong><br /><small className="muted">{r.description}</small></div></td><td>{r.suggestion || '-'}</td><td><button className="btn btn-subtle" onClick={() => editRule(r)}>编辑</button><button className="btn btn-subtle" onClick={() => toggleRule(r)}>{r.enabled === false ? '启用' : '停用'}</button><button className="btn btn-subtle" onClick={() => deleteRule(r)}>删除</button></td></tr>)}</tbody></table></div>}
          {ruleEditor.open && (
            <div className="inspection-modal-overlay" onClick={() => setRuleEditor({ open: false, mode: 'create', originalCode: '', form: {} })}>
              <div className="panel-card inspection-rule-modal" onClick={(e) => e.stopPropagation()}>
                <div className="inspection-modal-header">
                  <div>
                    <h2 style={{ margin: 0 }}>{ruleEditor.mode === 'create' ? '新增巡检规则' : '编辑巡检规则'}</h2>
                    <p className="muted" style={{ margin: '4px 0 0' }}>规则内容建议填写可执行只读命令、替换变量、判定要点和整改口径。</p>
                  </div>
                  <button className="btn btn-subtle" onClick={() => setRuleEditor({ open: false, mode: 'create', originalCode: '', form: {} })}>关闭</button>
                </div>
                <div className="inspection-rule-form-grid">
                  <label>规则编码<input value={ruleEditor.form.rule_code || ''} onChange={(e) => updateRuleForm({ rule_code: e.target.value.toUpperCase().replace(/\s+/g, '_') })} placeholder="CUSTOM_RULE_CODE" /></label>
                  <label>规则名称<input value={ruleEditor.form.rule_name || ''} onChange={(e) => updateRuleForm({ rule_name: e.target.value })} placeholder="规则名称" /></label>
                  <label>范围<select value={ruleEditor.form.scope_type || 'SERVER'} onChange={(e) => updateRuleForm({ scope_type: e.target.value })}><option value="SERVER">SERVER</option><option value="PROJECT">PROJECT</option><option value="PROJECT_COMBINED">PROJECT_COMBINED</option><option value="BOTH">BOTH</option></select></label>
                  <label>分类<input value={ruleEditor.form.category || ''} onChange={(e) => updateRuleForm({ category: e.target.value.toUpperCase().replace(/\s+/g, '_') })} placeholder="CUSTOM" /></label>
                  <label>风险等级<select value={ruleEditor.form.risk_level || 'MEDIUM'} onChange={(e) => updateRuleForm({ risk_level: e.target.value })}><option value="HIGH">HIGH 高危</option><option value="MEDIUM">MEDIUM 中危</option><option value="LOW">LOW 低危</option><option value="NONE">NONE 无风险</option></select></label>
                  <label>状态<select value={ruleEditor.form.enabled === false ? 'false' : 'true'} onChange={(e) => updateRuleForm({ enabled: e.target.value === 'true' })}><option value="true">启用</option><option value="false">停用</option></select></label>
                </div>
                <div className="inspection-rule-editor-body">
                  <div className="inspection-rule-main-column">
                    <label>规则内容 / 可执行命令<textarea rows={14} value={ruleEditor.form.rule_content || ''} onChange={(e) => updateRuleForm({ rule_content: e.target.value })} placeholder={'# 示例：只读巡检命令\nss -ntulp\ndf -h\n# 判定要点：高危端口外网暴露、磁盘超过 85%'} /></label>
                    <label>规则配置 JSON<textarea rows={8} value={ruleEditor.form.config_text || '{}'} onChange={(e) => updateRuleForm({ config_text: e.target.value })} placeholder='{ "commands": ["ss -ntulp"], "threshold": 85 }' /></label>
                  </div>
                  <div className="inspection-rule-side-column">
                    <label>规则说明<textarea rows={7} value={ruleEditor.form.description || ''} onChange={(e) => updateRuleForm({ description: e.target.value })} /></label>
                    <label>整改建议<textarea rows={7} value={ruleEditor.form.suggestion || ''} onChange={(e) => updateRuleForm({ suggestion: e.target.value })} /></label>
                    <div className="mini-card">
                      <strong>变量约定</strong>
                      <p className="muted">项目规则可使用 &lt;deploy_path&gt;、&lt;config_path&gt;、&lt;log_path&gt;、&lt;backup_path&gt;、&lt;main_port&gt;、&lt;runtime_user&gt;，执行时由项目部署信息替换。</p>
                    </div>
                  </div>
                </div>
                <div className="inspection-modal-actions">
                  <button className="btn btn-subtle" onClick={() => setRuleEditor({ open: false, mode: 'create', originalCode: '', form: {} })}>取消</button>
                  <button className="btn primary" onClick={submitRuleForm}>{ruleEditor.mode === 'create' ? '新增规则' : '保存规则'}</button>
                </div>
              </div>
            </div>
          )}
        </section>
      )}


      {projectPathEditor.open && (
        <div className="inspection-modal-overlay" onClick={() => setProjectPathEditor({ open: false, form: {} })}>
          <div className="panel-card inspection-path-modal" onClick={(e) => e.stopPropagation()}>
            <div className="inspection-modal-header">
              <div>
                <h2 style={{ margin: 0 }}>配置项目部署信息</h2>
                <p className="muted" style={{ margin: '4px 0 0' }}>保存后，项目巡检和综合巡检会使用这些路径替换规则命令中的占位变量。</p>
              </div>
              <button className="btn btn-subtle" onClick={() => setProjectPathEditor({ open: false, form: {} })}>关闭</button>
            </div>
            <div className="inspection-rule-form-grid">
              <label>项目 ID<input value={projectPathEditor.form.project_id || ''} disabled /></label>
              <label>部署服务器<select value={projectPathEditor.form.server_id || ''} onChange={(e) => updateProjectPathForm({ server_id: e.target.value })}>
                <option value="">请选择服务器</option>
                {servers.map((s) => <option key={s.id || s.name} value={s.id || s.name}>{s.name || s.id} · {s.host || '-'}</option>)}
              </select></label>
              <label>部署角色<input value={projectPathEditor.form.deploy_role || ''} onChange={(e) => updateProjectPathForm({ deploy_role: e.target.value })} placeholder="APP / DB / NGINX" /></label>
              <label>主端口<input value={projectPathEditor.form.main_port || ''} onChange={(e) => updateProjectPathForm({ main_port: e.target.value })} placeholder="例如 8000" /></label>
              <label>运行用户<input value={projectPathEditor.form.runtime_user || ''} onChange={(e) => updateProjectPathForm({ runtime_user: e.target.value })} placeholder="例如 appuser" /></label>
              <label>状态<select value={projectPathEditor.form.active === false ? 'false' : 'true'} onChange={(e) => updateProjectPathForm({ active: e.target.value === 'true' })}><option value="true">启用</option><option value="false">停用</option></select></label>
            </div>
            <div className="inspection-path-editor-grid">
              <label>部署路径 &lt;deploy_path&gt;<input value={projectPathEditor.form.deploy_path || ''} onChange={(e) => updateProjectPathForm({ deploy_path: e.target.value })} placeholder="例如 /opt/apps/project-a" /></label>
              <label>配置路径 &lt;config_path&gt;<input value={projectPathEditor.form.config_path || ''} onChange={(e) => updateProjectPathForm({ config_path: e.target.value })} placeholder="例如 /opt/apps/project-a/config" /></label>
              <label>日志路径 &lt;log_path&gt;<input value={projectPathEditor.form.log_path || ''} onChange={(e) => updateProjectPathForm({ log_path: e.target.value })} placeholder="例如 /var/log/project-a" /></label>
              <label>备份路径 &lt;backup_path&gt;<input value={projectPathEditor.form.backup_path || ''} onChange={(e) => updateProjectPathForm({ backup_path: e.target.value })} placeholder="例如 /data/backups/project-a" /></label>
            </div>
            <div className="mini-card" style={{ marginTop: 12 }}>
              <strong>配置说明</strong>
              <p className="muted">这些配置会落入 project_server_relations，用于项目巡检命令生成、路径检查、日志扫描、备份校验和综合报告。第一阶段仍只执行只读命令。</p>
            </div>
            <div className="inspection-modal-actions">
              <button className="btn btn-subtle" onClick={() => setProjectPathEditor({ open: false, form: {} })}>取消</button>
              <button className="btn primary" onClick={saveProjectPathConfig}>保存部署配置</button>
            </div>
          </div>
        </div>
      )}

      {/* 巡检项目配置编辑器（可选/可编辑/可调整） */}
      {itemConfigEditor.open && itemConfigEditor.item && (
        <ItemConfigEditorModal
          item={itemConfigEditor.item}
          onClose={() => setItemConfigEditor({ open: false, item: null })}
          onSave={saveItemConfig}
          onToggleEnabled={toggleItemConfigEnabled}
        />
      )}

      {/* 巡检记录原始数据查看器 */}
      {runDetailRaw.open && (
        <RunDetailRawModal
          runId={runDetailRaw.runId}
          items={runDetailRaw.items}
          onClose={() => setRunDetailRaw({ open: false, runId: '', items: [] })}
        />
      )}
    </div>
  )
}

// ============ 巡检项目配置编辑器 ============
function ItemConfigEditorModal({ item, onClose, onSave, onToggleEnabled }: { item: any; onClose: () => void; onSave: (patch: any) => void; onToggleEnabled: (id: string) => void }) {
  const [name, setName] = useState(item.item_name || '')
  const [desc, setDesc] = useState(item.description || '')
  const [enabled, setEnabled] = useState(item.enabled !== false)
  const [sortOrder, setSortOrder] = useState(item.sort_order || 0)
  const [rules, setRules] = useState<any[]>(item.rules || [])

  function toggleRule(idx: number) {
    setRules((prev) => prev.map((r, i) => i === idx ? { ...r, enabled: r.enabled === false ? true : false } : r))
  }

  function quickToggle() {
    onToggleEnabled(item.id)
    setEnabled(!enabled)
  }

  return (
    <div className="inspection-modal-overlay" onClick={onClose}>
      <div className="panel-card inspection-rule-modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 720 }}>
        <div className="inspection-modal-header">
          <div>
            <h2 style={{ margin: 0 }}>配置巡检项目</h2>
            <p className="muted" style={{ margin: '4px 0 0' }}>可启用/禁用项目，可编辑名称描述，可调整关联规则。</p>
          </div>
          <button className="btn btn-subtle" onClick={onClose}>关闭</button>
        </div>
        <div className="inspection-rule-form-grid">
          <label>编码（不可改）<input value={item.item_code} disabled /></label>
          <label>名称<input value={name} onChange={(e) => setName(e.target.value)} /></label>
          <label>状态<select value={enabled ? 'true' : 'false'} onChange={(e) => setEnabled(e.target.value === 'true')}><option value="true">启用</option><option value="false">禁用</option></select></label>
          <label>执行顺序<input type="number" value={sortOrder} onChange={(e) => setSortOrder(Number(e.target.value))} /></label>
          <label style={{ gridColumn: '1 / -1' }}>描述<textarea value={desc} onChange={(e) => setDesc(e.target.value)} rows={2} /></label>
        </div>
        <div className="mini-card" style={{ marginTop: 12 }}>
          <strong>关联规则（{rules.length}）</strong>
          <div style={{ display: 'grid', gap: 6, marginTop: 8 }}>
            {rules.map((r, idx) => (
              <div key={r.id || r.rule_code} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: 6, background: 'var(--bg-elev, #fafafa)', borderRadius: 4 }}>
                <input type="checkbox" checked={r.enabled !== false} onChange={() => toggleRule(idx)} />
                <span style={{ flex: 1 }}><code>{r.rule_code}</code></span>
                <small className="muted">顺序 {r.sort_order || 0}</small>
              </div>
            ))}
          </div>
        </div>
        <div className="inspection-modal-actions">
          <button className="btn btn-subtle" onClick={quickToggle}>{enabled ? '快速禁用' : '快速启用'}</button>
          <button className="btn btn-subtle" onClick={onClose}>取消</button>
          <button className="btn primary" onClick={() => onSave({ item_name: name, description: desc, enabled, sort_order: sortOrder, rules })}>保存</button>
        </div>
      </div>
    </div>
  )
}

// ============ 巡检记录原始数据查看器 ============
function RunDetailRawModal({ runId, items, onClose }: { runId: string; items: any[]; onClose: () => void }) {
  return (
    <div className="inspection-modal-overlay" onClick={onClose}>
      <div className="panel-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 960, maxHeight: '85vh', overflow: 'auto' }}>
        <div className="inspection-modal-header">
          <div>
            <h2 style={{ margin: 0 }}>巡检原始数据</h2>
            <p className="muted" style={{ margin: '4px 0 0' }}>运行 ID: {runId} · 共 {items.length} 项</p>
          </div>
          <button className="btn btn-subtle" onClick={onClose}>关闭</button>
        </div>
        <div style={{ display: 'grid', gap: 12 }}>
          {items.map((it) => (
            <div key={it.id} className="mini-card">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <strong>{it.item_name || it.item_code}</strong>
                <small className="muted">[{it.category}]</small>
                <span className={`status-pill status-${(it.status || 'normal').toLowerCase()}`} style={{ fontSize: 11 }}>{it.status}</span>
                {it.risk_level && it.risk_level !== 'NONE' && <span style={{ fontSize: 11, color: it.risk_level === 'HIGH' ? '#c0392b' : it.risk_level === 'MEDIUM' ? '#d35400' : '#7f8c8d' }}>风险: {it.risk_level}</span>}
              </div>
              {it.message && <div style={{ marginTop: 6 }}><strong>结果描述：</strong>{it.message}</div>}
              {it.suggestion && <div style={{ marginTop: 4, color: '#2c3e50' }}><strong>建议：</strong>{it.suggestion}</div>}
              {it.raw_output && (
                <details open style={{ marginTop: 6 }}>
                  <summary style={{ cursor: 'pointer', fontWeight: 'bold' }}>原始输出（点击折叠/展开）</summary>
                  <pre style={{ background: '#1e1e1e', color: '#d4d4d4', padding: 10, borderRadius: 4, overflow: 'auto', fontSize: 12, marginTop: 6, maxHeight: 280 }}>{it.raw_output}</pre>
                </details>
              )}
            </div>
          ))}
        </div>
        <div className="inspection-modal-actions">
          <button className="btn primary" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  )
}
