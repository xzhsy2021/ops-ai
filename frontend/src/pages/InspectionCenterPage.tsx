import { Fragment, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { inspection, reports } from '../api'
import { ROUTES } from '../routes'
import { EmptyState, FavoriteButton, RiskBadge, RiskConfirmDialog, StatusBadge } from '../components/ui'
import { DEFAULT_PAGE_SIZE, PaginationControls } from './inspection/PaginationControls'
import { RunDetailRawModal } from './inspection/RunDetailRawModal'
import { ItemConfigEditorModal } from './inspection/ItemConfigEditorModal'
import { CategoryOptions } from './inspection/CategoryOptions'
import { extractShellCommand, formatExecutionEntry, formatTime, isServerInspectable, normalizeServerStatus, riskLabel, scoreTone, serverStatusText } from './inspection/inspectionHelpers'
import { OverviewTab } from './inspection/OverviewTab'
import { ServerProfileStrip } from './inspection/ServerProfileStrip'
import { ServerStatsGrid } from './inspection/ServerStatsGrid'
import { ServerGroupChips } from './inspection/ServerGroupChips'
import { ServerToolbar } from './inspection/ServerToolbar'
import { ServerCategoryChips } from './inspection/ServerCategoryChips'
import { ServerAdvancedSettings } from './inspection/ServerAdvancedSettings'
import DiagnosisCard from '../components/v10/DiagnosisCard'

type TabKey = 'overview' | 'server' | 'project' | 'combined' | 'runs' | 'ledger' | 'issues' | 'rules'

export default function InspectionCenterPage() {
  const [tab, setTab] = useState<TabKey>('overview')
  const [overview, setOverview] = useState<any>({})
  const [categories, setCategories] = useState<any>({ server: [], project: [] })
  const [itemConfigs, setItemConfigs] = useState<any>({ server: [], project: [] })
  const [itemConfigEditor, setItemConfigEditor] = useState<{ open: boolean; item: any }>({ open: false, item: null })
  const [runDetailRaw, setRunDetailRaw] = useState<{ open: boolean; runId: string; items: any[] }>({ open: false, runId: '', items: [] })
  const [servers, setServers] = useState<any[]>([])
  const [inspectionProfiles, setInspectionProfiles] = useState<any[]>([])
  const [profilePreviewData, setProfilePreviewData] = useState<any>(null)
  const [profileRunMode, setProfileRunMode] = useState<'profile' | 'issue_retry'>('profile')
  const [profileConfirmOpen, setProfileConfirmOpen] = useState(false)
  const [profileConfirmValue, setProfileConfirmValue] = useState('')
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
  const [issuesTotal, setIssuesTotal] = useState(0)
  const [issueOffset, setIssueOffset] = useState(0)
  const [issuePageSize, setIssuePageSize] = useState(DEFAULT_PAGE_SIZE)
  const [rules, setRules] = useState<any[]>([])
  const [rulesTotal, setRulesTotal] = useState(0)
  const [ruleOffset, setRuleOffset] = useState(0)
  const [rulePageSize, setRulePageSize] = useState(DEFAULT_PAGE_SIZE)
  const [ruleFilter, setRuleFilter] = useState('')
  const [ruleScopeFilter, setRuleScopeFilter] = useState('')
  const [ruleRiskFilter, setRuleRiskFilter] = useState('')
  const [serverFilter, setServerFilter] = useState('')
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
      const [ov, cat, srv, prj, profileRes, runRes, ledgerRes, issueRes, ruleRes, reportRes, itemCfgRes]: any[] = await Promise.all([
        inspection.overview(),
        inspection.categories(),
        inspection.servers(),
        inspection.projects(),
        inspection.profiles(),
        inspection.runs({ limit: runPageSize, offset: runOffset }),
        inspection.ledger({ period: ledgerPeriod, limit: ledgerPageSize, offset: ledgerOffset }),
        inspection.issues({ status: issueFilter || undefined, limit: issuePageSize, offset: issueOffset }),
        inspection.rules({ keyword: ruleFilter || undefined, scope_type: ruleScopeFilter || undefined, risk_level: ruleRiskFilter || undefined, limit: rulePageSize, offset: ruleOffset }),
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
      setInspectionProfiles(profileRes.data?.items || [])
      setRuns(runRes.data?.items || [])
      setRunsTotal(Number(runRes.data?.total || 0))
      setLedgerData(ledgerRes.data || { summary: {}, items: [] })
      setIssues(issueRes.data?.items || [])
      setIssuesTotal(Number(issueRes.data?.total || 0))
      setRules(ruleRes.data?.items || [])
      setRulesTotal(Number(ruleRes.data?.total || 0))
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
    const res: any = await inspection.issues({ status: issueFilter || undefined, limit: issuePageSize, offset: issueOffset })
    setIssues(res.data?.items || [])
    setIssuesTotal(Number(res.data?.total || 0))
  }

  async function reloadRules(nextOffset = ruleOffset) {
    const res: any = await inspection.rules({ keyword: ruleFilter || undefined, scope_type: ruleScopeFilter || undefined, risk_level: ruleRiskFilter || undefined, limit: rulePageSize, offset: nextOffset })
    setRules(res.data?.items || [])
    setRulesTotal(Number(res.data?.total || 0))
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
    } catch (e: any) { setError(e?.message || String(e)) }
    finally { setRunning(false) }
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
    } catch (e: any) { setError(e?.message || String(e)) }
    finally {
      setRunning(false)
      // 单击分组触发的巡检在成功后保留 selectedGroups 以便再次点击
      // 但是 running 标志必须重置，否则按钮一直显示"巡检中..."
    }
  }

  async function previewInspectionProfile(profileId: string) {
    if (!profileId) return
    setRunning(true); setError(''); setMessage('')
    try {
      const res: any = await inspection.profilePreview({ profile_id: profileId })
      setProfilePreviewData(res.data)
      setProfileRunMode('profile')
      setProfileConfirmValue('')
      setProfileConfirmOpen(true)
      setMessage(res.data?.summary || '巡检方案预览已生成，请确认后执行。')
    } catch (e: any) { setError(e?.message || String(e)) }
    finally { setRunning(false) }
  }

  async function previewIssueRetry(profileId?: string) {
    const selectedProfile = profileId || inspectionProfiles.find((p: any) => p.id === 'crypto-test-daily')?.id || inspectionProfiles[0]?.id || 'daily-lite'
    setRunning(true); setError(''); setMessage('')
    try {
      const res: any = await inspection.profileRetryIssues({ profile_id: selectedProfile })
      setProfilePreviewData(res.data)
      setProfileRunMode('issue_retry')
      setProfileConfirmValue('')
      setProfileConfirmOpen(true)
      setMessage(res.data?.summary || '复巡目标预览已生成，请确认后执行。')
    } catch (e: any) { setError(e?.message || String(e)) }
    finally { setRunning(false) }
  }

  async function runInspectionProfile() {
    const preview = profilePreviewData || {}
    const profileId = preview.profile?.id || preview.profile_id
    const confirmation = preview.confirmation || {}
    if (!profileId) return setError('巡检方案不存在，请重新预览。')
    const confirmText = (profileConfirmValue.trim() || confirmation.confirm_text || '').trim()
    setRunning(true); setError(''); setMessage('')
    try {
      const profilePayload = {
        profile_id: profileId,
        confirm_text: confirmText,
        expected_count: Number(confirmation.target_count || preview.eligible_count || 0),
        fingerprint: confirmation.fingerprint || '',
      }
      const res: any = profileRunMode === 'issue_retry'
        ? await inspection.profileRetryIssues(profilePayload)
        : await inspection.profileRun(profilePayload)
      const runIds = res.data?.run_ids || []
      if (runIds.length > 0) setActiveRunIds(runIds)
      if (runIds[0]) {
        const detail: any = await inspection.runDetail(runIds[0])
        setCurrentResult(detail.data)
      } else {
        setCurrentResult(res.data)
      }
      setProfileConfirmOpen(false)
      setProfilePreviewData(null)
      setProfileRunMode('profile')
      setProfileConfirmValue('')
      setTab('runs')
      setMessage(res.data?.summary || '巡检方案已执行，报告生成后可在报告中心查看。')
      await Promise.all([reloadRuns('SERVER'), reloadInspectionReports(), reloadLedger()])
    } catch (e: any) { setError(e?.message || String(e)) }
    finally { setRunning(false) }
  }

  function buildRuleForm(rule?: any) {
    const config = rule?.config && typeof rule.config === 'object' ? rule.config : {}
    // 解析阈值/提取器结构，缺失字段填默认值
    const extractor = config.extractor || { mode: 'regex', pattern: '', value_field: 'value' }
    const threshold = config.threshold || { high: null, medium: null, low: null, comparator: '>', unit: 'count' }
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
      // 结构化提取器
      extractor_mode: extractor.mode || 'regex',
      extractor_pattern: extractor.pattern || '',
      extractor_value_field: extractor.value_field || 'value',
      // 结构化阈值
      threshold_high: threshold.high ?? '',
      threshold_medium: threshold.medium ?? '',
      threshold_low: threshold.low ?? '',
      threshold_comparator: threshold.comparator || '>',
      threshold_unit: threshold.unit || 'count',
      threshold_enabled: !!config.threshold,
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
    // 注入结构化提取器
    if (form.extractor_pattern || form.extractor_keywords) {
      config.extractor = {
        mode: form.extractor_mode || 'regex',
        pattern: form.extractor_pattern || '',
        value_field: form.extractor_value_field || 'value',
      }
      if (form.extractor_keywords) {
        config.extractor.keywords = form.extractor_keywords.split(',').map((s: string) => s.trim()).filter(Boolean)
      }
    } else if (config.extractor) {
      delete config.extractor
    }
    // 注入结构化阈值
    if (form.threshold_enabled) {
      const high = form.threshold_high === '' ? null : Number(form.threshold_high)
      const medium = form.threshold_medium === '' ? null : Number(form.threshold_medium)
      const low = form.threshold_low === '' ? null : Number(form.threshold_low)
      if (high === null && medium === null && low === null) {
        return setError('已启用阈值判定，请至少填写 high / medium / low 之一')
      }
      config.threshold = {
        high,
        medium,
        low,
        comparator: form.threshold_comparator || '>',
        unit: form.threshold_unit || 'count',
      }
    } else if (config.threshold) {
      delete config.threshold
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
      await reloadRules()
    } catch (e: any) { setError(e?.message || String(e)) }
  }

  async function toggleRule(rule: any) {
    const code = rule.rule_code || rule.id
    if (!code) return setError('规则编码缺失')
    try {
      const res: any = await inspection.updateRule(code, { enabled: rule.enabled === false })
      setMessage(`规则已${res.data?.enabled ? '启用' : '停用'}：${res.data?.rule_name || code}`)
      await reloadRules()
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
    } catch (e: any) { setError(e?.message || String(e)) }
    finally { setRunning(false) }
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
      const res: any = await inspection.generateReports({ run_ids: ids, format: 'html', title: `巡检合并报告（${ids.length} 条记录）` })
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
  useEffect(() => { reloadIssues().catch(() => undefined) }, [issueFilter, issueOffset, issuePageSize])
  useEffect(() => { reloadRules().catch(() => undefined) }, [ruleFilter, ruleScopeFilter, ruleRiskFilter, ruleOffset, rulePageSize])
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
          setMessage('巡检执行完成，报告已自动生成，可查看详情或报告。')
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
    const kw = serverFilter.toLowerCase()
    for (const s of servers) {
      if (kw) {
        const name = String(s.name || s.id || '').toLowerCase()
        const host = String(s.host || s.ip || '').toLowerCase()
        if (!name.includes(kw) && !host.includes(kw)) continue
      }
      const group = s.group || s.env || '未分组'
      if (!groups[group]) groups[group] = []
      groups[group].push(s)
    }
    return groups
  }, [servers, serverFilter])

  const activeServerIds = useMemo(() => servers.filter(isServerInspectable).map((s) => s.id || s.name).filter(Boolean), [servers])
  const disabledServerCount = useMemo(() => servers.filter((s) => !isServerInspectable(s)).length, [servers])
  const filteredServers = useMemo(() => {
    const kw = (serverFilter || '').trim().toLowerCase()
    if (!kw) return servers
    return servers.filter((s: any) => {
      const id = (s.id || s.name || '').toLowerCase()
      const host = (s.host || s.ip || '').toLowerCase()
      const name = (s.name || '').toLowerCase()
      return id.includes(kw) || host.includes(kw) || name.includes(kw)
    })
  }, [servers, serverFilter])
  // 当前分组展开 + 过滤后可见的服务器 id 列表（用于"全选当前可见"）
  const visibleServerIds = useMemo(() => {
    const ids: string[] = []
    Object.entries(groupedServers).forEach(([group, list]) => {
      if (expandedGroups[group] === false) return
      ;(list as any[]).forEach((s) => {
        if (filteredServers.includes(s)) {
          const id = s.id || s.name
          if (id) ids.push(id)
        }
      })
    })
    return ids
  }, [groupedServers, expandedGroups, filteredServers])

  return (
    <div className="cc-grid-bg page-container inspection-page inspection-cc" style={{ padding: '4px 0 24px' }}>
      <div className="cc-hero">
        <div>
          <span className="cc-hero-eyebrow">OBSERVABILITY · INSPECTION</span>
          <h1 className="cc-hero-title">巡检中心</h1>
          <p className="cc-hero-desc">单项目轻量巡检：平台本地/SSH 执行只读检查，保留台账与报告，AI 仅做摘要、解释和建议。</p>
        </div>
        <div className="cc-hero-stats" style={{ minWidth: 240 }}>
          <div className="cc-hero-stat cc-hero-stat--ok">
            <strong>{overview.server_count || servers.length || 0}</strong><span>服务器</span>
          </div>
          <div className="cc-hero-stat cc-hero-stat--info">
            <strong>{overview.project_count || projects.length || 0}</strong><span>项目</span>
          </div>
          <div className="cc-hero-stat cc-hero-stat--risk">
            <strong>{overview.open_issue_count || 0}</strong><span>待处理</span>
          </div>
        </div>
        <div className="cc-server-detail-actions" style={{ position: 'absolute', top: 22, right: 30, display: 'flex', gap: 8 }}>
          <FavoriteButton url={ROUTES.inspection} label="巡检中心" category="inspection" />
          <Link className="cc-icon-btn" to={ROUTES.reports}>报告中心</Link>
          <button className="cc-icon-btn cc-icon-btn--success" onClick={loadBase} disabled={loading}>{loading ? '加载中…' : '刷新'}</button>
        </div>
      </div>

      {error && <div className="cc-modal-callout cc-modal-callout--danger">{error}</div>}
      {message && <div className="cc-modal-callout cc-modal-callout--success">{message}</div>}

      <nav className="cc-tabbar" aria-label="巡检中心标签">
        {[
          ['overview', '巡检总览', overview.server_count || servers.length || 0],
          ['server', '服务器巡检', servers.length || 0],
          ['project', '项目巡检', projects.length || 0],
          ['runs', '巡检记录', runsTotal || 0],
          ['ledger', '台账报表', inspectionReportsTotal || 0],
          ['issues', '风险问题', issuesTotal || 0],
          ['rules', '巡检规则', rulesTotal || 0],
        ].map(([key, label, count]) => <button key={key as string} className={`cc-tab${tab === key ? ' cc-tab--active' : ''}`} onClick={() => setTab(key as TabKey)}>{label}<small>{count as number}</small></button>)}
      </nav>

      {tab === 'overview' && (
        <>
        <OverviewTab overview={overview} servers={servers} projects={projects} />
        {(overview.open_issue_count > 0 || overview.high_risk_count > 0) && (
          <DiagnosisCard
            title="巡检风险诊断"
            explain={`当前有 ${overview.open_issue_count || 0} 个待处理问题${overview.high_risk_count ? `，其中 ${overview.high_risk_count} 个高风险` : ''}。建议优先处理高风险项，避免影响系统稳定性。`}
            recommend="点击下方按钮进入风险问题页，按优先级逐项处理。高风险项应在 24 小时内响应。"
            actionLabel="查看风险问题"
            actionTo={`${ROUTES.inspection}?tab=issues`}
            level={overview.high_risk_count > 0 ? 'danger' : 'warn'}
          />
        )}
        </>
      )}

      {tab === 'server' && (
        <section className="panel-card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: 4 }}>
            <h2 style={{ margin: 0 }}>服务器巡检</h2>
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>只读命令 · 登录/账号/命令/端口/防火墙/磁盘/服务/备份</span>
          </div>

          <ServerProfileStrip
            profiles={inspectionProfiles}
            previewData={profilePreviewData}
            running={running}
            onPreviewIssueRetry={() => previewIssueRetry()}
            onPreviewInspectionProfile={previewInspectionProfile}
          />

          <ServerStatsGrid
            serverCount={servers.length}
            activeServerCount={activeServerIds.length}
            disabledServerCount={disabledServerCount}
            selectedCount={selectedServerIds.length + selectedGroups.length}
          />

          <ServerToolbar
            filter={serverFilter}
            servers={servers}
            selectedServerId={serverId}
            activeServerCount={activeServerIds.length}
            selectedTargetCount={selectedServerIds.length + selectedGroups.length}
            running={running}
            onFilterChange={setServerFilter}
            onSelectOnlineServers={() => { setSelectedServerIds(activeServerIds); setMessage(`???????/????? ${activeServerIds.length} ????/?? ${disabledServerCount} ???????`) }}
            onClearSelection={() => { setSelectedServerIds([]); setSelectedGroups([]) }}
            onExpandAll={() => setExpandedGroups(Object.fromEntries(Object.keys(groupedServers).map((g) => [g, true])))}
            onCollapseAll={() => setExpandedGroups(Object.fromEntries(Object.keys(groupedServers).map((g) => [g, false])))}
            onSelectedServerChange={setServerId}
            onRunServer={runServer}
            onRunSelectedServers={() => runServersBatch()}
          />

          <ServerGroupChips
            groupedServers={groupedServers}
            selectedGroups={selectedGroups}
            onSelectedGroupsChange={setSelectedGroups}
          />

          {/* 4. 紧凑服务器表格（按分组折叠） */}
          <div className="cc-table-wrap inspection-server-scroll">
            <table className="cc-table inspection-server-table">
              <thead>
                <tr>
                  <th style={{ width: 36 }}><input type="checkbox" title="全选当前可见" checked={visibleServerIds.length > 0 && visibleServerIds.every((id) => selectedServerIds.includes(id))} onChange={(e) => setSelectedServerIds(e.target.checked ? Array.from(new Set([...selectedServerIds, ...visibleServerIds])) : selectedServerIds.filter((x) => !visibleServerIds.includes(x)))} /></th>
                  <th>服务器</th>
                  <th>IP / 主机</th>
                  <th>状态</th>
                  <th>分组</th>
                </tr>
              </thead>
              <tbody>
                {Object.keys(groupedServers).length === 0 && (
                  <tr><td colSpan={5} className="cc-empty-state" style={{ padding: 24 }}>{serverFilter ? `没有匹配「${serverFilter}」的服务器` : '暂无服务器，请先在「服务器」管理中添加'}</td></tr>
                )}
                {Object.entries(groupedServers).map(([group, list]) => {
                  const ids = (list as any[]).filter(isServerInspectable).map((s) => s.id || s.name).filter(Boolean)
                  const allChecked = ids.length > 0 && ids.every((id) => selectedServerIds.includes(id))
                  const someChecked = ids.some((id) => selectedServerIds.includes(id))
                  const groupIsSelected = selectedGroups.includes(group)
                  // 选中分组时自动展开（仅在被选中时为 true），便于用户看到联动高亮
                  const expanded = groupIsSelected || expandedGroups[group] !== false
                  return (
                    <Fragment key={group}>
                      <tr className={`group-header${groupIsSelected ? ' group-header--selected' : ''}`}>
                        <td colSpan={5}>
                          <label>
                            <input
                              type="checkbox"
                              checked={allChecked}
                              ref={(el) => { if (el) el.indeterminate = !allChecked && someChecked }}
                              onChange={(e) => setSelectedServerIds(e.target.checked ? Array.from(new Set([...selectedServerIds, ...ids])) : selectedServerIds.filter((x) => !ids.includes(x)))}
                            />
                            <strong>{group}</strong>
                            <span className="group-meta">在线 {ids.length} / 全部 {(list as any[]).length} · 已选 {ids.filter((id) => selectedServerIds.includes(id)).length}</span>
                            {groupIsSelected && <span className="group-meta group-meta--selected">已加入巡检批次</span>}
                          </label>
                          <button className="group-toggle" onClick={() => setExpandedGroups({ ...expandedGroups, [group]: !expanded })}>{expanded ? '收起 ▾' : '展开 ▸'}</button>
                        </td>
                      </tr>
                      {expanded && (list as any[]).map((s) => {
                        const id = s.id || s.name
                        const inspectable = isServerInspectable(s)
                        const status = normalizeServerStatus(s)
                        const inSelectedGroup = selectedGroups.length > 0 && selectedGroups.includes(group)
                        const rowClass = [
                          inspectable ? '' : 'row-disabled',
                          inSelectedGroup ? 'is-in-selected-group' : '',
                          inspectable && selectedServerIds.includes(id) ? 'is-selected' : '',
                        ].filter(Boolean).join(' ')
                        return (
                          <tr key={id} className={rowClass}>
                            <td><input type="checkbox" disabled={!inspectable} checked={inspectable && selectedServerIds.includes(id)} onChange={(e) => setSelectedServerIds(e.target.checked ? Array.from(new Set([...selectedServerIds, id])) : selectedServerIds.filter((x) => x !== id))} /></td>
                            <td>
                              <div className="server-name">
                                {s.name || id}
                                {s.description && <small>{s.description}</small>}
                              </div>
                            </td>
                            <td><span className="server-host">{s.host || s.ip || '-'}</span></td>
                            <td><span className={`server-status server-status--${status}`}>{serverStatusText(s)}</span></td>
                            <td>
                              <small style={{ color: inSelectedGroup ? 'var(--primary, #2563eb)' : 'var(--text-muted)', fontWeight: inSelectedGroup ? 600 : 400 }}>{group}</small>
                            </td>
                          </tr>
                        )
                      })}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>

          <ServerCategoryChips
            items={categories.server || []}
            itemConfigs={itemConfigs.server || []}
            selected={serverCats}
            onChange={setServerCats}
            onConfigEdit={openItemConfigEditor}
          />

          <ServerAdvancedSettings
            batchConcurrency={batchConcurrency}
            batchSize={batchSize}
            commandTimeoutSeconds={commandTimeoutSeconds}
            runTimeoutSeconds={runTimeoutSeconds}
            onBatchConcurrencyChange={setBatchConcurrency}
            onBatchSizeChange={setBatchSize}
            onCommandTimeoutSecondsChange={setCommandTimeoutSeconds}
            onRunTimeoutSecondsChange={setRunTimeoutSeconds}
          />
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
          <CategoryOptions items={categories.project || []} selected={projectCats} onChange={setProjectCats} />
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
            <div><CategoryOptions items={categories.server || []} selected={serverCats} onChange={setServerCats} itemConfigs={itemConfigs.server} onConfigEdit={openItemConfigEditor} /></div>
            <div><CategoryOptions items={categories.project || []} selected={projectCats} onChange={setProjectCats} /></div>
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
          {runs.length === 0 ? <EmptyState title="暂无巡检记录" /> : <><div className="table-scroll"><table className="data-table"><thead><tr><th><input type="checkbox" checked={runs.length > 0 && runs.every((r: any) => selectedRunIds.includes(r.id))} onChange={(e) => setSelectedRunIds(e.target.checked ? runs.map((r: any) => r.id) : [])} /></th><th>时间</th><th>范围</th><th>对象</th><th>评分</th><th>风险</th><th>状态</th><th>操作</th></tr></thead><tbody>{runs.map((r: any) => <tr key={r.id}><td><input type="checkbox" checked={selectedRunIds.includes(r.id)} onChange={(e) => toggleRunSelection(r.id, e.target.checked)} /></td><td>{formatTime(r.created_at)}</td><td>{r.scope_type}</td><td>{r.project_id || r.server_id || '-'}</td><td><span className={`status-badge status-badge--${scoreTone(r.score)}`}>{r.score}</span></td><td>{r.high_count}/{r.medium_count}/{r.low_count}</td><td><StatusBadge value={r.status} /></td><td><button className="btn btn-subtle" onClick={() => inspection.runDetail(r.id).then((res: any) => { setCurrentResult(res.data); const st = res.data?.run?.status; if (st === 'RUNNING' || st === 'PENDING') setActiveRunIds((prev) => Array.from(new Set([...prev, r.id]))); })}>详情</button><button className="btn btn-subtle" onClick={() => openRunDetailRaw(r.id)}>原始数据</button>{r.report_id && <a className="btn btn-subtle" href={`/api/v2/reports/${r.report_id}/view?as=html`} target="_blank" rel="noreferrer">查看报告</a>}{r.report_id && <a className="btn btn-subtle" href={`/api/v2/reports/${r.report_id}/download?as=md`} target="_blank" rel="noreferrer" download>下载</a>}<button className="btn btn-danger" onClick={() => deleteInspectionRuns([r.id])}>删除</button></td></tr>)}</tbody></table></div><PaginationControls total={runsTotal} limit={runPageSize} offset={runOffset} onChange={setRunOffset} onPageSizeChange={setRunPageSize} /></>}
          {currentResult && <div className="mini-card" style={{ marginTop: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <strong>当前详情：{currentResult.run?.summary || currentResult.summary || '-'}</strong>
              <span><StatusBadge value={currentResult.run?.status || '-'} /></span>
            </div>
            <div style={{ marginTop: 8, display: 'grid', gap: 8 }}>
              {(currentResult.run?.status === 'RUNNING' || currentResult.run?.status === 'PENDING') && <div className="alert alert-info">巡检执行中，页面每 2 秒自动刷新执行过程。已完成 {(currentResult.items || []).length} 项。</div>}
              {currentResult.ledger && <div className="stats-grid"><div className="stat-card"><span>巡检项</span><strong>{currentResult.ledger.item_count}</strong><small>已入台账</small></div><div className="stat-card"><span>未闭环</span><strong>{currentResult.ledger.open_issue_count}</strong><small>待处理/处理中</small></div><div className="stat-card"><span>已闭环</span><strong>{currentResult.ledger.closed_issue_count}</strong><small>已修复/验证/忽略</small></div><div className="stat-card"><span>执行耗时</span><strong>{currentResult.ledger.duration_text}</strong><small>{currentResult.ledger.trigger_type || '-'}</small></div></div>}
              {(currentResult.category_summary || []).length > 0 && <div className="table-scroll"><table className="data-table"><thead><tr><th>分类</th><th>总数</th><th>通过</th><th>风险</th><th>错误</th><th>高/中/低</th></tr></thead><tbody>{(currentResult.category_summary || []).map((c: any) => <tr key={c.category}><td>{c.category}</td><td>{c.total}</td><td>{c.pass}</td><td>{c.risk}</td><td>{c.error}</td><td>{c.high}/{c.medium}/{c.low}</td></tr>)}</tbody></table></div>}
              <div className="table-scroll"><table className="data-table"><thead><tr><th>分类</th><th>巡检项</th><th>等级</th><th>结果</th><th>可验证 Shell 命令</th><th>建议</th></tr></thead><tbody>{(currentResult.items || []).length === 0 ? <tr><td colSpan={6}>暂无巡检项结果；如果状态为 RUNNING，请等待执行输出。</td></tr> : (currentResult.items || []).map((i: any) => <tr key={i.id}><td>{i.category}</td><td>{i.item_name}</td><td><RiskBadge level={i.risk_level} label={riskLabel(i.risk_level)} /></td><td>{i.message}{i.parsed_facts?.summary ? <><br /><small className="muted" style={{ color: 'var(--accent, #3182ce)' }}>📊 {i.parsed_facts.summary}</small></> : ''}</td><td><pre className="inspection-command-cell">{extractShellCommand(i.raw_output, i.command) || '-'}</pre></td><td>{i.suggestion}</td></tr>)}</tbody></table></div>
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
          <div className="inspection-ledger-header">
            <div>
              <h2>巡检台账与报表</h2>
              <p className="muted">巡检报表作为主内容展示，台账统计、周期筛选和口径说明保留在辅助区，便于审阅与归档。</p>
            </div>
            <button className="btn primary" onClick={() => generatePeriodicInspectionReport(ledgerPeriod)}>生成{ledgerPeriod === 'daily' ? '每日台账' : ledgerPeriod === 'weekly' ? '每周报表' : '月度报告'}</button>
          </div>
          <div className="inspection-ledger-layout">
            <div className="inspection-report-primary">
              <div className="section-toolbar">
                <div>
                  <strong>巡检报表</strong>
                  <p className="muted">生成单次、合并或周期巡检报告后在这里分页展示和归档。</p>
                </div>
                <div className="section-actions">
                  <button className="btn btn-subtle" onClick={() => reloadInspectionReports()}>刷新报表</button>
                  <button className="btn btn-danger" disabled={selectedReportIds.length === 0} onClick={() => deleteInspectionReports(selectedReportIds)}>删除选中报表（{selectedReportIds.length}）</button>
                </div>
              </div>
              {inspectionReports.length === 0 ? <EmptyState title="暂无巡检报表" description="生成单次/合并/周期巡检报告后会在这里分页展示。" /> : <><div className="table-scroll" style={{ marginTop: 8 }}><table className="data-table"><thead><tr><th><input type="checkbox" checked={inspectionReports.length > 0 && inspectionReports.every((r: any) => selectedReportIds.includes(r.id))} onChange={(e) => setSelectedReportIds(e.target.checked ? inspectionReports.map((r: any) => r.id) : [])} /></th><th>生成时间</th><th>标题</th><th>对象</th><th>格式</th><th>大小</th><th>状态</th><th>操作</th></tr></thead><tbody>{inspectionReports.map((r: any) => <tr key={r.id}><td><input type="checkbox" checked={selectedReportIds.includes(r.id)} onChange={(e) => toggleReportSelection(r.id, e.target.checked)} /></td><td>{formatTime(r.created_at || r.generated_at)}</td><td>{r.title || r.id}</td><td>{r.target_id || '-'}</td><td>{r.format || '-'}</td><td>{r.size_bytes ? `${Math.ceil(Number(r.size_bytes) / 1024)} KB` : '-'}</td><td><StatusBadge value={r.status || 'ready'} /></td><td><button className="btn btn-danger" onClick={() => deleteInspectionReports([r.id])}>删除</button></td></tr>)}</tbody></table></div><PaginationControls total={inspectionReportsTotal} limit={reportPageSize} offset={reportOffset} onChange={setReportOffset} onPageSizeChange={setReportPageSize} /></>}
            </div>

            <aside className="inspection-ledger-aside">
              <div className="mini-card">
                <div className="section-toolbar compact">
                  <strong>台账筛选</strong>
                  <button className="btn btn-subtle" onClick={() => reloadLedger()}>刷新</button>
                </div>
                <label>周期 <select value={ledgerPeriod} onChange={(e) => { setLedgerPeriod(e.target.value); setLedgerOffset(0); setSelectedLedgerRunIds([]); reloadLedger(e.target.value, 0) }}><option value="daily">每日台账</option><option value="weekly">每周报表</option><option value="monthly">月度报告</option></select></label>
                <div className="ledger-metric-grid">
                  <span>记录 <strong>{ledgerData.summary?.run_count || 0}</strong></span>
                  <span>评分 <strong>{ledgerData.summary?.avg_score || 0}</strong></span>
                  <span>高/中/低 <strong>{ledgerData.summary?.high_count || 0}/{ledgerData.summary?.medium_count || 0}/{ledgerData.summary?.low_count || 0}</strong></span>
                  <span>未闭环 <strong>{ledgerData.summary?.open_issue_count || 0}</strong></span>
                </div>
                <div className="section-actions">
                  <button className="btn btn-danger" disabled={selectedLedgerRunIds.length === 0} onClick={() => deleteInspectionRuns(selectedLedgerRunIds)}>删除选中台账（{selectedLedgerRunIds.length}）</button>
                  <button className="btn btn-danger" disabled={Number(ledgerData.total || 0) === 0} onClick={deleteLedgerHistoryByPeriod}>删除当前周期历史</button>
                </div>
              </div>
              <div className="mini-card">
                <strong>台账记录</strong>
                {(ledgerData.items || []).length === 0 ? <EmptyState title="暂无台账记录" description="执行巡检后会自动沉淀到台账中。" /> : <><div className="table-scroll" style={{ marginTop: 8 }}><table className="data-table data-table--compact"><thead><tr><th><input type="checkbox" checked={(ledgerData.items || []).length > 0 && (ledgerData.items || []).every((row: any) => selectedLedgerRunIds.includes(row.run_id))} onChange={(e) => setSelectedLedgerRunIds(e.target.checked ? (ledgerData.items || []).map((row: any) => row.run_id) : [])} /></th><th>时间</th><th>对象</th><th>评分</th><th>风险</th><th>操作</th></tr></thead><tbody>{(ledgerData.items || []).map((row: any) => <tr key={row.run_id}><td><input type="checkbox" checked={selectedLedgerRunIds.includes(row.run_id)} onChange={(e) => toggleLedgerRunSelection(row.run_id, e.target.checked)} /></td><td>{formatTime(row.inspection_time)}</td><td>{row.target}</td><td><span className={`status-badge status-badge--${scoreTone(row.score)}`}>{row.score}</span></td><td>{row.high_count}/{row.medium_count}/{row.low_count}</td><td><button className="btn btn-subtle" onClick={() => inspection.runDetail(row.run_id).then((res: any) => { setCurrentResult(res.data); setTab('runs') })}>详情</button></td></tr>)}</tbody></table></div><PaginationControls total={Number(ledgerData.total || 0)} limit={ledgerPageSize} offset={ledgerOffset} onChange={setLedgerOffset} onPageSizeChange={setLedgerPageSize} /></>}
              </div>
              <div className="mini-card">
                <strong>报表口径</strong>
                <ul className="muted" style={{ margin: '8px 0 0', paddingLeft: 18 }}>
                  <li>每日台账：登录异常、命令高危操作、接口异常、服务状态、备份任务执行异常，无风险也保留正常记录。</li>
                  <li>每周报表：汇总程序文件、配置文件、白名单、端口、权限、备份文件检查结果和整改进度。</li>
                  <li>月度报告：复盘当月安全风险、入侵尝试、漏洞问题、整改情况，并输出规则优化建议。</li>
                </ul>
              </div>
            </aside>
          </div>
        </section>
      )}

      {tab === 'issues' && (
        <section className="panel-card">
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <h2>风险问题</h2>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <label>状态 <select value={issueFilter} onChange={(e) => { setIssueFilter(e.target.value); setIssueOffset(0) }}><option value="">全部</option><option value="OPEN">待处理</option><option value="PROCESSING">处理中</option><option value="FIXED">已修复</option><option value="VERIFIED">已验证</option><option value="IGNORED">已忽略</option></select></label>
              <button className="btn btn-danger" disabled={selectedIssueIds.length === 0} onClick={() => deleteIssues(selectedIssueIds)}>删除选中（{selectedIssueIds.length}）</button>
              <button className="btn btn-subtle" disabled={selectedIssueIds.length === 0} onClick={() => setSelectedIssueIds([])}>清空选择</button>
            </div>
          </div>
          {issues.length === 0 ? <EmptyState title="暂无风险问题" description="巡检发现的高/中/低风险会进入这里进行闭环。" /> : <><div className="table-scroll"><table className="data-table"><thead><tr><th><input type="checkbox" checked={issues.length > 0 && issues.every((i) => selectedIssueIds.includes(i.id))} onChange={(e) => setSelectedIssueIds(e.target.checked ? issues.map((i) => i.id) : [])} /></th><th>等级</th><th>标题</th><th>对象</th><th>描述</th><th>状态</th><th>操作</th></tr></thead><tbody>{issues.map((i) => <tr key={i.id}><td><input type="checkbox" checked={selectedIssueIds.includes(i.id)} onChange={(e) => setSelectedIssueIds((prev) => e.target.checked ? Array.from(new Set([...prev, i.id])) : prev.filter((x) => x !== i.id))} /></td><td><RiskBadge level={i.risk_level} label={riskLabel(i.risk_level)} /></td><td>{i.title}</td><td>{i.project_id || i.server_id || '-'}</td><td>{i.description}</td><td><StatusBadge value={i.status} /></td><td><button className="btn btn-subtle" onClick={() => updateIssue(i.id, 'PROCESSING')}>处理中</button><button className="btn btn-subtle" onClick={() => updateIssue(i.id, 'FIXED')}>已修复</button><button className="btn btn-subtle" onClick={() => updateIssue(i.id, 'VERIFIED')}>已验证</button><button className="btn btn-subtle" onClick={() => updateIssue(i.id, 'IGNORED')}>忽略</button><button className="btn btn-danger" onClick={() => deleteIssue(i.id)}>删除</button></td></tr>)}</tbody></table></div><PaginationControls total={issuesTotal} limit={issuePageSize} offset={issueOffset} onChange={setIssueOffset} onPageSizeChange={(next) => { setIssuePageSize(next); setIssueOffset(0) }} /></>}
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
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
            <label style={{ fontSize: 13 }}>关键词 <input value={ruleFilter} onChange={(e) => { setRuleFilter(e.target.value); setRuleOffset(0) }} placeholder="搜索规则名称/编码" style={{ width: 160, padding: '4px 8px' }} /></label>
            <label style={{ fontSize: 13 }}>范围 <select value={ruleScopeFilter} onChange={(e) => { setRuleScopeFilter(e.target.value); setRuleOffset(0) }} style={{ padding: '4px 8px' }}><option value="">全部</option><option value="SERVER">SERVER</option><option value="PROJECT">PROJECT</option><option value="BOTH">BOTH</option></select></label>
            <label style={{ fontSize: 13 }}>风险 <select value={ruleRiskFilter} onChange={(e) => { setRuleRiskFilter(e.target.value); setRuleOffset(0) }} style={{ padding: '4px 8px' }}><option value="">全部</option><option value="HIGH">高危</option><option value="MEDIUM">中危</option><option value="LOW">低危</option><option value="NONE">无风险</option></select></label>
            <button className="btn btn-subtle" onClick={() => { setRuleFilter(''); setRuleScopeFilter(''); setRuleRiskFilter(''); setRuleOffset(0) }}>重置</button>
          </div>
          {rules.length === 0 ? <EmptyState title="暂无规则" /> : (
            <>
            <div className="table-scroll">
              <table className="data-table inspection-rule-table">
                <colgroup>
                  <col style={{ width: 220, minWidth: 220 }} />
                  <col style={{ width: 110 }} />
                  <col style={{ width: 110 }} />
                  <col style={{ width: 80 }} />
                  <col style={{ width: 90 }} />
                  <col style={{ minWidth: 360 }} />
                  <col style={{ minWidth: 220 }} />
                  <col style={{ width: 220, minWidth: 220 }} />
                </colgroup>
                <thead>
                  <tr>
                    <th className="col-sticky-left">规则</th>
                    <th>范围</th>
                    <th>分类</th>
                    <th>等级</th>
                    <th>状态</th>
                    <th>内容/说明</th>
                    <th>建议</th>
                    <th className="col-sticky-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {rules.map((r: any) => {
                    const cfg = r.config && typeof r.config === 'object' ? r.config : {}
                    const hasExtractor = !!cfg.extractor
                    const hasThreshold = !!cfg.threshold
                    const th = cfg.threshold || {}
                    return (
                    <tr key={r.id || r.rule_code}>
                      <td className="col-sticky-left">
                        {r.rule_name || r.rule_code}
                        <br /><small className="muted">{r.rule_code}</small>
                        {r.builtin && <><br /><small className="muted">内置</small></>}
                      </td>
                      <td>{r.scope_type}</td>
                      <td>{r.category}</td>
                      <td>
                        <RiskBadge level={r.risk_level} label={riskLabel(r.risk_level)} />
                        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 4 }}>
                          {hasExtractor && <span className="group-chip group-chip--active" style={{ fontSize: 10, padding: '1px 6px' }} title={`提取器: ${cfg.extractor?.mode} ${cfg.extractor?.pattern || cfg.extractor?.value_field || ''}`}>📊 提取</span>}
                          {hasThreshold && <span className="group-chip" style={{ fontSize: 10, padding: '1px 6px', background: '#fef3c7' }} title={`阈值: ${th.comparator || '>'} H=${th.high ?? '-'} M=${th.medium ?? '-'} L=${th.low ?? '-'}`}>🎯 阈值</span>}
                        </div>
                      </td>
                      <td><StatusBadge value={r.enabled === false ? 'DISABLED' : 'ENABLED'} /></td>
                      <td><div style={{ maxWidth: 360 }}><strong>{r.rule_content || '-'}</strong><br /><small className="muted">{r.description}</small></div></td>
                      <td>{r.suggestion || '-'}</td>
                      <td className="col-sticky-right">
                        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                          <button className="btn btn-subtle" onClick={() => editRule(r)}>编辑</button>
                          <button className="btn btn-subtle" onClick={() => toggleRule(r)}>{r.enabled === false ? '启用' : '停用'}</button>
                          <button className="btn btn-subtle" onClick={() => deleteRule(r)}>删除</button>
                        </div>
                      </td>
                    </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <PaginationControls total={rulesTotal} limit={rulePageSize} offset={ruleOffset} onChange={setRuleOffset} onPageSizeChange={setRulePageSize} />
            </>
          )}
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

                {/* 三段式布局：命令 / 提取器 / 阈值，每段独立卡片、配色分明 */}
                <div className="inspection-rule-stack">
                  {/* Section A: 命令内容 - 灰 */}
                  <section className="rule-section rule-section--cmd">
                    <header className="rule-section__head">
                      <span className="rule-section__icon" aria-hidden>💻</span>
                      <h3>规则内容 / 可执行命令</h3>
                    </header>
                    <label className="rule-section__body">
                      <textarea rows={6} value={ruleEditor.form.rule_content || ''} onChange={(e) => updateRuleForm({ rule_content: e.target.value })} placeholder={'# 示例：只读巡检命令\nss -ntulp\ndf -h\n# 判定要点：高危端口外网暴露、磁盘超过 85%'} />
                    </label>
                  </section>

                  {/* Section B: 提取器 - 蓝 */}
                  <section className="rule-section rule-section--extractor">
                    <header className="rule-section__head">
                      <span className="rule-section__icon" aria-hidden>📊</span>
                      <h3>提取器 <small>· 从命令输出中提取一个数值</small></h3>
                    </header>
                    <div className="rule-section__body rule-grid rule-grid--2col">
                      <label>提取模式
                        <select value={ruleEditor.form.extractor_mode || 'regex'} onChange={(e) => updateRuleForm({ extractor_mode: e.target.value })}>
                          <option value="regex">正则 regex</option>
                          <option value="json">JSONPath</option>
                          <option value="numeric">首段数字</option>
                          <option value="keyword">关键字计数</option>
                        </select>
                      </label>
                      <label>模式 / 表达式
                        <input value={ruleEditor.form.extractor_pattern || ''} onChange={(e) => updateRuleForm({ extractor_pattern: e.target.value })} placeholder={ruleEditor.form.extractor_mode === 'regex' ? 'MemAvailable:\\s+(\\d+)' : '$..memory.used'} />
                      </label>
                      <label>JSON 取值字段
                        <input value={ruleEditor.form.extractor_value_field || 'value'} onChange={(e) => updateRuleForm({ extractor_value_field: e.target.value })} placeholder="value" />
                      </label>
                      <label>关键字列表 <small>· 逗号分隔</small>
                        <input value={ruleEditor.form.extractor_keywords || ''} onChange={(e) => updateRuleForm({ extractor_keywords: e.target.value })} placeholder="kdevtmpfsi, kinsing, xmrig" />
                      </label>
                    </div>
                  </section>

                  {/* Section C: 阈值 - 橙 */}
                  <section className={`rule-section rule-section--threshold ${ruleEditor.form.threshold_enabled ? 'is-active' : ''}`}>
                    <header className="rule-section__head">
                      <span className="rule-section__icon" aria-hidden>🎯</span>
                      <h3>阈值判定 <small>· 启用后根据提取值自动判定风险等级</small></h3>
                      <label className="rule-section__switch" onClick={(e) => e.stopPropagation()}>
                        <input type="checkbox" checked={!!ruleEditor.form.threshold_enabled} onChange={(e) => updateRuleForm({ threshold_enabled: e.target.checked })} />
                        <span>{ruleEditor.form.threshold_enabled ? '已启用' : '未启用'}</span>
                      </label>
                    </header>
                    <div className="rule-section__body rule-grid rule-grid--threshold">
                      <label className="rule-cell rule-cell--high">
                        <span className="rule-cell__label">HIGH ≥</span>
                        <input type="number" value={ruleEditor.form.threshold_high ?? ''} onChange={(e) => updateRuleForm({ threshold_high: e.target.value })} placeholder="90" />
                      </label>
                      <label className="rule-cell rule-cell--medium">
                        <span className="rule-cell__label">MEDIUM ≥</span>
                        <input type="number" value={ruleEditor.form.threshold_medium ?? ''} onChange={(e) => updateRuleForm({ threshold_medium: e.target.value })} placeholder="75" />
                      </label>
                      <label className="rule-cell rule-cell--low">
                        <span className="rule-cell__label">LOW ≥</span>
                        <input type="number" value={ruleEditor.form.threshold_low ?? ''} onChange={(e) => updateRuleForm({ threshold_low: e.target.value })} placeholder="50" />
                      </label>
                      <label>比较符
                        <select value={ruleEditor.form.threshold_comparator || '>'} onChange={(e) => updateRuleForm({ threshold_comparator: e.target.value })}>
                          <option value=">">{'>'} 大于</option>
                          <option value=">=">{'>='} 大于等于</option>
                          <option value="<">{'<'} 小于</option>
                          <option value="<=">{'<='} 小于等于</option>
                          <option value="==">== 等于</option>
                          <option value="contains">包含</option>
                        </select>
                      </label>
                      <label>单位
                        <select value={ruleEditor.form.threshold_unit || 'count'} onChange={(e) => updateRuleForm({ threshold_unit: e.target.value })}>
                          <option value="count">个</option>
                          <option value="%">百分比</option>
                          <option value="byte">字节</option>
                          <option value="ms">毫秒</option>
                        </select>
                      </label>
                    </div>
                  </section>

                  {/* Section D: 说明/建议 + 高级 - 紫（默认折叠）*/}
                  <section className="rule-section rule-section--notes">
                    <header className="rule-section__head">
                      <span className="rule-section__icon" aria-hidden>📝</span>
                      <h3>规则说明 & 整改建议</h3>
                    </header>
                    <div className="rule-section__body rule-grid rule-grid--2col">
                      <label>规则说明
                        <textarea rows={3} value={ruleEditor.form.description || ''} onChange={(e) => updateRuleForm({ description: e.target.value })} placeholder="该规则检测什么、为什么重要、判定逻辑" />
                      </label>
                      <label>整改建议
                        <textarea rows={3} value={ruleEditor.form.suggestion || ''} onChange={(e) => updateRuleForm({ suggestion: e.target.value })} placeholder="命中风险后推荐的处理步骤" />
                      </label>
                    </div>
                    <div className="rule-section__tip">
                      项目规则可使用 <code>&lt;deploy_path&gt;</code>、<code>&lt;config_path&gt;</code>、<code>&lt;log_path&gt;</code>、<code>&lt;backup_path&gt;</code>、<code>&lt;main_port&gt;</code>、<code>&lt;runtime_user&gt;</code>，执行时由项目部署信息替换。
                    </div>
                  </section>

                  <details className="rule-advanced">
                    <summary>⚙️ 高级：编辑原始 JSON 配置</summary>
                    <label>规则配置 JSON
                      <textarea rows={5} value={ruleEditor.form.config_text || '{}'} onChange={(e) => updateRuleForm({ config_text: e.target.value })} placeholder='{ "commands": ["ss -ntulp"] }' />
                    </label>
                  </details>
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

      <RiskConfirmDialog
        open={profileConfirmOpen}
        title={profileRunMode === 'issue_retry' ? '确认复巡未关闭风险' : '确认执行巡检方案'}
        description={profileRunMode === 'issue_retry' ? '仅复巡当前未关闭风险问题关联的服务器。请核对目标数量和巡检项，无需手动输入字符串。' : '请核对目标数量和巡检项，确认后将自动使用本次预览生成的确认短语。'}
        target={profilePreviewData?.profile?.name || profilePreviewData?.profile_id || '-'}
        confirmText={profilePreviewData?.confirmation?.confirm_text || ''}
        value={profileConfirmValue}
        onValueChange={setProfileConfirmValue}
        onCancel={() => { setProfileConfirmOpen(false); setProfileConfirmValue('') }}
        onConfirm={runInspectionProfile}
        riskLevel="high"
        details={[
          { label: '执行模式', value: profileRunMode === 'issue_retry' ? '复巡未关闭风险' : '巡检方案' },
          { label: '目标服务器', value: `${profilePreviewData?.eligible_count || 0} 台` },
          { label: '跳过/过滤', value: `${profilePreviewData?.skipped_count || 0} / ${profilePreviewData?.filtered_count || 0}` },
          { label: '巡检项', value: Array.isArray(profilePreviewData?.categories) ? profilePreviewData.categories.join(', ') : '-' },
          { label: '并发/批量', value: `${profilePreviewData?.concurrency || '-'} / ${profilePreviewData?.batch_size || '-'}` },
        ]}
        confirmButtonLabel={running ? '执行中...' : '确认并执行'}
        confirmDisabled={running || !profilePreviewData?.confirmation?.confirm_text}
        confirmMode="one-click"
        showConfirmTextInOneClick={false}
      />
    </div>
  )
}
