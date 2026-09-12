import { useCallback, useMemo, useRef, useState } from 'react'
import { deploy, deployment, pipelineBinding } from '../../api'
import { EnvironmentOption } from './useDeployFormState'
import { LogEntry } from './useDeploymentPolling'

type UseDeployActionsArgs = {
  system: string
  service: string
  environment: string
  fileName: string
  servers: string
  pipelineId: string
  pipelineSteps: any[]
  serverGroup: string
  environments: EnvironmentOption[]
  selectedService: any
  selectedServerNames: string[]
  parallelism: number
  failFast: boolean
  parseServerList: (value: string) => string[]
  validateEnvironmentServerSelection: (actionName: string) => boolean
  setResolveResult: (value: any) => void
  setReleaseConfirmation: (value: any) => void
  setConfirmingRelease: (value: boolean) => void
  setResolving: (value: boolean) => void
  taskId: string
  deploymentId: string
  logs: LogEntry[]
  status: string
  setTaskId: (value: string) => void
  setDeploymentId: (value: string) => void
  setLogs: (value: LogEntry[]) => void
  setStatus: (value: string) => void
  setTaskDetails: (value: any) => void
  fetchLogs: (taskId: string) => Promise<void>
  startPolling: (taskId: string) => void
  resetRunState: (nextStatus?: string) => void
  setActiveTab: (tab: 'deploy' | 'history') => void
  setDeployments: (items: any[]) => void
  notify: (text: string, type?: 'success' | 'error' | 'info') => void
}

