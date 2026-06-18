#!/usr/bin/env node
const fs = require('fs')
const path = require('path')

const root = path.resolve(__dirname, '..')
const app = fs.readFileSync(path.join(root, 'frontend', 'src', 'App.tsx'), 'utf8')
const routesSource = fs.readFileSync(path.join(root, 'frontend', 'src', 'routes.ts'), 'utf8')
const pages = fs.readFileSync(path.join(root, 'app', 'pages.py'), 'utf8')
const security = fs.readFileSync(path.join(root, 'app', 'core', 'security.py'), 'utf8')
const deployTools = fs.readFileSync(path.join(root, 'app', 'services', 'tool_adapters', 'deploy_tools.py'), 'utf8')
const sftpApi = fs.readFileSync(path.join(root, 'app', 'api', 'sftp.py'), 'utf8')
const serversApi = fs.readFileSync(path.join(root, 'app', 'api', 'servers.py'), 'utf8')
const maintenanceApi = fs.readFileSync(path.join(root, 'app', 'api', 'maintenance.py'), 'utf8')
const systemApi = fs.readFileSync(path.join(root, 'app', 'api', 'system.py'), 'utf8')
const diagnosticsService = fs.readFileSync(path.join(root, 'app', 'services', 'diagnostics.py'), 'utf8')
const errorLogService = fs.readFileSync(path.join(root, 'app', 'services', 'error_log.py'), 'utf8')
const diagnosticTools = fs.readFileSync(path.join(root, 'app', 'services', 'tool_adapters', 'diagnostic_tools.py'), 'utf8')
const aiDiagnosticsService = fs.readFileSync(path.join(root, 'app', 'services', 'ai_diagnostics.py'), 'utf8')
const aiTools = fs.readFileSync(path.join(root, 'app', 'services', 'tool_adapters', 'ai_tools.py'), 'utf8')
const diagnosticsPage = fs.readFileSync(path.join(root, 'frontend', 'src', 'pages', 'SystemDiagnosticsPage.tsx'), 'utf8')
const backupService = fs.readFileSync(path.join(root, 'app', 'services', 'backup_service.py'), 'utf8')
const backupTools = fs.readFileSync(path.join(root, 'app', 'services', 'tool_adapters', 'backup_tools.py'), 'utf8')
const toolPolicy = fs.readFileSync(path.join(root, 'app', 'services', 'tool_policy.py'), 'utf8')
const riskPolicy = fs.readFileSync(path.join(root, 'app', 'services', 'risk_policy.py'), 'utf8')
const jobService = fs.readFileSync(path.join(root, 'app', 'services', 'job_service.py'), 'utf8')
const jobTools = fs.readFileSync(path.join(root, 'app', 'services', 'tool_adapters', 'job_tools.py'), 'utf8')
const taskCenterApi = fs.readFileSync(path.join(root, 'app', 'api', 'task_center.py'), 'utf8')
const runtimeJobs = fs.readFileSync(path.join(root, 'app', 'domain', 'runtime', 'jobs.py'), 'utf8')
const toolAccessPage = fs.readFileSync(path.join(root, 'frontend', 'src', 'pages', 'ToolAccessPage.tsx'), 'utf8')
const backupActions = fs.readFileSync(path.join(root, 'frontend', 'src', 'pages', 'maintenance', 'useMaintenanceBackupActions.ts'), 'utf8')
const toolsApi = fs.readFileSync(path.join(root, 'app', 'api', 'tools.py'), 'utf8')
const mcpCapabilityService = fs.readFileSync(path.join(root, 'app', 'services', 'mcp_capability_service.py'), 'utf8')
const mcpCapabilitySurface = `${toolsApi}\n${mcpCapabilityService}`

