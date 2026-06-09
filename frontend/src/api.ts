import axios from 'axios'

export function getApiBaseUrl(): string {
  if (typeof window !== 'undefined' && (window as any).__API_BASE_URL__) {
    return (window as any).__API_BASE_URL__
  }

  const configured = import.meta.env.VITE_API_BASE_URL
  if (!configured) return '/api/v2'

  // Keep dev/prod behavior consistent: use the Vite/nginx /api proxy for local addresses.
  // This prevents browser-side CORS failures from http://127.0.0.1:3000 -> http://127.0.0.1:8000.
  if (typeof window !== 'undefined') {
    try {
      const url = new URL(configured)
      const isLoopback = ['127.0.0.1', 'localhost', '0.0.0.0'].includes(url.hostname)
      if (isLoopback) return '/api/v2'
    } catch {
      return configured
    }
  }

  return configured
}

const api = axios.create({
  baseURL: getApiBaseUrl(),
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true,
})

const ttlCache = new Map<string, { expiresAt: number; value: Promise<any> }>()

function cachedGet(key: string, ttlMs: number, getter: () => Promise<any>) {
  const now = Date.now()
  const hit = ttlCache.get(key)
  if (hit && hit.expiresAt > now) return hit.value
  const value = getter().catch((err) => {
    ttlCache.delete(key)
    throw err
  })
  ttlCache.set(key, { expiresAt: now + ttlMs, value })
  return value
}

function clearCachedGet(prefix?: string) {
  if (!prefix) {
    ttlCache.clear()
    return
  }
  for (const key of Array.from(ttlCache.keys())) {
    if (key.startsWith(prefix)) ttlCache.delete(key)
  }
}

function stableKey(prefix: string, params?: Record<string, any>) {
  const entries = Object.entries(params || {}).filter(([, value]) => value !== undefined && value !== null && value !== '')
  entries.sort(([a], [b]) => a.localeCompare(b))
  return `${prefix}:${JSON.stringify(entries)}`
}

let _authBootstrapDone = false
let _backendOnline = true

export function resetAuthBootstrap() {
  _authBootstrapDone = false
}

export function isBackendOnline(): boolean {
  return _backendOnline
}

export function getBaseURL(): string {
  return getApiBaseUrl()
}

api.interceptors.response.use(
  (res) => {
    _backendOnline = true
    return res.data
  },
  (err) => {
    if (axios.isCancel(err)) return Promise.reject(err)
    if (!err.response && err.code === 'ERR_NETWORK') {
      _backendOnline = false
      return Promise.reject('backend_offline')
    }
    if (err.response?.status === 401) {
      const path = window.location.pathname
      const isLoginPage = path === '/login'
      const isAuthMeRequest = err.config?.url?.endsWith('/auth/me')

      if (isLoginPage) {
        return Promise.reject('not_authenticated')
      }

      if (isAuthMeRequest && !_authBootstrapDone) {
        _authBootstrapDone = true
        return Promise.reject('not_authenticated')
      }

      if (!isLoginPage) {
        window.location.href = '/login'
      }
    }
    const detail = err.response?.data?.detail || err.response?.data?.message || err.message
    return Promise.reject(detail)
  }
)

export function createAbortController() {
  return new AbortController()
}

export const auth = {
  login: (username: string, password: string) =>
    api.post('/auth/login', { username, password }),
  logout: () => api.post('/auth/logout'),
  me: () => api.get('/auth/me'),
}

export const deploy = {
  execute: (data: any) => api.post('/deploy/execute', data),
  confirmation: (data: any) => api.post('/deploy/confirmation', data),
  logs: (taskId: string, options?: { signal?: AbortSignal; limit?: number; cursor?: string; etag?: string }) =>
    api.get(`/deploy/logs/${taskId}`, {
      signal: options?.signal,
      params: { limit: options?.limit, cursor: options?.cursor },
      headers: options?.etag ? { 'If-None-Match': options.etag } : undefined,
    }),
  task: (taskId: string, signal?: AbortSignal) => api.get(`/deploy/tasks/${taskId}`, { signal }),
  deploymentTasks: (deploymentId: string) => api.get(`/deploy/deployments/${deploymentId}/tasks`),
  cancelDeployment: (deploymentId: string) => api.post(`/deploy/deployments/${deploymentId}/cancel`),
  workerStatus: () => api.get('/deploy/worker/status'),
  aggregateStatus: () => api.get('/status/deploy-aggregate'),
}


