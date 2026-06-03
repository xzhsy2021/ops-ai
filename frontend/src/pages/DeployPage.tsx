import { useState, useEffect } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { deployment } from '../api'
import { useNotificationStore } from '../store'
import { ROUTES } from '../routes'
import { RiskConfirmDialog, Skeleton, PageHeader, FavoriteButton } from '../components/ui'
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
import { useDeploymentStream } from './deploy/useDeploymentStream'
import { useDeployFormState } from './deploy/useDeployFormState'
import { useDeployOptionsLoader } from './deploy/useDeployOptionsLoader'
import { useDeployServerSelection } from './deploy/useDeployServerSelection'
import { useDeployActions } from './deploy/useDeployActions'
import { useRetentionPolicy } from './deploy/useRetentionPolicy'

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
    setService,
    setServers,
    setServerAutoMode,
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
  if (!fileName) deployDisabledReasons.push('请选择发布包')
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


  return (
    <div style={{ display: 'grid', gap: '20px' }}>
      <PageHeader
        title="发布管理"
        description="创建、执行和追踪应用发布"
        badge={<span className="tag">{system || '请选择系统'}</span>}
        actions={<FavoriteButton url={ROUTES.deploy} label="发布管理" category="deploy" />}
      />
      <div style={{ display: 'flex', gap: '12px', marginBottom: '4px' }}>
        <button
          className="btn"
          onClick={() => { setSelectedReport(null); setActiveTab('deploy') }}
          style={{ background: activeTab === 'deploy' ? 'var(--action-bg)' : 'var(--border-strong)', color: 'var(--text-primary)' }}
        >
          新发布
        </button>
        <button
          className="btn"
          onClick={() => { setActiveTab('history'); loadDeployments() }}
          style={{ background: activeTab === 'history' ? 'var(--action-bg)' : 'var(--border-strong)', color: 'var(--text-primary)' }}
        >
          部署历史
        </button>
      </div>

      {optionsLoading && systems.length === 0 && (
        <Skeleton type="card" count={3} />
      )}

      {activeTab === 'deploy' && (
        <>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 260px', gap: '16px', alignItems: 'start' }}>
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
                key: 'package', title: '发布包', description: 'Pipeline / 版本 / 文件名',
                content: <DeployPackageStep
                  pipelines={pipelines} deployPackages={deployPackages}
                  system={system} fileName={fileName} pipelineId={pipelineId}
                  pipelineSteps={pipelineSteps} selectStyle={selectStyle}
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
          <div className="glass-card" style={{ padding: '12px', fontSize: '13px', position: 'sticky', top: '12px' }}>
            <div style={{ fontWeight: 600, marginBottom: '10px', fontSize: '14px' }}>已选配置</div>
            <div style={{ display: 'grid', gap: '8px' }}>
              <div>
                <div style={{ color: 'var(--text-muted)', fontSize: '11px' }}>系统 / 服务 / 环境</div>
                <div style={{ color: 'var(--text-primary)' }}>{system || '-'}</div>
                <div style={{ color: 'var(--text-primary)' }}>{service || '-'}</div>
                <div style={{ color: 'var(--text-primary)' }}>{environment || '-'}</div>
              </div>
              <div>
                <div style={{ color: 'var(--text-muted)', fontSize: '11px' }}>发布包</div>
                <div style={{ color: 'var(--text-primary)', wordBreak: 'break-all' }}>{fileName || '-'}</div>
              </div>
              <div>
                <div style={{ color: 'var(--text-muted)', fontSize: '11px' }}>目标服务器 ({selectedServerNames.length})</div>
                <div style={{ color: 'var(--text-primary)', maxHeight: '80px', overflow: 'auto' }}>
                  {selectedServerNames.length > 0 ? selectedServerNames.join(', ') : '-'}
                </div>
              </div>
              <div>
                <div style={{ color: 'var(--text-muted)', fontSize: '11px' }}>Pipeline</div>
                <div style={{ color: 'var(--text-primary)' }}>{pipelineId || '默认'}</div>
              </div>
              <div>
                <div style={{ color: 'var(--text-muted)', fontSize: '11px' }}>并行度 / 快速失败</div>
                <div style={{ color: 'var(--text-primary)' }}>{parallelism} / {failFast ? '是' : '否'}</div>
              </div>
              {precheckResult && (
                <div>
                  <div style={{ color: 'var(--text-muted)', fontSize: '11px' }}>预检状态</div>
                  <div style={{ color: precheckResult.status === 'ok' ? 'var(--success)' : precheckResult.status === 'blocked' ? 'var(--danger)' : 'var(--warning)' }}>
                    {precheckResult.status === 'ok' ? '通过' : precheckResult.status === 'blocked' ? '阻塞' : '警告'}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', marginTop: '16px' }}>
          <button className="btn" onClick={goBack} disabled={wizardStep === 0}
            style={{ background: 'var(--border-strong)', color: 'var(--text-secondary)' }}>
            上一步
          </button>
          <span className="muted-text" style={{ alignSelf: 'center', fontSize: '13px' }}>
            {wizardStep + 1} / 6
          </span>
          <button className="btn" onClick={goNext} disabled={wizardStep === 5}
            style={{ background: 'var(--action-bg)', color: 'var(--action-text)' }}>
            下一步
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
            onQuery={(filters) => loadDeployments(filters).then(setDeploymentPagination)}
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
          { label: '发布包', value: pendingReleaseConfirmation?.summary?.file_name || fileName || '-' },
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