const releasePlanService = fs.readFileSync(path.join(root, 'app', 'services', 'release_plan.py'), 'utf8')
const releasePlanPanel = fs.readFileSync(path.join(root, 'frontend', 'src', 'pages', 'deploy', 'ReleasePlanPanel.tsx'), 'utf8')
const deployApiV2 = fs.readFileSync(path.join(root, 'app', 'api', 'deploy_v2.py'), 'utf8')
const deployPlansApi = fs.readFileSync(path.join(root, 'app', 'api', 'deploy', 'plans.py'), 'utf8')
const deployPrecheckApi = fs.readFileSync(path.join(root, 'app', 'api', 'deploy', 'precheck.py'), 'utf8')
const deployExecutionsApi = fs.readFileSync(path.join(root, 'app', 'api', 'deploy', 'executions.py'), 'utf8')
const deployApiRoutes = [deployApiV2, deployPlansApi, deployPrecheckApi, deployExecutionsApi].join('\n')
const frontendApi = fs.readFileSync(path.join(root, 'frontend', 'src', 'api.ts'), 'utf8')
const auditChainService = fs.readFileSync(path.join(root, 'app', 'services', 'audit_chain.py'), 'utf8')
const auditTools = fs.readFileSync(path.join(root, 'app', 'services', 'tool_adapters', 'audit_tools.py'), 'utf8')
const auditPage = fs.readFileSync(path.join(root, 'frontend', 'src', 'pages', 'AuditLogPage.tsx'), 'utf8')
const reportCenterService = fs.readFileSync(path.join(root, 'app', 'services', 'report_center.py'), 'utf8')
const reportTools = fs.readFileSync(path.join(root, 'app', 'services', 'tool_adapters', 'report_tools.py'), 'utf8')
const reportsApi = fs.readFileSync(path.join(root, 'app', 'api', 'reports.py'), 'utf8')
const reportPage = fs.readFileSync(path.join(root, 'frontend', 'src', 'pages', 'ReportCenterPage.tsx'), 'utf8')
const dbQueryService = fs.readFileSync(path.join(root, 'app', 'services', 'db_query_export.py'), 'utf8')
const dbToolsApi = fs.readFileSync(path.join(root, 'app', 'api', 'db_tools.py'), 'utf8')
const dbToolAdapter = fs.readFileSync(path.join(root, 'app', 'services', 'tool_adapters', 'db_tools.py'), 'utf8')
const databasePage = fs.readFileSync(path.join(root, 'frontend', 'src', 'pages', 'DatabaseToolsPage.tsx'), 'utf8')
const dashboardPage = fs.readFileSync(path.join(root, 'frontend', 'src', 'pages', 'DashboardPage.tsx'), 'utf8')
const dashboardService = fs.readFileSync(path.join(root, 'app', 'services', 'dashboard.py'), 'utf8')
const frontendStyles = fs.readFileSync(path.join(root, 'frontend', 'src', 'index.css'), 'utf8')
const inspectionPage = fs.readFileSync(path.join(root, 'frontend', 'src', 'pages', 'InspectionCenterPage.tsx'), 'utf8')
const inspectionPaginationPath = path.join(root, 'frontend', 'src', 'pages', 'inspection', 'PaginationControls.tsx')
const inspectionPaginationComponent = fs.existsSync(inspectionPaginationPath) ? fs.readFileSync(inspectionPaginationPath, 'utf8') : ''
const inspectionRunDetailModalPath = path.join(root, 'frontend', 'src', 'pages', 'inspection', 'RunDetailRawModal.tsx')
const inspectionRunDetailModalComponent = fs.existsSync(inspectionRunDetailModalPath) ? fs.readFileSync(inspectionRunDetailModalPath, 'utf8') : ''

const routeEntries = new Map()
for (const match of routesSource.matchAll(/([a-zA-Z][a-zA-Z0-9_]*):\s*'([^']+)'/g)) {
  routeEntries.set(match[2], match[1])
}

const requiredRoutes = [
  '/', '/dashboard', '/login', '/deploy', '/system', '/system/status', '/system/diagnostics',
  '/servers', '/systems', '/tasks', '/task-center', '/maintenance', '/files', '/pipelines', '/audit', '/reports', '/database', '/tools',
]
const failures = []

function hasQuotedRoute(source, route) {
  return source.includes(`"${route}"`) || source.includes(`'${route}'`)
}