export const pipeline = {
  list: (systemName?: string) => api.get('/pipelines', { params: { system_name: systemName } }),
  get: (id: string) => api.get(`/pipelines/${id}`),
  create: (data: any) => api.post('/pipelines', data),
  update: (id: string, data: any) => api.put(`/pipelines/${id}`, data),
  delete: (id: string) => api.delete(`/pipelines/${id}`),
  duplicate: (id: string, newName?: string) => api.post(`/pipelines/${id}/duplicate`, { new_name: newName }),
  batchUpdate: (ids: string[], updates: Record<string, any>) => api.put('/pipelines/batch/update', { ids, updates }),
  addStep: (pipelineId: string, data: any) => api.post(`/pipelines/${pipelineId}/steps`, data),
  updateStep: (pipelineId: string, stepId: string, data: any) => api.put(`/pipelines/${pipelineId}/steps/${stepId}`, data),
  deleteStep: (pipelineId: string, stepId: string) => api.delete(`/pipelines/${pipelineId}/steps/${stepId}`),
  reorderSteps: (pipelineId: string, stepIds: string[]) => api.put(`/pipelines/${pipelineId}/steps/reorder`, { step_ids: stepIds }),
  templates: () => api.get('/pipelines/templates'),
  createFromTemplate: (data: any) => api.post('/pipelines/from-template', data),
}

export const resource = {
  systems: Object.assign(
    () => api.get('/systems'),
    {
      list: () => api.get('/systems'),
      get: (name: string) => api.get(`/systems/${encodeURIComponent(name)}`),
      create: (data: any) => api.post('/systems', data),
      update: (name: string, data: any) => api.put(`/systems/${encodeURIComponent(name)}`, data),
      delete: (name: string) => api.delete(`/systems/${encodeURIComponent(name)}`),
      getVariableInheritance: (name: string) => api.get(`/systems/${encodeURIComponent(name)}/variable-inheritance`),
    }
  ),
  services: (system?: string, environment?: string) => api.get('/services', { params: { system, environment } }),
  systemServices: {
    list: (system: string) => api.get(`/systems/${encodeURIComponent(system)}/services`),
    create: (system: string, data: any) => api.post(`/systems/${encodeURIComponent(system)}/services`, data),
    update: (system: string, serviceName: string, data: any) => api.put(`/systems/${encodeURIComponent(system)}/services/${encodeURIComponent(serviceName)}`, data),
    delete: (system: string, serviceName: string) => api.delete(`/systems/${encodeURIComponent(system)}/services/${encodeURIComponent(serviceName)}`),
  },
  systemGroups: {
    list: (system: string, environment?: string) => api.get(`/systems/${encodeURIComponent(system)}/groups`, { params: { environment: environment || undefined } }),
    get: (system: string, groupCode: string) => api.get(`/systems/${encodeURIComponent(system)}/groups/${encodeURIComponent(groupCode)}`),
    create: (system: string, data: any) => api.post(`/systems/${encodeURIComponent(system)}/groups`, data),
    update: (system: string, groupCode: string, data: any) => api.put(`/systems/${encodeURIComponent(system)}/groups/${encodeURIComponent(groupCode)}`, data),
    delete: (system: string, groupCode: string) => api.delete(`/systems/${encodeURIComponent(system)}/groups/${encodeURIComponent(groupCode)}`),
  },
  systemEnvironments: {
    create: (system: string, data: any) => api.post(`/systems/${encodeURIComponent(system)}/environments`, data),
    update: (system: string, envName: string, data: any) => api.put(`/systems/${encodeURIComponent(system)}/environments/${encodeURIComponent(envName)}`, data),
    delete: (system: string, envName: string) => api.delete(`/systems/${encodeURIComponent(system)}/environments/${encodeURIComponent(envName)}`),
  },
  environments: (system?: string) => api.get('/environments', { params: { system } }),
  stepTypes: () => api.get('/step-types'),
}

export const deployment = {
  list: (params?: number | { limit?: number; offset?: number; system?: string; service?: string; environment?: string; status?: string; created_by?: string; q?: string }, system?: string) => {
    const query = typeof params === 'number' ? { limit: params, system } : (params || {})
    return api.get('/deploy/deployments', { params: query })
  },
  rollback: (deploymentId: string, data?: any) => api.post(`/deploy/rollback/${deploymentId}`, data || {}),
  rollbackPlan: (deploymentId: string) => api.get(`/deploy/deployments/${deploymentId}/rollback-plan`),
  rollbackReadiness: (deploymentId: string) => api.get(`/deploy/deployments/${deploymentId}/rollback-readiness`),
  toolPlans: (params?: { plan_type?: string; status?: string; system?: string; service?: string; environment?: string; limit?: number }) => api.get('/deploy/tool-plans', { params }),
  toolPlanRunbook: (planId: string, includeEvents = true) => api.get(`/deploy/tool-plans/${encodeURIComponent(planId)}/runbook`, { params: { include_events: includeEvents } }),
  locks: () => api.get('/deploy/locks'),
  releaseLock: (lockKey: string) => api.post('/deploy/locks/release', { lock_key: lockKey }),
  precheck: (data: any) => api.post('/deploy/precheck', data),
  preflight: (data: any) => api.post('/deploy/preflight', data),
  deploymentLogs: (deploymentId: string, options?: { limit?: number; cursor?: string; etag?: string }) =>
    api.get(`/deploy/deployments/${deploymentId}/logs`, {
      params: { limit: options?.limit, cursor: options?.cursor },
      headers: options?.etag ? { 'If-None-Match': options.etag } : undefined,
    }),
  report: (deploymentId: string) => api.get(`/deploy/deployments/${deploymentId}/report`),
  reportTextUrl: (deploymentId: string) => `${getBaseURL()}/deploy/deployments/${encodeURIComponent(deploymentId)}/report.txt`,
  reportMarkdownUrl: (deploymentId: string) => `${getBaseURL()}/deploy/deployments/${encodeURIComponent(deploymentId)}/report.md`,
  retry: (deploymentId: string, data?: any) => api.post(`/deploy/deployments/${deploymentId}/retry`, data || {}),
  cancel: (deploymentId: string) => api.post(`/deploy/deployments/${deploymentId}/cancel`),
  tasks: (deploymentId: string) => api.get(`/deploy/deployments/${deploymentId}/tasks`),
  retention: () => api.get('/deploy/retention'),
  updateRetention: (data: any) => api.put('/deploy/retention', data),
  previewRetention: () => api.post('/deploy/retention/preview', {}),
  cleanupRetention: (data: any) => api.post('/deploy/retention/cleanup', data),
  notifications: () => api.get('/deploy/notifications'),
  updateNotifications: (data: any) => api.put('/deploy/notifications', data),
  testNotifications: () => api.post('/deploy/notifications/test'),
}

