import { useCallback } from 'react'
import type { Dispatch, SetStateAction } from 'react'
import { maintenance } from '../../api'
import { defaultConnForm, defaultJobForm } from './useMaintenanceData'

type UseCleanupJobActionsArgs = {
  connections: any[]
  connForm: any
  setConnForm: Dispatch<SetStateAction<any>>
  setConnSubmitting: (value: boolean) => void
  setShowConnForm: (value: boolean) => void
  jobForm: any
  setJobForm: Dispatch<SetStateAction<any>>
  setJobSubmitting: (value: boolean) => void
  setShowJobForm: (value: boolean) => void
  statusFilter: string
  selectedJob: any
  setSelectedJob: (job: any) => void
  setJobDetail: (job: any) => void
  setJobBatches: (items: any[]) => void
  setJobEvents: (items: any[]) => void
  setDryRunResult: (value: any) => void
  setDryRunLoading: (value: boolean) => void
  confirmText: string
  setConfirmText: (value: string) => void
  startPlan: any
  setStartPlan: (value: any) => void
  setActionLoading: (value: boolean) => void
  setTableOptions: (items: string[]) => void
  setColumnOptions: (items: string[]) => void
  setTablesLoading: (value: boolean) => void
  setColumnsLoading: (value: boolean) => void
  loadConnections: () => void
  loadJobs: (status?: string) => void
  showMsg: (message: string) => void
  showErr: (message: string) => void
  requestConfirm: (config: { title: string; description?: string; confirmLabel?: string; danger?: boolean }) => Promise<boolean>
}

function errorMessage(e: any, fallback: string) {
  return typeof e === 'string' ? e : e?.response?.data?.detail || e?.message || fallback
}

function normalizePolicyList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String).map(x => x.trim()).filter(Boolean)
  return String(value || '').split(/[\n,，]+/).map(x => x.trim()).filter(Boolean)
}