for (const route of requiredRoutes) {
  const routeKey = routeEntries.get(route)
  if (!routeKey) failures.push(`ROUTES constant missing: ${route}`)
  if (!hasQuotedRoute(pages, route)) failures.push(`SPA backend route missing: ${route}`)
  if (routeKey && !app.includes(`path={ROUTES.${routeKey}}`) && !hasQuotedRoute(app, route)) {
    failures.push(`React route missing: ${route} (ROUTES.${routeKey})`)
  }
}

if (!app.includes('path="*"') || !app.includes('NotFoundPage')) failures.push('React catch-all NotFound route missing')
if (!app.includes('__OPS_FRONTEND_READY__')) failures.push('frontend ready marker missing')
if (!app.includes('__OPS_FRONTEND_BUILD__')) failures.push('frontend build marker missing')
if (!fs.existsSync(path.join(root, 'frontend', 'scripts', 'generate-build-info.cjs'))) failures.push('frontend build info generator missing')
if (!fs.existsSync(path.join(root, 'frontend', 'src', 'generated', 'buildInfo.ts'))) failures.push('frontend generated buildInfo.ts missing')

const dist = path.join(root, 'frontend', 'dist')
const index = path.join(dist, 'index.html')
const assets = path.join(dist, 'assets')
if (!fs.existsSync(index)) failures.push('frontend/dist/index.html missing')
const builtJs = fs.existsSync(assets) ? fs.readdirSync(assets).filter((name) => name.endsWith('.js')) : []
if (!builtJs.length) failures.push('frontend/dist/assets/*.js missing')
if (builtJs.length) {
  const combinedJs = builtJs.map((name) => fs.readFileSync(path.join(assets, name), 'utf8')).join('\n')
  for (const marker of ['/system/status', '/system/diagnostics', '__OPS_FRONTEND_READY__']) {
    if (!combinedJs.includes(marker)) failures.push(`built frontend JS missing marker: ${marker}`)
  }
}

if (!security.includes('/assets/')) failures.push('auth middleware must allow /assets/ static files')
if (!security.includes('/api/v2/mcp')) failures.push('auth middleware must allow MCP bearer-token endpoints')
if (!deployTools.includes('tool_result')) failures.push('MCP deploy tools should use stable tool_result shape')
if (!serversApi.includes('/ops-summary') || !serversApi.includes('/ops-health')) failures.push('server ops summary/health endpoints missing')
if (!sftpApi.includes('/files/tail') || !sftpApi.includes('download-stream')) failures.push('SFTP tail/stream endpoints missing')
if (!sftpApi.includes('Deleting files requires matching confirm_path')) failures.push('SFTP delete confirm_path guard missing')
if (!maintenanceApi.includes('/query/analyze') || !maintenanceApi.includes('pagination')) failures.push('SQL analyze/history pagination endpoints missing')
if (!systemApi.includes('/build-info')) failures.push('system build-info API missing')
if (!diagnosticsService.includes('build_info')) failures.push('diagnostics build_info section missing')

if (!systemApi.includes('/diagnostics/report')) failures.push('system diagnostics JSON report API missing')
if (!systemApi.includes('/recent-errors')) failures.push('system recent-errors API missing')
for (const marker of ['/backups/{file_name:path}/verify', '/restore-db', 'confirm_text']) {
  if (!fs.readFileSync(path.join(root, 'app', 'api', 'config.py'), 'utf8').includes(marker)) failures.push(`admin backup API marker missing: ${marker}`)
}
if (!diagnosticsService.includes('recent_errors')) failures.push('diagnostics recent_errors section missing')
if (!diagnosticsService.includes('recommendations')) failures.push('diagnostics recommendations missing')
if (!errorLogService.includes('get_recent_errors')) failures.push('error log aggregation service missing')
for (const toolName of ['ops.get_system_status', 'ops.get_build_info', 'ops.get_recent_errors', 'ops.export_diagnostics_report']) {
  if (!diagnosticTools.includes(toolName)) failures.push(`MCP diagnostic read-only tool missing: ${toolName}`)
}