export const files = {
  deployPackages: (params?: { system?: string; service?: string }) => api.get('/files/deploy-packages', { params }),
  browseRemote: (serverName: string, path: string) => api.post(`/files/browse/${encodeURIComponent(serverName)}`, { path }),
  checksum: (fileName: string) => api.get(`/files/checksum/${encodeURIComponent(fileName)}`),
  upload: (formData: FormData, params?: { system?: string; service?: string; overwrite?: boolean }) => api.post('/files/upload', formData, { params, headers: { 'Content-Type': 'multipart/form-data' } }),
  uploadRemote: (serverName: string, formData: FormData) => api.post(`/files/upload-remote/${encodeURIComponent(serverName)}`, formData, { headers: { 'Content-Type': 'multipart/form-data' } }),
  packageRetention: () => api.get('/files/packages/retention'),
  updatePackageRetention: (data: any) => api.put('/files/packages/retention', data),
  previewPackageCleanup: (data?: any) => api.post('/files/packages/cleanup/preview', data || {}),
  cleanupPackages: (data?: any) => api.post('/files/packages/cleanup', data || {}),
  deletePackage: (packageName: string) => api.delete(`/files/packages/${encodeURIComponent(packageName)}`),
  protectPackage: (packageName: string, protectedValue: boolean) => api.post(`/files/packages/${encodeURIComponent(packageName)}/protect`, { protected: protectedValue }),
}

export const serverGroups = {
  list: () => api.get('/groups'),
  create: (data: any) => api.post('/groups', data),
  update: (id: string, data: any) => api.put(`/groups/${id}`, data),
  delete: (id: string) => api.delete(`/groups/${id}`),
}

export const serverManagement = {
  list: (withStatus = true) => api.get('/servers', { params: { with_status: withStatus } }),
  opsSummary: () => api.get('/servers/ops-summary'),
  opsHealth: (name: string, force = false) => api.get(`/servers/${encodeURIComponent(name)}/ops-health`, { params: { force } }),
  get: (name: string) => api.get(`/servers/${encodeURIComponent(name)}`),
  create: (data: any) => api.post('/servers', data),
  update: (name: string, data: any) =>
    api.put(`/servers/${encodeURIComponent(name)}`, data),
  delete: (name: string) => api.delete(`/servers/${encodeURIComponent(name)}`),
  batchUpdate: (names: string[], updates: Record<string, any>) =>
    api.put('/servers/batch', { names, updates }),
  groups: {
    list: () => api.get('/servers/groups/list'),
    create: (name: string) =>
      api.post('/servers/groups/create', { name }),
    assign: (serverNames: string[], group: string) =>
      api.post('/servers/groups/assign', { server_names: serverNames, group }),
    rename: (oldName: string, newName: string) =>
      api.post('/servers/groups/rename', { old_name: oldName, new_name: newName }),
    delete: (name: string) =>
      api.post('/servers/groups/delete', { name }),
  },
}


export const systemHealth = {
  health: () => api.get('/system/health'),
  dashboard: () => api.get('/system/dashboard'),
  diagnostics: () => api.get('/system/diagnostics'),
  diagnosticsReport: () => api.get('/system/diagnostics/report'),
  buildInfo: () => api.get('/system/build-info'),
  recentErrors: (params?: { limit?: number; include_warnings?: boolean }) => api.get('/system/recent-errors', { params }),
  aiDiagnostics: (params?: { mode?: string; focus?: string; include_report?: boolean }) => api.get('/system/ai-diagnostics', { params }),
  diagnosticsExportUrl: () => `${getBaseURL()}/system/diagnostics/export`,
  diagnosticsReportExportUrl: () => `${getBaseURL()}/system/diagnostics/report/export`,
  startupCheck: () => api.get('/system/startup-check'),
  mcpSelfCheck: () => api.get('/system/mcp/self-check'),
  runtimeUsage: () => api.get('/system/runtime/usage'),
  storageUsage: () => api.get('/system/storage/usage'),
  storageSummary: () => api.get('/system/storage/summary'),
  storageScan: () => api.post('/system/storage/scan'),
  runtimeRetention: () => api.get('/system/runtime/retention'),
  updateRuntimeRetention: (policy: any) => api.put('/system/runtime/retention', { policy }),
  previewRuntimeCleanup: (data?: any) => api.post('/system/runtime/cleanup/preview', data || { dry_run: true }),
  cleanupRuntime: (data: any) => api.post('/system/runtime/cleanup', data),
  snapshot: (force = false) => api.get('/system/snapshot', { params: { force } }),
  frontendStatus: () => api.get('/system/frontend-status'),
}