export function useCleanupJobActions(args: UseCleanupJobActionsArgs) {
  const {
    connections,
    connForm,
    setConnForm,
    setConnSubmitting,
    setShowConnForm,
    jobForm,
    setJobForm,
    setJobSubmitting,
    setShowJobForm,
    statusFilter,
    selectedJob,
    setSelectedJob,
    setJobDetail,
    setJobBatches,
    setJobEvents,
    setDryRunResult,
    setDryRunLoading,
    confirmText,
    setConfirmText,
    startPlan,
    setStartPlan,
    setActionLoading,
    setTableOptions,
    setColumnOptions,
    setTablesLoading,
    setColumnsLoading,
    loadConnections,
    loadJobs,
    showMsg,
    showErr,
    requestConfirm,
  } = args

  const handleConnSubmit = useCallback(async () => {
    const sshRequired = connForm.use_ssh_tunnel && connForm.ssh_mode === 'manual'
    if (!connForm.name || !connForm.host || !connForm.database_name || (sshRequired && (!connForm.ssh_host || !connForm.ssh_username))) {
      showErr(sshRequired ? '请填写数据库连接和 SSH 跳板机必填字段' : '请填写必填字段')
      return
    }
    setConnSubmitting(true)
    const isEditing = Boolean(connForm.id)
    try {
      const payload: any = {
        ...connForm,
        allowed_dml_types: normalizePolicyList(connForm.allowed_dml_types),
        allowed_tables: normalizePolicyList(connForm.allowed_tables_text),
        blocked_tables: normalizePolicyList(connForm.blocked_tables_text),
        max_affected_rows_default: Number(connForm.max_affected_rows_default || 100),
      }
      // Remove client-only fields
      delete payload.ssh_key_content
      delete payload.allowed_tables_text
      delete payload.blocked_tables_text
      delete payload.id
      delete payload.created_at
      delete payload.updated_at
      delete payload.ssh_key_has_content
      if (isEditing) {
        await maintenance.connections.update(connForm.id, payload)
        showMsg('连接已更新')
      } else {
        await maintenance.connections.create(payload)
        showMsg('连接已创建')
      }
      setShowConnForm(false)
      setConnForm({ ...defaultConnForm })
      loadConnections()
    } catch (e: any) {
      showErr(errorMessage(e, isEditing ? '更新失败' : '创建失败'))
    }
    setConnSubmitting(false)
  }, [connForm, setConnSubmitting, setShowConnForm, setConnForm, loadConnections, showMsg, showErr])

  const handleConnDelete = useCallback(async (id: string, name: string) => {
    const ok = await requestConfirm({ title: '删除数据库连接', description: `确认删除连接 \"${name}\"？SQL 查询、执行和清理任务将无法继续使用该连接。`, confirmLabel: '删除连接', danger: true })
    if (!ok) return
    try {
      await maintenance.connections.delete(id)
      showMsg('连接已删除')
      loadConnections()
    } catch (e: any) {
      showErr(errorMessage(e, '删除失败'))
    }
  }, [loadConnections, showMsg, showErr, requestConfirm])

  const handleConnTest = useCallback(async (id: string) => {
    try {
      const res: any = await maintenance.connections.test(id)
      showMsg(res?.message || '连接测试成功')
    } catch (e: any) {
      showErr(errorMessage(e, '连接测试失败'))
    }
  }, [showMsg, showErr])

  const handleJobFormConnChange = useCallback(async (connId: string) => {
    const conn = connections.find((c: any) => c.id === connId)
    setJobForm((f: any) => ({ ...f, connection_id: connId, connection_name: conn?.name || '', environment: conn?.environment || f.environment, database_name: f.database_name || conn?.database_name || '', table_name: '', date_column: '' }))
    setTableOptions([])
    setColumnOptions([])
    if (!connId) return
    setTablesLoading(true)
    try {
      const selectedConn = connections.find((c: any) => c.id === connId)
      const dbName = jobForm.database_name || selectedConn?.database_name || ''
      if (!dbName) { setTablesLoading(false); return }
      const res: any = await maintenance.connections.tables(connId, dbName)
      setTableOptions(res.data || [])
    } catch {}
    setTablesLoading(false)
  }, [connections, jobForm.database_name, setJobForm, setTableOptions, setColumnOptions, setTablesLoading])

  const handleJobFormTableChange = useCallback(async (tableName: string) => {
    setJobForm((f: any) => ({ ...f, table_name: tableName, date_column: '' }))
    setColumnOptions([])
    if (!tableName || !jobForm.connection_id) return
    setColumnsLoading(true)
    try {
      const conn = connections.find((c: any) => c.id === jobForm.connection_id)
      const dbName = jobForm.database_name || conn?.database_name || ''
      const res: any = await maintenance.connections.columns(jobForm.connection_id, dbName, tableName)
      setColumnOptions((res.data || []).map((c: any) => c.Field || c.name || c.column_name || String(c)))
    } catch {}
    setColumnsLoading(false)
  }, [connections, jobForm.connection_id, jobForm.database_name, setJobForm, setColumnOptions, setColumnsLoading])

  const handleJobSubmit = useCallback(async () => {
    if (!jobForm.name || !jobForm.connection_id || !jobForm.table_name || !jobForm.date_column || !jobForm.cutoff_time) {
      showErr('请填写必填字段')
      return
    }
    setJobSubmitting(true)
    try {
      const payload: any = {
        ...jobForm,
        max_delete_rows: jobForm.max_delete_rows ? Number(jobForm.max_delete_rows) : undefined,
      }
      await maintenance.jobs.create(payload)
      showMsg('数据清理任务已创建')
      setShowJobForm(false)
      setJobForm({ ...defaultJobForm })
      setTableOptions([])
      setColumnOptions([])
      loadJobs(statusFilter || undefined)
    } catch (e: any) {
      showErr(errorMessage(e, '创建失败'))
    }
    setJobSubmitting(false)
  }, [jobForm, statusFilter, setJobSubmitting, setShowJobForm, setJobForm, setTableOptions, setColumnOptions, loadJobs, showMsg, showErr])

  const openJobDetail = useCallback(async (job: any) => {
    setSelectedJob(job)
    setDryRunResult(null)
    setConfirmText('')
    setStartPlan(null)
    try {
      const [detailRes, batchesRes, eventsRes]: any[] = await Promise.all([
        maintenance.jobs.get(job.id),
        maintenance.jobs.batches(job.id),
        maintenance.jobs.events(job.id),
      ])
      const detail = detailRes.data
      setJobDetail(detail)
      setDryRunResult(detail?.dry_run || null)
      setJobBatches(batchesRes.data || [])
      setJobEvents(eventsRes.data || [])
      if (detail?.status === 'approved') {
        try {
          const planRes: any = await maintenance.jobs.startPlan(job.id)
          setStartPlan(planRes.data || null)
        } catch {
          setStartPlan(null)
        }
      }
    } catch {
      setJobDetail(job)
      setJobBatches([])
      setJobEvents([])
    }
  }, [setSelectedJob, setDryRunResult, setConfirmText, setStartPlan, setJobDetail, setJobBatches, setJobEvents])

  const handleDryRun = useCallback(async () => {
    if (!selectedJob) return
    setDryRunLoading(true)
    try {
      const res: any = await maintenance.jobs.dryRun(selectedJob.id)
      setDryRunResult(res.data)
      showMsg('Dry Run 完成')
    } catch (e: any) {
      showErr(errorMessage(e, 'Dry Run 失败'))
    }
    setDryRunLoading(false)
  }, [selectedJob, setDryRunLoading, setDryRunResult, showMsg, showErr])

  const handleJobAction = useCallback(async (action: string) => {
    if (!selectedJob) return
    setActionLoading(true)
    try {
      switch (action) {
        case 'submit':
          await maintenance.jobs.submit(selectedJob.id)
          break
        case 'approve':
          await maintenance.jobs.approve(selectedJob.id)
          break
        case 'reject':
          await maintenance.jobs.reject(selectedJob.id)
          break
        case 'start': {
          const plan = startPlan || await maintenance.jobs.startPlan(selectedJob.id).then((res: any) => res.data).catch(() => null)
          const confirm = plan?.confirm_text || `CLEAN ${selectedJob.table_name} BEFORE ${selectedJob.cutoff_time}`
          const summary = plan?.summary || {}
          const ok = await requestConfirm({
            title: '开始数据库清理执行',
            description: `确认对 ${selectedJob.connection_name || selectedJob.database_name}.${selectedJob.table_name} 执行分批清理？匹配 ${summary.matched_rows ?? selectedJob.matched_rows ?? '-'} 行，保护阈值 ${summary.max_delete_rows ?? selectedJob.max_delete_rows ?? '未设置'}。`,
            confirmLabel: '开始执行',
            danger: true,
          })
          if (!ok) { setActionLoading(false); return }
          await maintenance.jobs.start(selectedJob.id, confirm)
          break
        }
        case 'pause':
          await maintenance.jobs.pause(selectedJob.id)
          break
        case 'resume': {
          const ok = await requestConfirm({ title: '继续数据库清理执行', description: `确认继续执行任务 ${selectedJob.name || selectedJob.id}？系统会继续按批次删除匹配数据，并记录批次日志。`, confirmLabel: '继续执行', danger: true })
          if (!ok) { setActionLoading(false); return }
          await maintenance.jobs.resume(selectedJob.id)
          break
        }
        case 'cancel': {
          const ok = await requestConfirm({ title: '取消数据清理任务', description: `确认取消任务 ${selectedJob.name || selectedJob.id}？运行中的任务会停止后续批次，已执行批次不会回滚。`, confirmLabel: '取消任务', danger: true })
          if (!ok) { setActionLoading(false); return }
          await maintenance.jobs.cancel(selectedJob.id)
          break
        }
        default:
          break
      }
      showMsg('操作成功')
      loadJobs(statusFilter || undefined)
      await openJobDetail(selectedJob)
    } catch (e: any) {
      showErr(errorMessage(e, '操作失败'))
    }
    setActionLoading(false)
  }, [selectedJob, startPlan, confirmText, statusFilter, setActionLoading, loadJobs, openJobDetail, showMsg, showErr, requestConfirm])

  return {
    handleConnSubmit,
    handleConnDelete,
    handleConnTest,
    handleJobFormConnChange,
    handleJobFormTableChange,
    handleJobSubmit,
    openJobDetail,
    handleDryRun,
    handleJobAction,
  }
}