export function useDeployActions(args: UseDeployActionsArgs) {
  const {
    system,
    service,
    environment,
    fileName,
    servers,
    pipelineId,
    pipelineSteps,
    serverGroup,
    environments,
    selectedService,
    selectedServerNames,
    parallelism,
    failFast,
    parseServerList,
    validateEnvironmentServerSelection,
    setResolveResult,
    setReleaseConfirmation,
    setConfirmingRelease,
    setResolving,
    taskId,
    deploymentId,
    logs,
    status,
    setTaskId,
    setDeploymentId,
    setLogs,
    setStatus,
    setTaskDetails,
    fetchLogs,
    startPolling,
    resetRunState,
    setActiveTab,
    setDeployments,
    notify,
  } = args

  const [loading, setLoading] = useState(false)
  const loadingRef = useRef(false)
  loadingRef.current = loading
  const [precheckResult, setPrecheckResult] = useState<any>(null)
  const [prechecking, setPrechecking] = useState(false)
  const [rollbackPlanData, setRollbackPlanData] = useState<any>(null)
  const [rollbackConfirmText, setRollbackConfirmText] = useState('')
  const [rollbackSubmitting, setRollbackSubmitting] = useState(false)
  const [releaseRiskDialogOpen, setReleaseRiskDialogOpen] = useState(false)
  const [releaseConfirmText, setReleaseConfirmText] = useState('')
  const [releaseClauseText, setReleaseClauseText] = useState('')
  const [releaseReason, setReleaseReason] = useState('')
  const [pendingReleaseConfirmation, setPendingReleaseConfirmation] = useState<any>(null)
  const [cancelRiskDialogOpen, setCancelRiskDialogOpen] = useState(false)

  const buildRuntimeVariables = useCallback(() => {
    const selectedEnv = environments.find((e) => e.name === environment || e.id === environment)
    const serviceVars = selectedService?.template_variables || {}
    const isDockerComposeService = (
      selectedService?.template === 'docker_compose' ||
      selectedService?.template === 'crypto_docker_compose' ||
      Boolean(serviceVars.compose_dir) ||
      (Array.isArray(pipelineSteps) && pipelineSteps.some((s: any) => {
        const t = s?.step_type || s?.type
        return t === 'docker_compose_update' || t === 'docker_compose'
      }))
    )
    const deployPath = isDockerComposeService
      ? (serviceVars.compose_dir || serviceVars.deploy_path || serviceVars.base_path || selectedEnv?.deploy_path || `/data/web/${system}`)
      : (serviceVars.deploy_path || serviceVars.base_path || serviceVars.service_dir || selectedEnv?.deploy_path || `/data/web/${system}`)
    const healthUrl = serviceVars.health_url || selectedEnv?.health_url || selectedEnv?.variables?.health_url || 'http://localhost/health'
    const releaseService = selectedService?.name || service
    return {
      ...serviceVars,
      compose_dir: serviceVars.compose_dir || deployPath,
      compose_file: serviceVars.compose_file || 'docker-compose.yml',
      deploy_path: deployPath,
      health_url: healthUrl,
      restart_command: isDockerComposeService
        ? (serviceVars.restart_command || `docker compose -f ${serviceVars.compose_file || 'docker-compose.yml'} up -d --remove-orphans`)
        : (serviceVars.restart_command || `pm2 restart ${releaseService || system || 'all'}`),
      build_command: serviceVars.build_command || '',
      repo: serviceVars.repo,
      release_service: releaseService,
      service: releaseService,
      server_group: serverGroup || '',
      environment,
      target_servers: parseServerList(servers),
      file_name: fileName,
      deployment_kind: isDockerComposeService ? 'docker_compose' : 'traditional',
    }
  }, [selectedService, environments, environment, system, service, serverGroup, servers, fileName, parseServerList, pipelineSteps])

  const buildDeployPayload = useCallback(() => ({
    system,
    service,
    environment,
    version: fileName,
    servers: parseServerList(servers),
    file_name: fileName,
    pipeline_id: pipelineId,
    server_group: serverGroup,
    variables: buildRuntimeVariables(),
    parallelism,
    fail_fast: failFast,
    steps: pipelineSteps.length > 0 ? pipelineSteps.map((s: any) => ({
      type: s.step_type || s.type || 'command',
      name: s.name || '',
      config: s.config || {},
    })) : undefined,
  }), [system, service, environment, fileName, servers, pipelineId, serverGroup, pipelineSteps, parseServerList, buildRuntimeVariables, parallelism, failFast])

  const loadReleaseConfirmation = useCallback(async () => {
    try {
      const res: any = await deploy.confirmation(buildDeployPayload())
      const data = res.data || res
      setReleaseConfirmation(data)
      return data
    } catch (e: any) {
      notify(typeof e === 'string' ? e : e?.message || '确认信息加载失败', 'error')
      throw e
    }
  }, [buildDeployPayload, setReleaseConfirmation, notify])

  const handleResolve = useCallback(async () => {
    if (!validateEnvironmentServerSelection('解析预览')) return
    setResolving(true)
    try {
      const res: any = await pipelineBinding.resolve({
        system,
        environment,
        pipeline_id: pipelineId,
        runtime_overrides: buildRuntimeVariables(),
        steps: pipelineSteps,
      })
      setResolveResult(res.data || res)
    } catch {
      setResolveResult({ errors: [{ message: '解析请求失败' }] })
    }
    setResolving(false)
  }, [system, environment, pipelineId, pipelineSteps, buildRuntimeVariables, setResolveResult, setResolving, validateEnvironmentServerSelection])

  const isProdRelease = useCallback(() => {
    const value = String(environment || '').toLowerCase()
    return ['prod', 'production', 'prd', 'online', 'live', 'release', '线上', '生产', '生产环境'].includes(value)
  }, [environment])

  const doDeploy = useCallback(async (extra?: { confirm_text?: string; reason?: string; confirm_production?: boolean; prod_confirm_text?: string }) => {
    setLoading(true)
    try {
      const res: any = await deploy.execute({ ...buildDeployPayload(), ...(extra || {}) })
      const tid = res.data.task_id
      setTaskId(tid)
      setDeploymentId(res.data.deployment_id || '')
      startPolling(tid)
    } catch (e: any) {
      notify(typeof e === 'string' ? e : e?.message || '部署请求失败', 'error')
    } finally {
      setLoading(false)
    }
  }, [buildDeployPayload, setTaskId, setDeploymentId, startPolling, notify])

  const handleDeploy = useCallback(async () => {
    if (!system) {
      notify('请填写系统', 'error')
      return
    }
    const isDockerComposeService = (
      selectedService?.template === 'docker_compose' ||
      selectedService?.template === 'crypto_docker_compose' ||
      Boolean(selectedService?.template_variables?.compose_dir) ||
      (Array.isArray(pipelineSteps) && pipelineSteps.some((s: any) => {
        const t = s?.step_type || s?.type
        return t === 'docker_compose_update' || t === 'docker_compose'
      }))
    )
    if (!isDockerComposeService && !fileName) {
      notify('请填写文件名', 'error')
      return
    }
    if (!validateEnvironmentServerSelection('开始发布')) return
    setConfirmingRelease(true)
    try {
      const confirmation = await loadReleaseConfirmation()
      if (confirmation.blockers?.length) {
        notify(`发布已阻断：${confirmation.blockers.map((x: string) => `- ${x}`).join(' | ')}`, 'error')
        return
      }
      const precheckRes: any = await deployment.precheck(buildDeployPayload())
      const precheck = precheckRes.data || precheckRes
      setPrecheckResult(precheck)
      if (precheck && precheck.ready === false) {
        const blockers = precheck.blocking_checks || []
        notify(`预检未通过：${blockers.map((x: any) => `- ${x.name || x.key}: ${x.detail || x.message || ''}`).join(' | ') || '存在阻断项'}`, 'error')
        return
      }
      const summary = confirmation.summary || {}
      const requiredConfirmText = confirmation.required_confirmation || confirmation.confirm_text || precheck.required_confirmation || `确认发布 ${summary.system || system}/${summary.service || service || '-'} 到 ${summary.environment || environment || '-'}`
      if (isProdRelease() || confirmation.requires_confirmation || precheck.requires_confirmation) {
        setPendingReleaseConfirmation({ ...confirmation, precheck, required_confirmation: requiredConfirmText })
        setReleaseConfirmText('')
        setReleaseClauseText('')
        setReleaseReason('')
        setReleaseRiskDialogOpen(true)
        return
      }
      setPendingReleaseConfirmation({ ...confirmation, precheck, required_confirmation: requiredConfirmText, risk_level: confirmation.risk_level || 'medium' })
      setReleaseConfirmText('')
      setReleaseClauseText('')
      setReleaseReason('')
      setReleaseRiskDialogOpen(true)
      return
 } catch (e: any) {
      notify(typeof e === 'string' ? e : e?.message || '确认信息加载失败', 'error')
      throw e
    } finally {
      setConfirmingRelease(false)
    }
  }, [system, fileName, selectedService, pipelineSteps, validateEnvironmentServerSelection, setConfirmingRelease, loadReleaseConfirmation, buildDeployPayload, isProdRelease, service, environment, resetRunState, doDeploy, notify])

  const cancelReleaseRiskDialog = useCallback(() => {
    if (loadingRef.current) return
    setReleaseRiskDialogOpen(false)
    setPendingReleaseConfirmation(null)
    setReleaseConfirmText('')
    setReleaseClauseText('')
    setReleaseReason('')
  }, [])

  const confirmRiskRelease = useCallback(() => {
    const required = pendingReleaseConfirmation?.required_confirmation || ''
    // 第 4 层（strict_prod_confirmation）：生产环境不再用"空输入回落正确短语"糊过去，
    // 也不再用 confirm_production 旁路——必须逐字输入短语，并额外输入确认从句。
    const requiresProdConfirm = Boolean(pendingReleaseConfirmation?.requires_prod_confirm)
    const prodClause = String(pendingReleaseConfirmation?.prod_confirm_text || '')
    const typed = releaseConfirmText.trim()
    if (requiresProdConfirm && typed !== required) {
      notify(`请输入完整确认短语：${required}`, 'error')
      return
    }
    if (requiresProdConfirm && prodClause && releaseClauseText.trim() !== prodClause) {
      notify(`请输入生产环境确认从句：${prodClause}`, 'error')
      return
    }
    const effectiveConfirmText = (typed || (requiresProdConfirm ? '' : required)).trim()
    setReleaseRiskDialogOpen(false)
    resetRunState('running')
    doDeploy({
      confirm_text: effectiveConfirmText,
      reason: releaseReason.trim(),
      ...(requiresProdConfirm && prodClause ? { prod_confirm_text: prodClause } : {}),
      ...(requiresProdConfirm ? {} : { confirm_production: isProdRelease() }),
    })
  }, [pendingReleaseConfirmation, releaseConfirmText, releaseClauseText, releaseReason, isProdRelease, resetRunState, doDeploy, notify])

  const handlePrecheck = useCallback(async () => {
    if (!system) { notify('请先选择系统', 'error'); return }
    if (!validateEnvironmentServerSelection('预检')) return
    setPrechecking(true)
    setPrecheckResult(null)
    try {
      const res: any = await deployment.precheck(buildDeployPayload())
      setPrecheckResult(res.data)
    } catch (e: any) {
      setPrecheckResult({ ready: false, status: 'blocked', blocking_checks: [{ name: '接口请求', detail: typeof e === 'string' ? e : e?.message || '预检请求失败' }], checks: [] })
      notify(typeof e === 'string' ? e : e?.message || '预检请求失败', 'error')
    }
    setPrechecking(false)
  }, [system, validateEnvironmentServerSelection, buildDeployPayload, notify])

  const loadDeployments = useCallback(async (filters?: { limit?: number; offset?: number; system?: string; service?: string; environment?: string; status?: string; created_by?: string; q?: string }) => {
    try {
      const params = { limit: 50, ...(filters || {}) }
      const res: any = await deployment.list(params)
      const items = Array.isArray(res.data) ? res.data : (res.data?.items || [])
      setDeployments(items)
      return res.pagination || res.data?.pagination || { limit: params.limit || 50, offset: params.offset || 0, total: items.length, has_more: false }
    } catch {
      setDeployments([])
      return { limit: filters?.limit || 50, offset: filters?.offset || 0, total: 0, has_more: false }
    }
  }, [setDeployments])

  const handleCancelDeployment = useCallback(() => {
    if (!deploymentId) return
    setCancelRiskDialogOpen(true)
  }, [deploymentId])

  const cancelCancelDeployment = useCallback(() => {
    setCancelRiskDialogOpen(false)
  }, [])

  const confirmCancelDeployment = useCallback(async () => {
    if (!deploymentId) return
    try {
      await deployment.cancel(deploymentId)
      setCancelRiskDialogOpen(false)
      setStatus('canceled')
      if (taskId) fetchLogs(taskId).catch(() => {})
    } catch (e: any) {
      notify(typeof e === 'string' ? e : e?.message || '终止发布失败', 'error')
    }
  }, [deploymentId, taskId, fetchLogs, setStatus, notify])

  const handleRollback = useCallback(async (depId: string) => {
    try {
      const planRes: any = await deployment.rollbackPlan(depId)
      const payload = planRes.data || planRes || {}
      setRollbackPlanData(payload)
      setRollbackConfirmText('')
    } catch (e: any) {
      notify(typeof e === 'string' ? e : '回滚计划获取失败', 'error')
    }
  }, [notify])

  const submitRollbackPlan = useCallback(async () => {
    if (!rollbackPlanData?.deployment_id) return
    const precheck = rollbackPlanData.precheck || {}
    const body: any = {}
    if (precheck.requires_confirmation && precheck.confirm_text) {
      if (rollbackConfirmText !== precheck.confirm_text) return
      body.confirm_text = rollbackConfirmText
    }
    setRollbackSubmitting(true)
    try {
      const res: any = await deployment.rollback(rollbackPlanData.deployment_id, body)
      const tid = res.data.task_id
      setTaskId(tid)
      setDeploymentId(res.data.deployment_id || rollbackPlanData.deployment_id)
      setStatus('running')
      setLogs([])
      setTaskDetails(null)
      setActiveTab('deploy')
      setRollbackPlanData(null)
      setRollbackConfirmText('')
      startPolling(tid)
    } catch (e: any) {
      notify(typeof e === 'string' ? e : e?.message || '回滚请求失败', 'error')
    } finally {
      setRollbackSubmitting(false)
    }
  }, [rollbackPlanData, rollbackConfirmText, setTaskId, setDeploymentId, setStatus, setLogs, setTaskDetails, setActiveTab, startPolling, notify])

  const closeRollbackPlan = useCallback(() => {
    if (!rollbackSubmitting) {
      setRollbackPlanData(null)
      setRollbackConfirmText('')
    }
  }, [rollbackSubmitting])

  const statusColor = status === 'success' ? 'var(--success)' : status === 'failed' ? 'var(--danger)' : 'var(--warning)'

  const serverExecutionRows = useMemo(() => {
    const names = new Set<string>()
    selectedServerNames.forEach((name) => names.add(name))
    logs.forEach((log) => {
      const step = log.step_name || ''
      if (step.startsWith('server:')) names.add(step.replace(/^server:/, ''))
      if (step.startsWith('rollback:')) names.add(step.replace(/^rollback:/, ''))
      if (step.startsWith('rollback-health:')) names.add(step.replace(/^rollback-health:/, ''))
      const match = String(log.message || '').match(/(?:开始服务器发布|服务器发布成功|部署异常|服务器发布失败):\s*([^:（(]+)/)
      if (match?.[1]) names.add(match[1].trim())
    })
    return Array.from(names).filter(Boolean).map((name) => {
      const related = logs.filter((l) => (l.step_name || '').includes(name) || String(l.message || '').includes(name))
      const failed = related.some((l) => l.level === 'error' || String(l.message || '').includes('失败'))
      const success = related.some((l) => String(l.message || '').includes('服务器发布成功') || String(l.message || '').includes('Rollback success') || String(l.message || '').includes('Rollback health check passed'))
      const running = related.some((l) => String(l.message || '').includes('开始服务器发布')) && !success && !failed
      return {
        name,
        status: failed ? 'failed' : success ? 'success' : running ? 'running' : 'pending',
        last: related[related.length - 1]?.message || '等待执行',
      }
    })
  }, [logs, selectedServerNames])

  const timelineSteps = useMemo(() => {
    const hasRollbackLogs = logs.some((l) => String(l.step_name || '').startsWith('rollback'))
    const names = hasRollbackLogs
      ? ['回滚执行', '回滚后健康检查']
      : pipelineSteps.length > 0
        ? pipelineSteps.map((s: any) => s.name || s.step_type || s.type || '步骤')
        : ['预检查', '上传发布包', '执行命令', '健康检查']
    return names.map((name: string) => {
      const related = logs.filter((l) => (l.step_name || '').includes(name) || name.includes(l.step_name || '__none__') || (name === '回滚执行' && String(l.step_name || '').startsWith('rollback:')) || (name === '回滚后健康检查' && String(l.step_name || '').startsWith('rollback-health:')))
      const hasError = related.some((l) => l.level === 'error')
      const hasLog = related.length > 0
      let stepStatus = hasError ? 'failed' : hasLog ? 'success' : status === 'running' ? 'running' : 'pending'
      if (status === 'failed' && !hasLog) stepStatus = 'pending'
      return { name, status: stepStatus, detail: related[related.length - 1]?.message || (stepStatus === 'pending' ? '等待执行或无日志' : '已产生执行日志') }
    })
  }, [logs, pipelineSteps, status])

  return {
    loading,
    precheckResult,
    prechecking,
    rollbackPlanData,
    rollbackConfirmText,
    rollbackSubmitting,
    statusColor,
    serverExecutionRows,
    timelineSteps,
    releaseRiskDialogOpen,
    releaseConfirmText,
    releaseClauseText,
    releaseReason,
    pendingReleaseConfirmation,
    cancelRiskDialogOpen,
    setReleaseConfirmText,
    setReleaseClauseText,
    setReleaseReason,
    cancelReleaseRiskDialog,
    confirmRiskRelease,
    setRollbackConfirmText,
    buildRuntimeVariables,
    buildDeployPayload,
    loadReleaseConfirmation,
    handleResolve,
    handleDeploy,
    handlePrecheck,
    loadDeployments,
    handleCancelDeployment,
    cancelCancelDeployment,
    confirmCancelDeployment,
    handleRollback,
    submitRollbackPlan,
    closeRollbackPlan,
  }
}