for (const marker of ['RESTORE ', 'DELETE ', 'verify_backup_file', 'restore_database_backup', 'delete_database_backup']) {
  if (!backupService.includes(marker)) failures.push(`backup service marker missing: ${marker}`)
}
for (const toolName of ['ops.list_backups', 'ops.verify_backup', 'ops.create_backup', 'ops.restore_backup', 'ops.delete_backup']) {
  if (!backupTools.includes(toolName)) failures.push(`MCP backup tool missing: ${toolName}`)
}
for (const marker of ['allow_backup_write', 'allow_backup_restore', 'backup_write', 'backup_restore']) {
  if (!toolPolicy.includes(marker)) failures.push(`backup tool policy marker missing: ${marker}`)
}
if (!systemApi.includes('/recent-errors')) failures.push('system recent-errors API missing')

for (const marker of ['RiskDecision', 'enforce_risk_policy', 'CONFIRMATION_REQUIRED', 'risk_policy_manifest']) {
  if (!riskPolicy.includes(marker)) failures.push(`risk policy marker missing: ${marker}`)
}
for (const marker of ['allow_high_risk_tools', 'allow_critical_risk_tools', 'risk_policy']) {
  if (!toolPolicy.includes(marker)) failures.push(`Iter34 tool policy marker missing: ${marker}`)
}
if (!toolsApi.includes('/risk-policy')) failures.push('tools risk-policy API missing')
if (!toolAccessPage.includes('riskPolicy') || !toolAccessPage.includes('allow_critical_risk_tools')) failures.push('ToolAccessPage risk policy integration missing')
if (!backupActions.includes('pendingRiskAction') || !backupActions.includes('RESTORE ${file}') || !backupActions.includes('DELETE ${file}')) failures.push('Backup risk action dialog integration missing')

for (const marker of ['OperationJob', 'related_job_id']) {
  if (!fs.readFileSync(path.join(root, 'app', 'db', 'models.py'), 'utf8').includes(marker)) failures.push(`Iter35 db model marker missing: ${marker}`)
}
for (const marker of ['enqueue_tool_job', 'start_job_worker', 'mcp_tool']) {
  if (!jobService.includes(marker)) failures.push(`Iter35 job service marker missing: ${marker}`)
}
for (const toolName of ['ops.list_jobs', 'ops.get_job_status']) {
  if (!jobTools.includes(toolName)) failures.push(`MCP job tool missing: ${toolName}`)
}
if (!riskPolicy.includes('taskize_high_risk_tools') || !riskPolicy.includes('must_create_job')) failures.push('Iter35 risk policy taskization marker missing')
if (!toolPolicy.includes('taskize_high_risk_tools')) failures.push('Iter35 tool policy taskize setting missing')
if (!runtimeJobs.includes('OperationJob') || !runtimeJobs.includes('"tool"') || !taskCenterApi.includes('list_tasks')) failures.push('Task center OperationJob integration missing')
if (!fs.readFileSync(path.join(root, 'frontend', 'src', 'pages', 'TaskCenterPage.tsx'), 'utf8').includes('工具任务')) failures.push('TaskCenterPage tool job UI marker missing')



for (const marker of ['release_runbook', 'release_quality_gates', 'rollback_readiness', 'mcp_flow']) {
  if (!releasePlanService.includes(marker)) failures.push(`Iter36 release plan service marker missing: ${marker}`)
}
for (const toolName of ['ops.list_deploy_plans', 'ops.get_deploy_plan', 'ops.generate_release_runbook', 'ops.get_rollback_readiness']) {
  if (!deployTools.includes(toolName)) failures.push(`Iter36 MCP deploy orchestration tool missing: ${toolName}`)
}
for (const marker of ['/tool-plans', '/rollback-readiness']) {
  if (!deployApiRoutes.includes(marker)) failures.push(`Iter36 deploy orchestration API marker missing: ${marker}`)
}
if (!releasePlanPanel.includes('发布计划与 MCP 编排') || !releasePlanPanel.includes('quality_gates') || !releasePlanPanel.includes('mcp_flow')) failures.push('ReleasePlanPanel MCP orchestration UI marker missing')
if (!frontendApi.includes('toolPlans') || !frontendApi.includes('toolPlanRunbook') || !frontendApi.includes('rollbackReadiness')) failures.push('frontend deploy API release plan methods missing')
if (!fs.readFileSync(path.join(root, 'frontend', 'src', 'pages', 'DeployPage.tsx'), 'utf8').includes('ReleasePlanPanel')) failures.push('DeployPage ReleasePlanPanel integration missing')


