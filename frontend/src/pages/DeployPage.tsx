import { useState, useEffect } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { deployment } from '../api'
import { useNotificationStore } from '../store'
import { ROUTES } from '../routes'
import { RiskConfirmDialog, Skeleton, FavoriteButton } from '../components/ui'
import { StepWizard } from '../components/StepWizard'
import DeployTargetStep from './deploy/DeployTargetStep'
import DeployPackageStep from './deploy/DeployPackageStep'
import DeployServerStep from './deploy/DeployServerStep'
import DeployPrecheckStep from './deploy/DeployPrecheckStep'
import DeployConfirmStep from './deploy/DeployConfirmStep'
import DeployRunStep from './deploy/DeployRunStep'
import RetentionPolicyPanel from './deploy/RetentionPolicyPanel'
import ReleasePlanPanel from './deploy/ReleasePlanPanel'
import RollbackPlanDialog from './deploy/RollbackPlanDialog'
import DeploymentHistoryTable from './deploy/DeploymentHistoryTable'
import type { DeploymentRecord, DeploymentReport } from '../types/deploy'
import DeploymentRunPanel from './deploy/DeploymentRunPanel'
import { DeploySummaryCard } from './deploy/DeploySummaryCard'
import { useDeploymentStream } from './deploy/useDeploymentStream'
import { useDeployFormState } from './deploy/useDeployFormState'
import { useDeployOptionsLoader } from './deploy/useDeployOptionsLoader'
import { useDeployServerSelection } from './deploy/useDeployServerSelection'
import { useDeployActions } from './deploy/useDeployActions'
import { useRetentionPolicy } from './deploy/useRetentionPolicy'
import { PipelineStageRail } from '../components/v10/PipelineStage'

const selectStyle: React.CSSProperties = {
  width: '100%',
  padding: '8px 12px',
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-strong)',
  borderRadius: '6px',
  color: 'var(--text-primary)',
  fontSize: '14px',
}

