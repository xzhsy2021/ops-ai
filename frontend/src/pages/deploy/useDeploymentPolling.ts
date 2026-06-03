import { useCallback, useEffect, useRef, useState } from 'react'
import { deploy, deployment } from '../../api'
import { useSmartPolling } from '../../hooks/useSmartPolling'
import type { DeploymentLogEntry as LogEntry } from '../../types/deploy'

export type { LogEntry }

const FINAL_STATUSES = new Set(['success', 'failed', 'canceled', 'cancelled'])

export function useDeploymentPolling() {
  const [taskId, setTaskId] = useState('')
  const [deploymentId, setDeploymentId] = useState('')
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [status, setStatus] = useState('')
  const [taskDetails, setTaskDetails] = useState<any>(null)

  const fetchCountRef = useRef(0)
  const deploymentIdRef = useRef('')
  const pollingTaskRef = useRef('')
  const stopPollingRef = useRef<() => void>(() => undefined)
  const maxLogBuffer = Number(import.meta.env.VITE_MAX_LOG_BUFFER_LINES || 500)
  const lastCursorRef = useRef<string | undefined>(undefined)
  const lastEtagRef = useRef<string | undefined>(undefined)

  useEffect(() => {
    deploymentIdRef.current = deploymentId
  }, [deploymentId])

  const fetchLogs = useCallback(async (tid: string) => {
    const currentFetch = ++fetchCountRef.current
    const limit = Math.max(50, Math.min(2000, maxLogBuffer))
    const res: any = await deploy.logs(tid, {
      limit,
      cursor: lastCursorRef.current,
      etag: lastEtagRef.current,
    })
    if (currentFetch !== fetchCountRef.current) return
    if (res.status === 304) return

    const nextLogs: LogEntry[] = res.data.logs || []
    const nextCursor: string | undefined = res.data.next_cursor
    if (nextCursor) {
      lastCursorRef.current = nextCursor
    }
    const etagHeader = res.headers?.['etag'] || res.headers?.['ETag']
    if (etagHeader) {
      lastEtagRef.current = etagHeader
    } else {
      lastEtagRef.current = undefined
    }
    setLogs((prev) => {
      const merged = [...prev]
      for (const entry of nextLogs) {
        merged.push(entry)
      }
      return merged.slice(-maxLogBuffer)
    })

    const taskRes: any = await deploy.task(tid)
    if (currentFetch !== fetchCountRef.current) return
    const nextStatus = taskRes.data.status
    setStatus(nextStatus)

    const depId = taskRes.data.deployment_id || deploymentIdRef.current
    if (depId) {
      setDeploymentId(depId)
      try {
        const detailRes: any = await deployment.tasks(depId)
        if (currentFetch === fetchCountRef.current) setTaskDetails(detailRes.data || null)
      } catch {
        // Task details are best-effort; logs/status remain useful if this fails.
      }
    }

    if (FINAL_STATUSES.has(nextStatus)) {
      pollingTaskRef.current = ''
      stopPollingRef.current()
    }
  }, [maxLogBuffer])

  const smartPolling = useSmartPolling(async () => {
    const tid = pollingTaskRef.current
    if (!tid) return
    await fetchLogs(tid)
  }, {
    activeMs: Number(import.meta.env.VITE_DEPLOY_POLL_ACTIVE_MS || 2000),
    hiddenMs: Number(import.meta.env.VITE_DEPLOY_POLL_HIDDEN_MS || 15000),
    maxBackoffMs: 30000,
  })

  useEffect(() => {
    stopPollingRef.current = smartPolling.stop
  }, [smartPolling.stop])

  const clearPolling = useCallback(() => {
    pollingTaskRef.current = ''
    smartPolling.stop()
  }, [smartPolling.stop])

  const startPolling = useCallback((tid: string) => {
    clearPolling()
    lastCursorRef.current = undefined
    lastEtagRef.current = undefined
    pollingTaskRef.current = tid
    void fetchLogs(tid).catch((e) => console.error(e))
    smartPolling.start()
  }, [fetchLogs, clearPolling, smartPolling.start])

  const resetRunState = useCallback((nextStatus: string = '') => {
    pollingTaskRef.current = ''
    smartPolling.stop()
    setLogs([])
    setStatus(nextStatus)
    setDeploymentId('')
    deploymentIdRef.current = ''
    setTaskDetails(null)
    lastCursorRef.current = undefined
    lastEtagRef.current = undefined
  }, [smartPolling.stop])

  useEffect(() => () => smartPolling.stop(), [smartPolling.stop])

  return {
    taskId,
    setTaskId,
    deploymentId,
    setDeploymentId,
    logs,
    setLogs,
    status,
    setStatus,
    taskDetails,
    setTaskDetails,
    fetchLogs,
    startPolling,
    clearPolling,
    resetRunState,
  }
}