for (const marker of ['build_ai_diagnostic_analysis', 'safe_mcp_toolchain', 'guardrails', 'read_only_analysis']) {
  if (!aiDiagnosticsService.includes(marker)) failures.push(`Iter37 AI diagnostics service marker missing: ${marker}`)
}
if (!systemApi.includes('/ai-diagnostics')) failures.push('Iter37 system AI diagnostics API missing')
if (!aiTools.includes('ops.analyze_diagnostics') || !aiTools.includes('ai_read')) failures.push('Iter37 MCP AI diagnostics tool missing')
if (!fs.readFileSync(path.join(root, 'app', 'services', 'tool_registry.py'), 'utf8').includes('ai_tools')) failures.push('Iter37 tool registry ai_tools import missing')
if (!diagnosticsPage.includes('AI 诊断助手') || !diagnosticsPage.includes('safe_mcp_toolchain') || !diagnosticsPage.includes('aiDiagnostics')) failures.push('Iter37 diagnostics page AI assistant UI missing')
if (!frontendApi.includes('aiDiagnostics')) failures.push('Iter37 frontend API aiDiagnostics method missing')
if (!mcpCapabilitySurface.includes('ops_diagnostic_triage')) failures.push('Iter37 MCP diagnostic triage prompt missing')



for (const marker of ['build_operation_chain', 'list_operation_chains', 'iter38.audit-chain.v1', 'read_only_audit_replay']) {
  if (!auditChainService.includes(marker)) failures.push(`Iter38 audit chain service marker missing: ${marker}`)
}
for (const toolName of ['ops.list_operation_chains', 'ops.get_operation_chain']) {
  if (!auditTools.includes(toolName)) failures.push(`Iter38 MCP audit replay tool missing: ${toolName}`)
}
for (const marker of ['/operation-chains', 'build_operation_chain', 'list_operation_chains']) {
  if (!taskCenterApi.includes(marker)) failures.push(`Iter38 audit operation chain API marker missing: ${marker}`)
}
if (!auditPage.includes('MCP / AI 操作链路回放') || !auditPage.includes('operationChains') || !auditPage.includes('operationChain')) failures.push('AuditLogPage operation chain replay UI missing')
if (!frontendApi.includes('operationChains') || !frontendApi.includes('operationChain')) failures.push('frontend audit API operation chain methods missing')
if (!mcpCapabilitySurface.includes('ops_operation_replay')) failures.push('Iter38 MCP operation replay prompt missing')
if (!mcpCapabilitySurface.includes('ops://operation-chains')) failures.push('Iter38 MCP operation chains resource missing')



for (const marker of ['generate_report', 'list_reports', 'report_summary', 'iter39.report-center.v1']) {
  if (!reportCenterService.includes(marker)) failures.push(`Iter39 report center service marker missing: ${marker}`)
}
for (const toolName of ['ops.list_reports', 'ops.get_report', 'ops.generate_report', 'ops.get_report_summary']) {
  if (!reportTools.includes(toolName)) failures.push(`Iter39 MCP report tool missing: ${toolName}`)
}
for (const marker of ['/generate', '/summary', '/operation-chain/export']) {
  if (!reportsApi.includes(marker)) failures.push(`Iter39 report API marker missing: ${marker}`)
}
if (!reportPage.includes('报告中心') || !reportPage.includes('generateType') || !reportPage.includes('operation_chain')) failures.push('Iter39 ReportCenterPage UI marker missing')
if (!frontendApi.includes('export const reports') || !frontendApi.includes('downloadUrl')) failures.push('frontend report API methods missing')
if (!mcpCapabilitySurface.includes('ops://reports')) failures.push('Iter39 MCP reports resource missing')
if (!mcpCapabilitySurface.includes('ops_report_brief')) failures.push('Iter39 MCP report prompt missing')