export const adminMaintenance = {
  health: () => api.get('/admin/health'),
  backups: (params?: { verify_latest?: boolean }) => api.get('/admin/backups', { params }),
  backupDb: () => api.post('/admin/backup-db'),
  verifyBackup: (file: string, includeChecksum = true) => api.post(`/admin/backups/${encodeURIComponent(file)}/verify`, null, { params: { include_checksum: includeChecksum } }),
  deleteBackup: (file: string, confirmText: string, reason?: string) => api.delete(`/admin/backups/${encodeURIComponent(file)}`, { data: { confirm_text: confirmText, reason } }),
  restoreDb: (file: string, confirmText: string, createSafetyBackup = true, reason?: string) => api.post('/admin/restore-db', { file, confirm_text: confirmText, create_safety_backup: createSafetyBackup, reason }),
  backupDownloadUrl: (file: string) => `${getApiBaseUrl()}/admin/backups/${encodeURIComponent(file)}/download`,
  exportConfig: () => api.get('/admin/export-config'),
  importConfig: (data: any) => api.post('/admin/import-config', data),
  importConfigPreview: (data: any) => api.post('/admin/import-config/preview', data),
  importXshell: () => api.post('/admin/import-xshell-sessions'),
  uploadKey: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return api.post('/admin/upload-key', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  sshKeys: {
    list: () => api.get('/admin/ssh-keys'),
    get: (name: string) => api.get(`/admin/ssh-keys/${encodeURIComponent(name)}`),
    create: (data: { name: string; content: string }) => api.post('/admin/ssh-keys', data),
    update: (name: string, data: { new_name?: string; content: string }) => api.put(`/admin/ssh-keys/${encodeURIComponent(name)}`, data),
    delete: (name: string) => api.delete(`/admin/ssh-keys/${encodeURIComponent(name)}`),
  },
}

export const maintenance = {
  sshServerAssets: () => api.get('/maintenance/ssh-server-assets'),
  connections: {
    list: (env?: string) => api.get('/maintenance/connections', { params: { environment: env } }),
    create: (data: any) => api.post('/maintenance/connections', data),
    update: (id: string, data: any) => api.put(`/maintenance/connections/${id}`, data),
    get: (id: string) => api.get(`/maintenance/connections/${id}`),
    delete: (id: string) => api.delete(`/maintenance/connections/${id}`),
    test: (id: string) => api.post(`/maintenance/connections/${id}/test`),
    uploadSshKey: (id: string, content: string) => api.put(`/maintenance/connections/${id}/ssh-key`, { key_content: content }),
    deleteSshKey: (id: string) => api.delete(`/maintenance/connections/${id}/ssh-key`),
    tables: (id: string, databaseName: string) =>
      api.get(`/maintenance/connections/${id}/tables`, { params: { database_name: databaseName } }),
    columns: (id: string, databaseName: string, tableName: string) =>
      api.get(`/maintenance/connections/${id}/columns`, { params: { database_name: databaseName, table_name: tableName } }),
  },
  query: {
    analyze: (connectionId: string, data: any) => api.post(`/maintenance/connections/${connectionId}/query/analyze`, data),
    preview: (connectionId: string, data: any) => api.post(`/maintenance/connections/${connectionId}/query/preview`, data),
    execute: (connectionId: string, data: any) => api.post(`/maintenance/connections/${connectionId}/query/execute`, data),
    history: (connectionId?: string, limit?: number, offset?: number) => api.get('/maintenance/query/history', { params: { connection_id: connectionId, limit, offset } }),
    saved: {
      list: (connectionId?: string, keyword?: string, category?: string) => api.get('/maintenance/query/saved', { params: { connection_id: connectionId, keyword, category } }),
      create: (data: any) => api.post('/maintenance/query/saved', data),
      update: (id: string, data: any) => api.put(`/maintenance/query/saved/${id}`, data),
      delete: (id: string) => api.delete(`/maintenance/query/saved/${id}`),
    },
  },
  jobs: {
    list: (status?: string, limit?: number) => api.get('/maintenance/jobs', { params: { status, limit } }),
    create: (data: any) => api.post('/maintenance/jobs', data),
    get: (id: string) => api.get(`/maintenance/jobs/${id}`),
    dryRun: (id: string) => api.post(`/maintenance/jobs/${id}/dry-run`),
    submit: (id: string) => api.post(`/maintenance/jobs/${id}/submit`),
    approve: (id: string) => api.post(`/maintenance/jobs/${id}/approve`),
    reject: (id: string, reason?: string) => api.post(`/maintenance/jobs/${id}/reject`, { reason }),
    startPlan: (id: string) => api.get(`/maintenance/jobs/${id}/start-plan`),
    start: (id: string, confirmText: string) => api.post(`/maintenance/jobs/${id}/start`, { confirm_text: confirmText }),
    pause: (id: string) => api.post(`/maintenance/jobs/${id}/pause`),
    resume: (id: string) => api.post(`/maintenance/jobs/${id}/resume`),
    cancel: (id: string) => api.post(`/maintenance/jobs/${id}/cancel`),
    batches: (id: string) => api.get(`/maintenance/jobs/${id}/batches`),
    events: (id: string) => api.get(`/maintenance/jobs/${id}/events`),
  },
}

export const serverWorkbench = {
  info: (name: string) => api.get(`/servers/${encodeURIComponent(name)}/info`),
  processes: (name: string) => api.get(`/servers/${encodeURIComponent(name)}/processes`),
  processAction: (name: string, process: string, action: 'start' | 'stop' | 'restart' | 'reload') =>
    api.post(`/servers/${encodeURIComponent(name)}/processes/action`, { process, action }),
  exec: (name: string, command: string, timeout?: number) =>
    api.post(`/servers/${encodeURIComponent(name)}/exec`, { command, timeout }),

  execHistory: (name: string, limit?: number, offset?: number, riskLevel?: string) =>
    api.get(`/servers/${encodeURIComponent(name)}/exec/history`, {
      params: { limit: limit || 50, offset: offset || 0, risk_level: riskLevel },
    }),
  allExecHistory: (limit?: number, offset?: number, serverName?: string, username?: string, riskLevel?: string) =>
    api.get('/servers/exec/history', {
      params: { limit: limit || 50, offset: offset || 0, server_name: serverName, username, risk_level: riskLevel },
    }),
  execHistoryDetail: (logId: string) =>
    api.get(`/servers/exec/history/${logId}`),

  terminalSessionCreate: (name: string, cols?: number, rows?: number) =>
    api.post(`/servers/${encodeURIComponent(name)}/terminal/sessions`, { cols, rows }),
  terminalSessionClose: (name: string, sessionId: string) =>
    api.delete(`/servers/${encodeURIComponent(name)}/terminal/sessions/${sessionId}`),
  terminalSessions: (name: string) => api.get(`/servers/${encodeURIComponent(name)}/terminal/sessions`),

  sftpList: (name: string, path: string, limit?: number, offset?: number) =>
    api.get(`/servers/${encodeURIComponent(name)}/files`, { params: { path, limit, offset } }),
  sftpStat: (name: string, path: string) =>
    api.get(`/servers/${encodeURIComponent(name)}/files/stat`, { params: { path } }),
  sftpMkdir: (name: string, path: string) =>
    api.post(`/servers/${encodeURIComponent(name)}/files/directories`, { path }),
  sftpDelete: (name: string, path: string, confirmPath?: string) =>
    api.delete(`/servers/${encodeURIComponent(name)}/files`, { data: { path, confirm_path: confirmPath || path } }),
  sftpDownload: (name: string, path: string) =>
    api.get(`/servers/${encodeURIComponent(name)}/files/download`, { params: { path } }),
  sftpUpload: (name: string, formData: FormData) =>
    api.post(`/servers/${encodeURIComponent(name)}/files/upload`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),
  sftpTail: (name: string, path: string, lines?: number) =>
    api.get(`/servers/${encodeURIComponent(name)}/files/tail`, { params: { path, lines } }),
  sftpStreamDownloadUrl: (name: string, path: string) =>
    `${getBaseURL()}/servers/${encodeURIComponent(name)}/files/download-stream?path=${encodeURIComponent(path)}`,
  sftpRename: (name: string, oldPath: string, newPath: string) =>
    api.post(`/servers/${encodeURIComponent(name)}/files/rename`, {
      old_path: oldPath,
      new_path: newPath,
    }),
  sftpChmod: (name: string, path: string, mode: string) =>
    api.post(`/servers/${encodeURIComponent(name)}/files/chmod`, { path, mode }),
  sftpContent: (name: string, path: string) =>
    api.get(`/servers/${encodeURIComponent(name)}/files/content`, { params: { path } }),
  sftpContentSave: (name: string, path: string, content: string) =>
    api.put(`/servers/${encodeURIComponent(name)}/files/content`, { path, content }),
}

export const pipelineBinding = {
  metadata: () => api.get('/deploy/pipeline-bindings'),
  resolve: (payload: {
    system?: string; environment?: string
    pipeline_id?: string; runtime_overrides?: Record<string, any>; steps?: any[]
  }) => api.post('/deploy/resolve', payload),
}

export default api

export const taskCenter = {
  list: (params?: { status?: string; kind?: string; limit?: number; offset?: number }) => api.get('/tasks', { params }),
  detail: (kind: string, id: string) => api.get(`/tasks/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`),
}

export const auditLog = {
  list: (params?: { limit?: number; action?: string }) => api.get('/audit', { params }),
  operationChains: (params?: { limit?: number; kind?: string; status?: string; risk?: string }) => api.get('/audit/operation-chains', { params }),
  operationChain: (chainId: string) => api.get(`/audit/operation-chains/${encodeURIComponent(chainId)}`),
  exportUrl: (params?: { limit?: number; action?: string }) => {
    const query = new URLSearchParams()
    if (params?.limit) query.set('limit', String(params.limit))
    if (params?.action) query.set('action', params.action)
    return `${getBaseURL()}/audit/export${query.toString() ? `?${query.toString()}` : ''}`
  },
}

export const capabilityTools = {
  clearCache: () => {
    clearCachedGet('tools.')
    clearCachedGet('mcp.')
    clearCachedGet('capability.')
  },
  list: (params?: { category?: string; risk?: string; include_disabled?: boolean; include_schema?: boolean; limit?: number; cursor?: number; format?: string }) => cachedGet(stableKey('tools.list', params), 60000, () => api.get('/tools', { params })),
  detail: (name: string) => api.get(`/tools/detail/${encodeURIComponent(name)}`),
  capabilities: (params?: { category?: string; include_disabled?: boolean; include_schema?: boolean; limit?: number; cursor?: number; format?: string }) => cachedGet(stableKey('tools.capabilities', params), 60000, () => api.get('/capabilities', { params })),
  riskPolicy: () => cachedGet('tools.riskPolicy', 60000, () => api.get('/tools/risk-policy')),
  policyPreview: (data: any) => api.post('/tools/policy-preview', data),
  call: (tool: string, args?: Record<string, any>) => api.post('/tools/call', { tool, arguments: args || {} }),
  settings: () => api.get('/tools/settings'),
  updateSettings: (settings: Record<string, any>) => api.put('/tools/settings', { settings }),
  tokens: () => api.get('/tools/tokens'),
  tokenTemplates: () => cachedGet('tools.tokenTemplates', 60000, () => api.get('/tools/token-templates')),
  createToken: (data: any) => api.post('/tools/tokens', data),
  updateToken: (id: string, data: any) => api.patch(`/tools/tokens/${encodeURIComponent(id)}`, data),
  revokeToken: (id: string) => api.delete(`/tools/tokens/${encodeURIComponent(id)}`),
  purgeToken: (id: string) => api.delete(`/tools/tokens/${encodeURIComponent(id)}/purge`),
  calls: (params?: { limit?: number; tool?: string; status?: string }) => api.get('/tools/calls', { params }),
  plans: (params?: { limit?: number; plan_type?: string; status?: string }) => api.get('/tools/plans', { params }),
  plan: (id: string) => api.get(`/tools/plans/${encodeURIComponent(id)}`),
  mcpManifest: () => cachedGet('mcp.manifest', 60000, () => api.get('/mcp/manifest')),
  mcpResources: () => cachedGet('mcp.resources', 60000, () => api.get('/mcp/resources')),
  mcpPrompts: () => cachedGet('mcp.prompts', 60000, () => api.get('/mcp/prompts')),
  prompts: () => cachedGet('capability.prompts', 60000, () => api.get('/prompts')),
}

export const reports = {
  list: (params?: { report_type?: string; limit?: number; offset?: number }) => api.get('/reports', { params }),
  summary: () => api.get('/reports/summary'),
  types: () => api.get('/reports/types'),
  generate: (data: { report_type: string; target_id?: string; format?: string; title?: string; include_raw?: boolean; focus?: string }) => api.post('/reports/generate', data),
  get: (id: string) => api.get(`/reports/${encodeURIComponent(id)}`),
  downloadUrl: (id: string) => `${getBaseURL()}/reports/${encodeURIComponent(id)}/download`,
  operationChainExportUrl: (chainId: string, format = 'json') => `${getBaseURL()}/reports/operation-chain/export?chain_id=${encodeURIComponent(chainId)}&format=${encodeURIComponent(format)}`,
  update: (id: string, data: { title?: string; status?: string; summary?: string }) => api.patch(`/reports/${encodeURIComponent(id)}`, data),
  delete: (id: string) => api.delete(`/reports/${encodeURIComponent(id)}`),
}

export const dbTools = {
  tables: (params?: { connection_id?: string; database_name?: string }) => api.get('/db/tables', { params }),
  schema: (tableName: string, params?: { connection_id?: string; database_name?: string }) => api.get(`/db/tables/${encodeURIComponent(tableName)}/schema`, { params }),
  query: (data: { sql: string; connection_id?: string; database_name?: string; limit?: number; timeout_seconds?: number }) => api.post('/db/query', data),
  exportQuery: (data: { sql: string; format?: string; filename_hint?: string; table_name?: string; connection_id?: string; database_name?: string; limit?: number; timeout_seconds?: number }) => api.post('/db/query/export', data),
  exports: (params?: { limit?: number }) => api.get('/db/exports', { params }),
  exportDetail: (id: string) => api.get(`/db/exports/${encodeURIComponent(id)}`),
  downloadUrl: (id: string) => `${getBaseURL()}/db/exports/${encodeURIComponent(id)}/download`,
  deleteExport: (id: string) => api.delete(`/db/exports/${encodeURIComponent(id)}`),
  previewExecute: (data: { sql: string; connection_id?: string; database_name?: string; max_affected_rows?: number; preview_level?: 'fast' | 'standard' | 'full' }) => api.post('/db/execute/preview', data),
  execute: (data: { sql: string; connection_id?: string; database_name?: string; max_affected_rows?: number; confirm_text?: string; reason?: string }) => api.post('/db/execute', data),
  executeHistory: (params?: { connection_id?: string; status?: string; statement_type?: string; limit?: number; offset?: number }) => api.get('/db/execute/history', { params }),
  executeHistoryDetail: (id: string) => api.get(`/db/execute/history/${encodeURIComponent(id)}`),
}

export const rawApi = axios.create({
  headers: { 'Content-Type': 'application/json' },
})

rawApi.interceptors.request.use((config) => {
  config.url = `${getBaseURL()}${config.url || ''}`
  return config
})


export const inspection = {
  overview: () => api.get('/inspection/overview'),
  categories: () => api.get('/inspection/categories'),
  servers: () => api.get('/inspection/servers'),
  serverGroups: () => api.get('/inspection/server-groups'),
  projects: () => api.get('/inspection/projects'),
  profiles: () => api.get('/inspection/profiles'),
  profilePreview: (data: { profile_id: string }) => api.post('/inspection/profiles/preview', data),
  profileRun: (data: { profile_id: string; confirm_text: string; expected_count?: number; fingerprint?: string }) => api.post('/inspection/profiles/run', data),
  profileRetryIssues: (data: { profile_id?: string; risk_level?: string; status?: string; confirm_text?: string; expected_count?: number; fingerprint?: string }) => api.post('/inspection/profiles/retry-issues', data),
  runServer: (data: { server_id: string; categories?: string[]; generate_report?: boolean }) => api.post(`/inspection/servers/${encodeURIComponent(data.server_id)}/run`, data),
  startServer: (data: { server_id: string; categories?: string[] }) => api.post(`/inspection/servers/${encodeURIComponent(data.server_id)}/start`, data),
  runServersBatch: (data: { server_ids?: string[]; groups?: string[]; group?: string; categories?: string[]; generate_report?: boolean; concurrency?: number; batch_size?: number; command_timeout_seconds?: number; run_timeout_seconds?: number; skip_disabled?: boolean; all_servers?: boolean }) => {
    const groups = data.groups || (data.group ? [data.group] : [])
    return api.post('/inspection/servers/batch-run', {
      ...data,
      serverIds: data.server_ids || [],
      groups,
      groupNames: groups,
      categoryCodes: data.categories || [],
      generateReport: data.generate_report,
      batchSize: data.batch_size,
      commandTimeoutSeconds: data.command_timeout_seconds,
      runTimeoutSeconds: data.run_timeout_seconds,
      skipDisabled: data.skip_disabled,
      allServers: data.all_servers,
    })
  },
  startServersBatch: (data: { server_ids?: string[]; groups?: string[]; group?: string; categories?: string[]; concurrency?: number; batch_size?: number; command_timeout_seconds?: number; run_timeout_seconds?: number; skip_disabled?: boolean; all_servers?: boolean }) => {
    const groups = data.groups || (data.group ? [data.group] : [])
    return api.post('/inspection/servers/batch-start', {
      ...data,
      serverIds: data.server_ids || [],
      groups,
      groupNames: groups,
      categoryCodes: data.categories || [],
      batchSize: data.batch_size,
      commandTimeoutSeconds: data.command_timeout_seconds,
      runTimeoutSeconds: data.run_timeout_seconds,
      skipDisabled: data.skip_disabled,
      allServers: data.all_servers,
    })
  },
  runProject: (data: { project_id: string; categories?: string[]; include_server_summary?: boolean; generate_report?: boolean }) => api.post(`/inspection/projects/${encodeURIComponent(data.project_id)}/run`, data),
  startProject: (data: { project_id: string; categories?: string[]; include_server_summary?: boolean }) => api.post(`/inspection/projects/${encodeURIComponent(data.project_id)}/start`, data),
  runCombined: (projectId: string, data: { categories?: string[]; generate_report?: boolean }) => api.post(`/inspection/projects/${encodeURIComponent(projectId)}/combined-run`, data),
  runs: (params?: { scope_type?: string; server_id?: string; project_id?: string; limit?: number; offset?: number }) => api.get('/inspection/runs', { params }),
  ledger: (params?: { period?: string; date_from?: string; date_to?: string; scope_type?: string; server_id?: string; project_id?: string; status?: string; limit?: number; offset?: number }) => api.get('/inspection/ledger', { params }),
  periodicReportPreview: (params?: { period?: string; date_from?: string; date_to?: string; scope_type?: string }) => api.get('/inspection/reports/periodic-preview', { params }),
  generatePeriodicReport: (data: { period?: string; date_from?: string; date_to?: string; scope_type?: string; format?: string; title?: string }) => api.post('/inspection/reports/periodic', data),
  runDetail: (runId: string) => api.get(`/inspection/runs/${encodeURIComponent(runId)}`),
  generateReport: (runId: string, data?: { format?: string; title?: string }) => api.post(`/inspection/runs/${encodeURIComponent(runId)}/report`, data || { format: 'md' }),
  generateReports: (data: { run_ids: string[]; format?: string; title?: string }) => api.post('/inspection/runs/report', data),
  deleteRun: (runId: string, params?: { delete_reports?: boolean; force?: boolean }) => api.delete(`/inspection/runs/${encodeURIComponent(runId)}`, { params }),
  deleteRuns: (data: { run_ids: string[]; delete_reports?: boolean; force?: boolean }) => api.post('/inspection/runs/delete', data),
  deleteLedger: (data: { period?: string; date_from?: string; date_to?: string; scope_type?: string; server_id?: string; project_id?: string; status?: string; delete_reports?: boolean; force?: boolean }) => api.post('/inspection/ledger/delete', data),
  deleteIssue: (issueId: string) => api.delete(`/inspection/issues/${encodeURIComponent(issueId)}`),
  rules: (params?: { scope_type?: string; category?: string; enabled?: boolean; keyword?: string; risk_level?: string; limit?: number; offset?: number }) => api.get('/inspection/rules', { params }),
  createRule: (data: any) => api.post('/inspection/rules', data),
  updateRule: (ruleCode: string, data: any) => api.patch(`/inspection/rules/${encodeURIComponent(ruleCode)}`, data),
  deleteRule: (ruleCode: string) => api.delete(`/inspection/rules/${encodeURIComponent(ruleCode)}`),
  baselines: (params?: { scope_type?: string; server_id?: string; project_id?: string; baseline_type?: string }) => api.get('/inspection/baselines', { params }),
  relations: (params?: { project_id?: string }) => api.get('/inspection/project-server-relations', { params }),
  saveRelation: (data: any) => api.post('/inspection/project-server-relations', data),
  updateRelation: (relationId: string, data: any) => api.patch(`/inspection/project-server-relations/${encodeURIComponent(relationId)}`, data),
  issues: (params?: { scope_type?: string; risk_level?: string; status?: string; server_id?: string; project_id?: string; limit?: number; offset?: number }) => api.get('/inspection/issues', { params }),
  updateIssue: (issueId: string, data: { status?: string; owner_id?: string; suggestion?: string }) => api.patch(`/inspection/issues/${encodeURIComponent(issueId)}`, data),
  // 巡检项目配置管理（可选/可编辑/可调整）
  listItemConfigs: (scopeType: string = 'SERVER') => api.get('/inspection/item-configs', { params: { scope_type: scopeType } }),
  getItemConfig: (itemId: string) => api.get(`/inspection/item-configs/${encodeURIComponent(itemId)}`),
  updateItemConfig: (itemId: string, data: any) => api.put(`/inspection/item-configs/${encodeURIComponent(itemId)}`, data),
  toggleItemConfig: (itemId: string) => api.post(`/inspection/item-configs/${encodeURIComponent(itemId)}/toggle`),
  reorderItemConfigs: (data: { scope_type: string; ordered_ids: string[] }) => api.put('/inspection/item-configs/reorder', data),
  updateItemConfigRules: (itemId: string, rules: any[]) => api.put(`/inspection/item-configs/${encodeURIComponent(itemId)}/rules`, { rules }),
  getThresholds: () => api.get('/inspection/thresholds'),
  saveThresholds: (category: string, thresholds: Record<string, any>) => api.post('/inspection/thresholds', { category, thresholds }),
  getRunRawOutput: (runId: string) => api.get(`/inspection/runs/${encodeURIComponent(runId)}/raw-output`),
}

export const mcpAi = {
  tools: (params?: { category?: string; risk?: string; include_schema?: boolean; limit?: number; cursor?: number }) => api.get('/tools', { params }),
  toolDetail: (name: string) => api.get(`/tools/detail/${encodeURIComponent(name)}`),
  toolCalls: (params?: { limit?: number; tool?: string; status?: string }) => api.get('/tools/calls', { params }),
  resources: () => api.get('/mcp/resources'),
  readResource: (uri: string) => api.post('/mcp/resources/read', { uri }),
  workflow: (tool: string, arguments_: Record<string, any>) => api.post('/tools/call', { tool, arguments: arguments_ }),
  recommendTools: (scenario: string) => api.get('/mcp/tools/recommend', { params: { scenario } }),
}

export const aiAnalysis = {
  list: (params?: { analysis_type?: string; target_type?: string; target_id?: string; limit?: number }) => api.get('/ai/analysis', { params }),
  get: (id: string) => api.get(`/ai/analysis/${encodeURIComponent(id)}`),
  save: (data: any) => api.post('/ai/analysis', data),
  generateReport: (id: string) => api.post(`/ai/analysis/${encodeURIComponent(id)}/generate-report`),
}
