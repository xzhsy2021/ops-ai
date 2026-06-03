import { useCallback, useEffect, useState } from 'react'
import { adminMaintenance, maintenance } from '../../api'

export const defaultConnForm = { id: '', name: '', description: '', environment: 'dev', db_type: 'mysql', host: '', port: 3306, username: '', password: '', database_name: '', use_ssh_tunnel: false, ssh_mode: 'manual' as 'manual' | 'server', ssh_server_name: '', ssh_host: '', ssh_port: 22, ssh_username: '', ssh_password: '', ssh_key_path: '', ssh_key_content: '', ssh_key_passphrase: '', ssh_remote_bind_host: '', ssh_target_server_name: '', ssh_target_host: '', ssh_target_port: 22, ssh_target_username: '', ssh_target_password: '', ssh_target_key_path: '', ssh_target_key_passphrase: '', allow_dml: false, allowed_dml_types: ['insert', 'update', 'delete'], allowed_tables_text: '', blocked_tables_text: '', max_affected_rows_default: 100, require_dml_reason: true }
export const defaultJobForm = { name: '', environment: 'dev', connection_id: '', connection_name: '', database_name: '', table_name: '', date_column: '', cutoff_time: '', batch_size: 100000, batch_interval_seconds: 3, max_delete_rows: '', approval_required: true }

export function useMaintenanceData(args: { isAdmin: boolean; mainTab: 'maintenance' | 'cleanup' | 'sql'; cleanupTab: 'connections' | 'jobs' }) {
  const { isAdmin, mainTab, cleanupTab } = args

  const [health, setHealth] = useState<any>(null)
  const [backups, setBackups] = useState<any[]>([])
  const [message, setMessage] = useState('')
  const [errorMsg, setErrorMsg] = useState('')
  const [xshellResult, setXshellResult] = useState<any>(null)
  const [xshellLoading, setXshellLoading] = useState(false)

  const [connections, setConnections] = useState<any[]>([])
  const [connLoading, setConnLoading] = useState(false)
  const [connSearch, setConnSearch] = useState('')
  const [connEnvFilter, setConnEnvFilter] = useState('')
  const [showConnForm, setShowConnForm] = useState(false)
  const [connForm, setConnForm] = useState({ ...defaultConnForm })
  const [connSubmitting, setConnSubmitting] = useState(false)

  const [jobs, setJobs] = useState<any[]>([])
  const [jobsLoading, setJobsLoading] = useState(false)
  const [jobSearch, setJobSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [showJobForm, setShowJobForm] = useState(false)
  const [jobForm, setJobForm] = useState({ ...defaultJobForm })
  const [jobSubmitting, setJobSubmitting] = useState(false)
  const [tableOptions, setTableOptions] = useState<string[]>([])
  const [columnOptions, setColumnOptions] = useState<string[]>([])
  const [tablesLoading, setTablesLoading] = useState(false)
  const [columnsLoading, setColumnsLoading] = useState(false)

  const [selectedJob, setSelectedJob] = useState<any>(null)
  const [jobDetail, setJobDetail] = useState<any>(null)
  const [jobBatches, setJobBatches] = useState<any[]>([])
  const [jobEvents, setJobEvents] = useState<any[]>([])
  const [dryRunResult, setDryRunResult] = useState<any>(null)
  const [dryRunLoading, setDryRunLoading] = useState(false)
  const [confirmText, setConfirmText] = useState('')
  const [startPlan, setStartPlan] = useState<any>(null)
  const [actionLoading, setActionLoading] = useState(false)

  const loadConnections = useCallback(() => {
    setConnLoading(true)
    maintenance.connections.list().then((res: any) => setConnections(res.data || [])).catch(() => {}).finally(() => setConnLoading(false))
  }, [])

  const loadJobs = useCallback((status?: string) => {
    setJobsLoading(true)
    maintenance.jobs.list(status || undefined).then((res: any) => setJobs(res.data || [])).catch(() => {}).finally(() => setJobsLoading(false))
  }, [])

  const refreshBackups = useCallback(() => {
    adminMaintenance.backups({ verify_latest: true }).then((res: any) => setBackups(res.data || [])).catch(() => {})
  }, [])

  const refreshHealth = useCallback(() => {
    adminMaintenance.health().then((res: any) => setHealth(res.data)).catch(() => {})
  }, [])

  useEffect(() => {
    if (!isAdmin && mainTab !== 'sql') return
    if (mainTab === 'maintenance') {
      refreshHealth()
      refreshBackups()
      return
    }
    if (mainTab === 'cleanup') {
      loadConnections()
      loadJobs()
      return
    }
    if (mainTab === 'sql') {
      loadConnections()
    }
  }, [isAdmin, mainTab, refreshHealth, refreshBackups, loadConnections, loadJobs])

  useEffect(() => {
    if (mainTab === 'cleanup') {
      if (cleanupTab === 'connections') loadConnections()
      if (cleanupTab === 'jobs') loadJobs(statusFilter || undefined)
    }
  }, [mainTab, cleanupTab, loadConnections, loadJobs, statusFilter])

  return {
    health,
    backups,
    message,
    setMessage,
    errorMsg,
    setErrorMsg,
    xshellResult,
    setXshellResult,
    xshellLoading,
    setXshellLoading,
    connections,
    connLoading,
    connSearch,
    setConnSearch,
    connEnvFilter,
    setConnEnvFilter,
    showConnForm,
    setShowConnForm,
    connForm,
    setConnForm,
    connSubmitting,
    setConnSubmitting,
    jobs,
    jobsLoading,
    jobSearch,
    setJobSearch,
    statusFilter,
    setStatusFilter,
    showJobForm,
    setShowJobForm,
    jobForm,
    setJobForm,
    jobSubmitting,
    setJobSubmitting,
    tableOptions,
    setTableOptions,
    columnOptions,
    setColumnOptions,
    tablesLoading,
    setTablesLoading,
    columnsLoading,
    setColumnsLoading,
    selectedJob,
    setSelectedJob,
    jobDetail,
    setJobDetail,
    jobBatches,
    setJobBatches,
    jobEvents,
    setJobEvents,
    dryRunResult,
    setDryRunResult,
    dryRunLoading,
    setDryRunLoading,
    confirmText,
    setConfirmText,
    startPlan,
    setStartPlan,
    actionLoading,
    setActionLoading,
    loadConnections,
    loadJobs,
    refreshBackups,
    refreshHealth,
  }
}
