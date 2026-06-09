import { useCallback, useEffect, useRef, useState } from 'react'
import { deploy, deployment } from '../../api'
import type { DeploymentLogEntry as LogEntry } from '../../types/deploy'

export type { LogEntry }

const FINAL_STATUSES = new Set(['success', 'failed', 'partial_failed', 'canceled', 'cancelled'])
const SSE_FALLBACK_TIMEOUT_MS = 5000

function getApiBaseUrl(): string {
  return import.meta.env.VITE_API_BASE_URL || '/api/v2'
}

export function useDeploymentStream() {
  const [taskId, setTaskId] = useState('')
  const [deploymentId, setDeploymentId] = useState('')
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [status, setStatus] = useState('')
  const [taskDetails, setTaskDetails] = useState<any>(null)

  const deploymentIdRef = useRef('')
  const eventSourceRef = useRef<EventSource | null>(null)
  const pollingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const isPollingRef = useRef(false)
  const sseFallbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const stoppedRef = useRef(false)
  const maxLogBuffer = Number(import.meta.env.VITE_MAX_LOG_BUFFER_LINES || 500)

  useEffect(() => {
    deploymentIdRef.current = deploymentId
  }, [deploymentId])

  const stopPolling = useCallback(() => {
    if (pollingTimerRef.current) {
      clearInterval(pollingTimerRef.current)
      pollingTimerRef.current = null
    }
    isPollingRef.current = false
  }, [])

  const startPolling = useCallback((tid: string) => {
    isPollingRef.current = true
    const intervalMs = Number(import.meta.env.VITE_DEPLOY_POLL_ACTIVE_MS || 2000)

    const doPoll = async () => {
      if (stoppedRef.current || !isPollingRef.current) return
      try {
        const limit = Math.max(50, Math.min(2000,maxLogBuffer))
        const res: any = await deploy.logs(tid, { limit })
        if (res.status === 304 || stoppedRef.current) return

        const nextLogs: LogEntry[] = res.data.logs || []
        setLogs((prev) => {
          const merged = [...prev]
          for (const entry of nextLogs) {
            merged.push(entry)
          }
          return merged.slice(-maxLogBuffer)
        })

        const taskRes: any = await deploy.task(tid)
        if (stoppedRef.current) return
        const nextStatus = taskRes.data.status
        setStatus(nextStatus)

        const depId = taskRes.data.deployment_id || deploymentIdRef.current
        if (depId) {
          setDeploymentId(depId)
          try {
            const detailRes: any = await deployment.tasks(depId)
            if (!stoppedRef.current) setTaskDetails(detailRes.data || null)
          } catch { /* best-effort */ }
        }

        if (FINAL_STATUSES.has(nextStatus)) {
          stopPolling()
        }
      } catch {
        // polling errors are swallowed; next interval will retry
      }
    }

    doPoll()
    pollingTimerRef.current = setInterval(doPoll, intervalMs)
  }, [maxLogBuffer, stopPolling])

  const connectSSE = useCallback((tid: string, depId: string) => {
    const baseUrl = getApiBaseUrl()
    const url = `${baseUrl}/deploy/deployments/${encodeURIComponent(depId)}/stream`
    const es = new EventSource(url, { withCredentials: true })
    eventSourceRef.current = es

    es.addEventListener('log', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data)
        setLogs((prev) => {
          const merged = [...prev, data]
          return merged.slice(-maxLogBuffer)
        })
      } catch { /* malformed, ignore */ }
    })

    es.addEventListener('status', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data)
        if (data.status) {
          setStatus(data.status)
          if (FINAL_STATUSES.has(data.status)) {
            return
          }
        }
      } catch { /* ignore */ }
    })

    es.addEventListener('done', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data)
        if (data.final_status) {
          setStatus(data.final_status)
        }
      } catch { /* ignore */ }
      es.close()
      eventSourceRef.current = null
    })

    es.onerror = () => {
      es.close()
      eventSourceRef.current = null
      if (!stoppedRef.current && !FINAL_STATUSES.has(status)) {
        startPolling(tid)
      }
    }
  }, [maxLogBuffer, startPolling, status])

  const disconnectSSE = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close()
      eventSourceRef.current = null
    }
    if (sseFallbackTimerRef.current) {
      clearTimeout(sseFallbackTimerRef.current)
      sseFallbackTimerRef.current = null
    }
  }, [])

  const clearRun = useCallback((nextStatus: string = '') => {
    stoppedRef.current = true
    disconnectSSE()
    stopPolling()
    setTaskId('')
    setDeploymentId('')
    deploymentIdRef.current = ''
    setLogs([])
    setStatus(nextStatus)
    setTaskDetails(null)
  }, [disconnectSSE, stopPolling])

  const startRun = useCallback(async (tid: string) => {
    clearRun()
    stoppedRef.current = false
    setLogs([])
    setStatus('')
    setTaskDetails(null)
    setTaskId(tid)

    const loadInitial = async () => {
      try {
        const taskRes: any = await deploy.task(tid)
        if (stoppedRef.current) return
        const depId: string = taskRes.data.deployment_id || ''
        const initialStatus: string = taskRes.data.status
        setStatus(initialStatus)
        if (depId) {
          setDeploymentId(depId)
          try {
            const detailRes: any = await deployment.tasks(depId)
            if (!stoppedRef.current) setTaskDetails(detailRes.data || null)
          } catch { /* best-effort */ }
        }

        if (FINAL_STATUSES.has(initialStatus)) {
          const logRes: any = await deploy.logs(tid, { limit: maxLogBuffer })
          if (!stoppedRef.current) {
            setLogs((logRes.data.logs || []).slice(-maxLogBuffer))
          }
          return
        }

        if (depId && !FINAL_STATUSES.has(initialStatus)) {
          connectSSE(tid, depId)

          sseFallbackTimerRef.current = setTimeout(() => {
            if (eventSourceRef.current && eventSourceRef.current.readyState !== EventSource.OPEN) {
              disconnectSSE()
              if (!stoppedRef.current) startPolling(tid)
            }
          }, SSE_FALLBACK_TIMEOUT_MS)
        } else {
          startPolling(tid)
        }
      } catch {
        startPolling(tid)
      }
    }

    loadInitial()
  }, [clearRun, maxLogBuffer, connectSSE, disconnectSSE, startPolling])

  useEffect(() => () => {
    stoppedRef.current = true
    disconnectSSE()
    stopPolling()
  }, [disconnectSSE, stopPolling])

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
    fetchLogs: startRun,
    startPolling: startRun,
    clearPolling: clearRun,
    resetRunState: clearRun,
  }
}