for (const toolName of ['ops.db.list_tables', 'ops.db.describe_table', 'ops.db.query_readonly', 'ops.db.export_query_result', 'ops.db.list_exports', 'ops.db.get_export']) {
  if (!dbToolAdapter.includes(toolName)) failures.push(`Iter40 MCP DB tool missing: ${toolName}`)
}
for (const marker of ['iter40.db-query-export.v1', 'EXPORT_FORMATS', 'sql_query', 'xlsx', 'ReportArtifact']) {
  if (!dbQueryService.includes(marker)) failures.push(`Iter40 DB export service marker missing: ${marker}`)
}
for (const marker of ['/query/export', '/exports/{export_id}/download', 'export_media_type']) {
  if (!dbToolsApi.includes(marker)) failures.push(`Iter40 DB API marker missing: ${marker}`)
}
if (!frontendApi.includes('export const dbTools') || !frontendApi.includes('/db/query/export')) failures.push('frontend dbTools API methods missing')
if (!databasePage.includes('数据库查询与多格式导出') || !databasePage.includes('FORMAT_OPTIONS') || !databasePage.includes('exportQuery')) failures.push('DatabaseToolsPage export UI missing')
if (!reportCenterService.includes('db_query_export')) failures.push('Report Center must include db_query_export type')
if (!mcpCapabilitySurface.includes('ops://db/exports')) failures.push('Iter40 MCP db exports resource missing')
if (!mcpCapabilitySurface.includes('ops_db_export_request')) failures.push('Iter40 MCP db export prompt missing')

for (const marker of ['DashboardClock', 'useCurrentTime', '当前时间']) {
  if (!dashboardPage.includes(marker)) failures.push(`Iter41a dashboard clock marker missing: ${marker}`)
}
if (!dashboardPage.includes('<DashboardClock now={now} />')) failures.push('Dashboard clock must render directly inside hero copy, not be clipped by the health orb column')
for (const marker of ['dashboard-clock-card', 'dashboard-hero-side']) {
  if (!frontendStyles.includes(marker)) failures.push(`Iter41a dashboard clock style marker missing: ${marker}`)
}
if (dashboardService.includes('维护审批')) failures.push('Dashboard quick action must not mention approval before the approval feature is introduced')
if (!dashboardService.includes('维护工具') || !dashboardService.includes('备份、清理与恢复')) failures.push('Dashboard maintenance quick action title/description missing')

if (!fs.existsSync(inspectionPaginationPath)) failures.push('Inspection pagination component file missing')
if (!inspectionPage.includes("from './inspection/PaginationControls'")) failures.push('InspectionCenterPage must import extracted PaginationControls')
if (inspectionPage.includes('function PaginationControls(')) failures.push('InspectionCenterPage should not define PaginationControls inline')
if (!inspectionPaginationComponent.includes('export function PaginationControls')) failures.push('PaginationControls component must export PaginationControls')
if (!fs.existsSync(inspectionRunDetailModalPath)) failures.push('Inspection run detail raw modal component file missing')
if (!inspectionPage.includes("from './inspection/RunDetailRawModal'")) failures.push('InspectionCenterPage must import extracted RunDetailRawModal')
if (inspectionPage.includes('function RunDetailRawModal(')) failures.push('InspectionCenterPage should not define RunDetailRawModal inline')
if (!inspectionRunDetailModalComponent.includes('export function RunDetailRawModal')) failures.push('RunDetailRawModal component must export RunDetailRawModal')


if (failures.length) {
  console.error(failures.map((x) => `[FAIL] ${x}`).join('\n'))
  process.exit(1)
}
console.log('frontend_route_check passed')