export default function DeployPage() {
  const navigate = useNavigate()

  const notify = useNotificationStore((s) => s.addMessage)

  const {
    systems, setSystems,
    services, setServices,
    environments, setEnvironments,
    pipelines, setPipelines,
    serverGroupList, setServerGroupList,
    serverInventory, setServerInventory,
    deployPackages, setDeployPackages,
    system, setSystem,
    service, setService,
    environment, setEnvironment,
    fileName, setFileName,
    servers, setServers,
    pipelineId, setPipelineId,
    pipelineSteps, setPipelineSteps,
    serverGroup, setServerGroup,
    serverAutoMode, setServerAutoMode,
    parallelism, setParallelism,
    failFast, setFailFast,
    resolveResult, setResolveResult,
    releaseConfirmation, setReleaseConfirmation,
    confirmingRelease, setConfirmingRelease,
    resolving, setResolving,
  } = useDeployFormState()

  const {
    taskId, setTaskId,
    deploymentId, setDeploymentId,
    logs, setLogs,
    status, setStatus,
    taskDetails, setTaskDetails,
    fetchLogs, startPolling, resetRunState,
  } = useDeploymentStream()
  const [deployments, setDeployments] = useState<DeploymentRecord[]>([])
  const [deploymentPagination, setDeploymentPagination] = useState<any>(null)
  const [selectedReport, setSelectedReport] = useState<DeploymentReport | null>(null)
  const [deleteHistoryTargets, setDeleteHistoryTargets] = useState<DeploymentRecord[]>([])
  const [selectedDeploymentIds, setSelectedDeploymentIds] = useState<string[]>([])
  const [deletingHistory, setDeletingHistory] = useState(false)
  const [activeTab, setActiveTab] = useState<'deploy' | 'history'>('deploy')
  const {
    retentionPolicy,
    retentionPreview,
    retentionLoading,
    retentionMessage,
    retentionError,
    loadRetention,
    previewRetention,
    saveRetention,
    cleanupRetention,
    updateRetentionField,
  } = useRetentionPolicy({ system, onDeploymentsChanged: setDeployments })

  const {
    parseServerList,
    isProdEnvironment,
    serverLooksLikeTest,
    selectedService,
    selectedServerNames,
    selectedServerSet,
    candidateServerNames,
    recommendedServerSet,
    selectedServerEnvironmentConflictSet,
    serverMetaByName,
    environmentServerErrorText,
    validateEnvironmentServerSelection,
    resolveServerNamesForTarget,
    autoFillServers,
    selectRecommendedServers,
    selectAllCandidateServers,
    clearSelectedServers,
    invertCandidateServers,
    toggleServerSelection,
    applyServiceDefaults,
    isSelectedDockerCompose,
  } = useDeployServerSelection({
    services,
    environments,
    serverGroupList,
    serverInventory,
    service,
    environment,
    servers,
    serverGroup,
    serverAutoMode,
    pipelineId,
    pipelineSteps,
    pipelines,
    setService,
    setServers,
    setServerAutoMode,
    setPipelineId,
  })

  const { optionsLoading } = useDeployOptionsLoader({
    system,
    service,
    environment,
    pipelineId,
    setSystems,
    setServices,
    setEnvironments,
    setPipelines,
    setServerGroupList,
    setServerInventory,
    setDeployPackages,
    setPipelineSteps,
  })

  const {
    loading,
    precheckResult,
    prechecking,
    rollbackPlanData,
    rollbackConfirmText,
    rollbackSubmitting,
    releaseRiskDialogOpen,
    releaseConfirmText,
    releaseReason,
    pendingReleaseConfirmation,
    cancelRiskDialogOpen,
    setReleaseConfirmText,
    setReleaseReason,
    cancelReleaseRiskDialog,
    confirmRiskRelease,
    statusColor,
    serverExecutionRows,
    timelineSteps,
    setRollbackConfirmText,
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
  } = useDeployActions({
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
  })

  const [wizardStep, setWizardStep] = useState(0)

  const goNext = () => setWizardStep((s) => Math.min(s + 1, 5))
  const goBack = () => setWizardStep((s) => Math.max(s - 1, 0))

  const deployDisabledReasons: string[] = []
  if (!system) deployDisabledReasons.push('请选择系统')
  if (!service) deployDisabledReasons.push('请选择服务')
  if (!environment) deployDisabledReasons.push('请选择环境')
  if (!selectedServerNames.length) deployDisabledReasons.push('请选择至少一台服务器')
  if (!isSelectedDockerCompose && !fileName) deployDisabledReasons.push('请选择发布包')
  if (!precheckResult) deployDisabledReasons.push('请先执行预检')
  if (precheckResult && precheckResult.status === 'blocked') deployDisabledReasons.push('预检未通过')
  if (loading) deployDisabledReasons.push('发布正在进行中')
  const deployButtonDisabled = deployDisabledReasons.length > 0

  useEffect(() => {
    if (activeTab === 'history') {
      loadDeployments().then(setDeploymentPagination)
      if (!retentionPreview) loadRetention()
    }
  }, [activeTab, loadDeployments, loadRetention, retentionPreview])

  useEffect(() => {
    if (environment && serverAutoMode) {
      autoFillServers({ envName: environment, force: true })
    }
  }, [environment, autoFillServers, serverAutoMode])

  const openHistoricalDeployment = async (d: DeploymentRecord, includeReport: boolean) => {
    const [logsRes, reportRes, tasksRes]: any[] = await Promise.all([
      deployment.deploymentLogs(d.id).catch(() => ({ data: { logs: [] } })),
      includeReport ? deployment.report(d.id).catch(() => null) : Promise.resolve(null),
      deployment.tasks(d.id).catch(() => null),
    ])
    const tasksPayload = tasksRes?.data || null
    const primaryTaskId = tasksPayload?.tasks?.[0]?.task_id || ''
    setLogs(logsRes.data?.logs || logsRes.data || [])
    setTaskDetails(tasksPayload)
    setSelectedReport(reportRes ? (reportRes.data || reportRes) : null)
    setTaskId(primaryTaskId)
    setDeploymentId(d.id)
    setStatus(d.status)
    setActiveTab('deploy')
  }

  const openDeleteSelectedDeploymentHistory = () => {
    const selected = deployments.filter((item) => selectedDeploymentIds.includes(item.id))
    if (selected.length) setDeleteHistoryTargets(selected)
  }

  const confirmDeleteDeploymentHistory = async () => {
    if (!deleteHistoryTargets.length) return
    setDeletingHistory(true)
    try {
      const ids = deleteHistoryTargets.map((item) => item.id).filter(Boolean)
      if (ids.length === 1) {
        await deployment.deleteHistory(ids[0], { confirm_text: `DELETE DEPLOYMENT ${ids[0]}` })
      } else {
        await deployment.deleteHistoryBatch({ deployment_ids: ids, confirm_text: `DELETE DEPLOYMENTS ${ids.length}` })
      }
      setDeleteHistoryTargets([])
      setSelectedDeploymentIds((prev) => prev.filter((id) => !ids.includes(id)))
      if (deploymentId && ids.includes(deploymentId)) {
        resetRunState('idle')
        setSelectedReport(null)
      }
      const pagination = await loadDeployments({ offset: deploymentPagination?.offset || 0, limit: deploymentPagination?.limit || 50 })
      setDeploymentPagination(pagination)
      notify('部署历史已删除', 'success')
    } catch (e: any) {
      notify(typeof e === 'string' ? e : e?.message || '删除部署历史失败', 'error')
    } finally {
      setDeletingHistory(false)
    }
  }


  return (
    <div style={{ display: 'grid', gap: '20px' }}>
      <header className="cc-hero">
        <div>
          <span className="cc-hero-eyebrow">Deploy · Release Management</span>
          <h1 className="cc-hero-title">发布管理</h1>
          <p className="cc-hero-desc">创建、执行和追踪应用发布；所有高风险操作都经过预检、变量解析和风险确认。</p>
        </div>
        <div className="cc-hero-stats">
          <div className="cc-hero-stat cc-hero-stat--info">
            <strong>{system || '—'}</strong>
            <span>系统</span>
          </div>
          <div className="cc-hero-stat cc-hero-stat--ok">
            <strong>{selectedServerNames.length}</strong>
            <span>目标服务器</span>
          </div>
          <div className={`cc-hero-stat ${precheckResult?.status === 'blocked' ? 'cc-hero-stat--risk' : precheckResult?.status === 'warning' ? 'cc-hero-stat--warn' : 'cc-hero-stat--ok'}`}>
            <strong>{precheckResult ? (precheckResult.status === 'ok' ? 'OK' : precheckResult.status === 'blocked' ? 'BLOCK' : 'WARN') : '—'}</strong>
            <span>预检</span>
          </div>
        </div>
        <div style={{ position: 'absolute', right: 30, top: 26 }}>
          <FavoriteButton url={ROUTES.deploy} label="发布管理" category="deploy" />
        </div>
      </header>

      <div className="file-tab-bar" role="tablist">
        <button className={activeTab === 'deploy' ? 'is-active' : ''} onClick={() => { setSelectedReport(null); setActiveTab('deploy') }}>新发布</button>
        <button className={activeTab === 'history' ? 'is-active' : ''} onClick={() => { setActiveTab('history'); loadDeployments() }}>部署历史</button>
      </div>

      {optionsLoading && systems.length === 0 && (
        <Skeleton type="card" count={3} />
      )}

      {activeTab === 'deploy' && (
        <>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 260px', gap: '16px', alignItems: 'start' }}>
          <div>
            <PipelineStageRail
              steps={[
                { key: 'target', title: '选择目标' },
                { key: 'package', title: isSelectedDockerCompose ? '发布配置' : '发布包' },
                { key: 'server', title: '服务器' },
                { key: 'precheck', title: '预检' },
                { key: 'confirm', title: '确认' },
                { key: 'run', title: '执行发布' },
              ]}
              activeIndex={wizardStep}
            />
            <StepWizard
            steps={[
              {
                key: 'target', title: '选择目标', description: '系统 / 服务 / 环境',
                content: <DeployTargetStep
                  systems={systems} services={services} environments={environments}
                  system={system} service={service} environment={environment}
                  selectedService={selectedService} serverAutoMode={serverAutoMode}
                  selectStyle={selectStyle} navigate={navigate}
                  setSystem={setSystem} setService={setService} setEnvironment={setEnvironment}
                  setPipelineId={setPipelineId}
                  applyServiceDefaults={applyServiceDefaults}
                  resolveServerNamesForTarget={resolveServerNamesForTarget}
                  autoFillServers={autoFillServers}
                />,
              },
              {
                key: 'package', title: isSelectedDockerCompose ? '发布配置' : '发布包', description: isSelectedDockerCompose ? 'Pipeline / 容器配置' : 'Pipeline / 版本 / 文件名',
                content: <DeployPackageStep
                  pipelines={pipelines} deployPackages={deployPackages}
                  system={system} fileName={fileName} pipelineId={pipelineId}
                  pipelineSteps={pipelineSteps} selectStyle={selectStyle}
                  isDockerCompose={isSelectedDockerCompose}
                  composeDir={selectedService?.template_variables?.compose_dir || ''}
                  composeFile={selectedService?.template_variables?.compose_file || ''}
                  setFileName={setFileName} setPipelineId={setPipelineId}
                />,
              },
              {key: 'server', title: '服务器', description: '选择发布目标服务器',
                content: <DeployServerStep
                  serverGroupList={serverGroupList} servers={servers}
                  selectedServerNames={selectedServerNames} selectedServerSet={selectedServerSet}
                  candidateServerNames={candidateServerNames}
                  recommendedServerSet={recommendedServerSet}
                  selectedServerEnvironmentConflictSet={selectedServerEnvironmentConflictSet}
                  serverMetaByName={serverMetaByName}
                  environmentServerErrorText={environmentServerErrorText}
                  system={system} serverGroup={serverGroup} serverAutoMode={serverAutoMode}
                  parallelism={parallelism} failFast={failFast}
                  selectStyle={selectStyle}
                  setServers={setServers} setServerGroup={setServerGroup}
                  setServerAutoMode={setServerAutoMode}
                  setParallelism={setParallelism} setFailFast={setFailFast}
                  autoFillServers={autoFillServers}
                  selectRecommendedServers={selectRecommendedServers}
                  selectAllCandidateServers={selectAllCandidateServers}
                  clearSelectedServers={clearSelectedServers}
                  invertCandidateServers={invertCandidateServers}
                  toggleServerSelection={toggleServerSelection}
                  serverLooksLikeTest={serverLooksLikeTest}
                  isProdEnvironment={isProdEnvironment}
                />,
              },
              {
                key: 'precheck', title: '预检', description: '预检 + 变量解析',
                content: <DeployPrecheckStep
                  precheckResult={precheckResult} resolveResult={resolveResult}
                  prechecking={prechecking} resolving={resolving}
                  handlePrecheck={handlePrecheck} handleResolve={handleResolve}
                />,
              },
              {
                key: 'confirm', title: '确认', description: '摘要 + 风险确认',
                content: <DeployConfirmStep
                  releaseConfirmation={releaseConfirmation}
                  system={system} service={service} environment={environment}
                  fileName={fileName} selectedServerNames={selectedServerNames}
                  pipelineId={pipelineId} parallelism={parallelism}
                  failFast={failFast} selectedService={selectedService}
                  isDockerCompose={isSelectedDockerCompose}
                  isProdEnvironment={isProdEnvironment}
                />,
              },
              {
                key: 'run', title: '执行发布', description: '发布按钮 + 状态',
                content: <DeployRunStep
                  deployDisabledReasons={deployDisabledReasons}
                  deployButtonDisabled={deployButtonDisabled}
                  loading={loading} confirmingRelease={confirmingRelease}
                  status={status} statusColor={statusColor}
                  handleDeploy={handleDeploy}
                />,
              },
            ]}
            activeStep={wizardStep}
            onStepChange={setWizardStep}
          />
          </div>
          <DeploySummaryCard
            title="已选配置"
            extra={<span className="cc-chip cc-chip--ghost">SUMMARY</span>}
            items={[
              { label: '系统', value: system || '—' },
              { label: '服务', value: service || '—' },
              { label: '环境', value: environment || '—' },
              { label: '部署方式', value: isSelectedDockerCompose ? '🐳 容器 (Compose)' : '传统发布' },
              { label: '发布包', value: isSelectedDockerCompose ? '—（容器无需）' : (fileName || '—') },
              { label: `目标服务器 · ${selectedServerNames.length}`, value: selectedServerNames.length > 0 ? selectedServerNames.join(', ') : '—' },
              { label: 'Pipeline', value: pipelineId || '默认' },
              { label: '并行度 / 快速失败', value: `${parallelism} / ${failFast ? '是' : '否'}` },
              ...(precheckResult
                ? [{
                    label: '预检状态',
                    value: precheckResult.status === 'ok' ? '通过' : precheckResult.status === 'blocked' ? '阻塞' : '警告',
                    tone: (precheckResult.status === 'blocked' ? 'danger' : precheckResult.status === 'warning' ? 'warning' : 'default') as 'danger' | 'warning' | 'default',
                  }]
                : []),
            ]}
          />
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', marginTop: '16px' }}>
          <button className="cc-icon-btn" onClick={goBack} disabled={wizardStep === 0} style={{ height: 32 }}>
            ← 上一步
          </button>
          <span className="cc-chip cc-chip--ghost" style={{ alignSelf: 'center' }}>
            STEP {wizardStep + 1} / 6
          </span>
          <button className="cc-icon-btn cc-icon-btn--success" onClick={goNext} disabled={wizardStep === 5} style={{ height: 32 }}>
            下一步 →
          </button>
        </div>
        <ReleasePlanPanel system={system} service={service} environment={environment} />
        </>
      )}

      {activeTab === 'history' && (
        <>
          <RetentionPolicyPanel
            retentionPolicy={retentionPolicy}
            retentionPreview={retentionPreview}
            retentionLoading={retentionLoading}
            retentionMessage={retentionMessage}
            retentionError={retentionError}
            loadRetention={loadRetention}
            previewRetention={previewRetention}
            saveRetention={saveRetention}
            cleanupRetention={cleanupRetention}
            updateRetentionField={updateRetentionField}
          />
          <DeploymentHistoryTable
            deployments={deployments}
            pagination={deploymentPagination}
            selectedIds={selectedDeploymentIds}
            onSelectionChange={setSelectedDeploymentIds}
            onDeleteSelected={openDeleteSelectedDeploymentHistory}
            onQuery={(filters) => {
              setSelectedDeploymentIds([])
              return loadDeployments(filters).then(setDeploymentPagination)
            }}
            onRollback={handleRollback}
            onReuse={(d) => {
              setSystem(d.system || '')
              setService(d.service || '')
              setEnvironment(d.environment || '')
              setFileName(d.version || '')
              setServers(d.servers || '')
              resetRunState('idle')
              setSelectedReport(null)
              setActiveTab('deploy')
            }}
            onViewReport={(d) => {
              openHistoricalDeployment(d, true)
            }}
            onViewLogs={(d) => {
              openHistoricalDeployment(d, true)
            }}
            onDelete={(deploymentRecord) => setDeleteHistoryTargets([deploymentRecord])}
          />
        </>
      )}


      <RiskConfirmDialog
        open={releaseRiskDialogOpen}
        title={pendingReleaseConfirmation?.risk_level === 'medium' ? '确认发布执行' : '确认高风险发布'}
        description="确认后将开始执行发布流程。确认信息会写入审计记录。"
        target={`${pendingReleaseConfirmation?.summary?.system || system}/${pendingReleaseConfirmation?.summary?.service || service || '-'} @ ${pendingReleaseConfirmation?.summary?.environment || environment || '-'}`}
        confirmText={pendingReleaseConfirmation?.required_confirmation || ''}
        value={releaseConfirmText}
        onValueChange={setReleaseConfirmText}
        reason={releaseReason}
        onReasonChange={setReleaseReason}
        reasonRequired={false}
        riskLevel={pendingReleaseConfirmation?.risk_level || 'high'}
        details={[
          { label: '部署方式', value: isSelectedDockerCompose ? '🐳 容器 (Compose) — 镜像自动拉取' : '传统发布 — 上传包' },
          { label: '发布包', value: pendingReleaseConfirmation?.summary?.file_name || (isSelectedDockerCompose ? '—（容器部署无需）' : fileName) || '-' },
          { label: '目标服务器', value: (pendingReleaseConfirmation?.summary?.servers || selectedServerNames || []).join(', ') || '-' },
          { label: '预检结果', value: pendingReleaseConfirmation?.precheck?.status || '-' },
          { label: '警告数量', value: String((pendingReleaseConfirmation?.warnings || []).length + (pendingReleaseConfirmation?.precheck?.warning_checks || []).length) },
        ]}
        onCancel={cancelReleaseRiskDialog}
        onConfirm={confirmRiskRelease}
        confirmMode="one-click"
      />


      <RiskConfirmDialog
        open={cancelRiskDialogOpen}
        title="确认终止当前发布"
        description="已开始执行的远程命令无法强制中断，但后续服务器和后续步骤会停止。"
        target={deploymentId || '-'}
        confirmText={`CANCEL ${deploymentId || ''}`}
        value=""
        onValueChange={() => {}}
        onCancel={cancelCancelDeployment}
        onConfirm={confirmCancelDeployment}
        riskLevel="high"
        details={[
          { label: '任务', value: taskId || '-' },
          { label: '当前状态', value: status || '-' },
        ]}
        confirmButtonLabel="确认终止"
        confirmMode="one-click"
      />

      <RiskConfirmDialog
        open={deleteHistoryTargets.length > 0}
        title={deleteHistoryTargets.length > 1 ? '确认批量删除部署历史' : '确认删除部署历史'}
        description="将删除部署记录及关联任务、日志、步骤和分发明细；不会删除发布包文件或审计日志。运行中的部署不可删除。"
        target={deleteHistoryTargets.length > 1 ? `已选择 ${deleteHistoryTargets.length} 条部署历史` : (deleteHistoryTargets[0] ? `${deleteHistoryTargets[0].system || '-'} / ${deleteHistoryTargets[0].service || '-'} / ${deleteHistoryTargets[0].id}` : '-')}
        confirmText={deleteHistoryTargets.length > 1 ? `DELETE DEPLOYMENTS ${deleteHistoryTargets.length}` : `DELETE DEPLOYMENT ${deleteHistoryTargets[0]?.id || ''}`}
        value=""
        onValueChange={() => {}}
        onCancel={() => { if (!deletingHistory) setDeleteHistoryTargets([]) }}
        onConfirm={confirmDeleteDeploymentHistory}
        riskLevel="high"
        details={[
          { label: '数量', value: String(deleteHistoryTargets.length) },
          { label: '环境', value: deleteHistoryTargets.length > 1 ? Array.from(new Set(deleteHistoryTargets.map((item) => item.environment || '-'))).join(', ') : (deleteHistoryTargets[0]?.environment || '-') },
          { label: '状态', value: deleteHistoryTargets.length > 1 ? Array.from(new Set(deleteHistoryTargets.map((item) => item.status || '-'))).join(', ') : (deleteHistoryTargets[0]?.status || '-') },
          { label: '版本', value: deleteHistoryTargets.length > 1 ? deleteHistoryTargets.map((item) => item.version || item.id).slice(0, 5).join(', ') : (deleteHistoryTargets[0]?.version || '-') },
        ]}
        confirmButtonLabel={deletingHistory ? '删除中...' : '确认删除'}
        confirmDisabled={deletingHistory}
        confirmMode="one-click"
      />

      <RollbackPlanDialog
        planData={rollbackPlanData}
        confirmText={rollbackConfirmText}
        submitting={rollbackSubmitting}
        onConfirmTextChange={setRollbackConfirmText}
        onClose={closeRollbackPlan}
        onConfirm={submitRollbackPlan}
      />

      {(taskId || deploymentId) && (
        <>
        <DeploymentRunPanel
          taskId={taskId || deploymentId}
          deploymentId={deploymentId}
          status={status}
          logs={logs}
          taskDetails={taskDetails}
          serverExecutionRows={serverExecutionRows}
          timelineSteps={timelineSteps}
          onCancel={handleCancelDeployment}
          onRefreshTask={() => {
            if (taskId) fetchLogs(taskId).catch(() => {})
            else if (deploymentId) {
              deployment.deploymentLogs(deploymentId).then((res: any) => {
                setLogs(res.data.logs || [])
              }).catch(() => {})
            }
          }}
          reportMarkdownUrl={deploymentId ? deployment.reportMarkdownUrl(deploymentId) : undefined}
          reportTextUrl={deploymentId ? deployment.reportTextUrl(deploymentId) : undefined}
          report={selectedReport}
        />
        <div style={{ display: 'flex', gap: 8, marginTop: 12, alignItems: 'center', justifyContent: 'flex-end' }}>
          <span className="muted-text" style={{ fontSize: 13 }}>执行后可查看：</span>
          <Link className="btn btn-subtle btn-sm" to={ROUTES.audit}>审计日志</Link>
          <Link className="btn btn-subtle btn-sm" to={ROUTES.reports}>报告中心</Link>
          <Link className="btn btn-subtle btn-sm" to={ROUTES.tasks}>任务中心</Link>
        </div>
        </>
      )}
    </div>
  )
}
