import { useCallback, useEffect, useRef } from 'react'

type PollingCallback = () => Promise<void> | void

interface SmartPollingOptions {
  activeMs?: number
  hiddenMs?: number
  idleMs?: number
  maxBackoffMs?: number
  pauseWhenHidden?: boolean
  runOnVisible?: boolean
}

interface StartOptions {
  activeMs?: number
  hiddenMs?: number
  idleMs?: number
}

/**
 * Small local-team polling helper: one in-flight request at a time, visibility-aware,
 * exponential backoff after failures, and automatic cleanup on unmount.
 */
export function useSmartPolling(callback: PollingCallback, options: SmartPollingOptions = {}) {
  const callbackRef = useRef(callback)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const runningRef = useRef(false)
  const inFlightRef = useRef(false)
  const failureCountRef = useRef(0)
  const optionRef = useRef({
    activeMs: options.activeMs ?? 2000,
    hiddenMs: options.hiddenMs ?? 15000,
    idleMs: options.idleMs ?? 30000,
    maxBackoffMs: options.maxBackoffMs ?? 30000,
    pauseWhenHidden: options.pauseWhenHidden ?? false,
    runOnVisible: options.runOnVisible ?? true,
  })

  useEffect(() => { callbackRef.current = callback }, [callback])
  useEffect(() => {
    optionRef.current = {
      activeMs: options.activeMs ?? 2000,
      hiddenMs: options.hiddenMs ?? 15000,
      idleMs: options.idleMs ?? 30000,
      maxBackoffMs: options.maxBackoffMs ?? 30000,
      pauseWhenHidden: options.pauseWhenHidden ?? false,
      runOnVisible: options.runOnVisible ?? true,
    }
  }, [options.activeMs, options.hiddenMs, options.idleMs, options.maxBackoffMs, options.pauseWhenHidden, options.runOnVisible])

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const nextDelay = useCallback((override?: StartOptions) => {
    const cfg = { ...optionRef.current, ...(override || {}) }
    const hidden = typeof document !== 'undefined' && document.hidden
    const base = hidden ? cfg.hiddenMs : cfg.activeMs
    const backoff = Math.min(cfg.maxBackoffMs, base * Math.max(1, 2 ** Math.max(0, failureCountRef.current - 1)))
    return Math.max(250, backoff)
  }, [])

  const schedule = useCallback((override?: StartOptions) => {
    clearTimer()
    if (!runningRef.current) return
    const cfg = { ...optionRef.current, ...(override || {}) }
    const hidden = typeof document !== 'undefined' && document.hidden
    if (hidden && cfg.pauseWhenHidden) return
    timerRef.current = setTimeout(async () => {
      if (!runningRef.current || inFlightRef.current) {
        schedule(override)
        return
      }
      inFlightRef.current = true
      try {
        await callbackRef.current()
        failureCountRef.current = 0
      } catch (error) {
        failureCountRef.current += 1
        console.error(error)
      } finally {
        inFlightRef.current = false
        schedule(override)
      }
    }, nextDelay(override))
  }, [clearTimer, nextDelay])

  const start = useCallback((override?: StartOptions) => {
    runningRef.current = true
    failureCountRef.current = 0
    schedule(override)
  }, [schedule])

  const stop = useCallback(() => {
    runningRef.current = false
    failureCountRef.current = 0
    clearTimer()
  }, [clearTimer])

  const runNow = useCallback(async () => {
    if (inFlightRef.current) return
    inFlightRef.current = true
    try {
      await callbackRef.current()
      failureCountRef.current = 0
    } catch (error) {
      failureCountRef.current += 1
      throw error
    } finally {
      inFlightRef.current = false
    }
  }, [])

  useEffect(() => {
    const onVisibility = () => {
      if (!runningRef.current) return
      clearTimer()
      if (!document.hidden && optionRef.current.runOnVisible) {
        void runNow().finally(() => schedule())
      } else {
        schedule()
      }
    }
    if (typeof document === 'undefined') return undefined
    document.addEventListener('visibilitychange', onVisibility)
    return () => document.removeEventListener('visibilitychange', onVisibility)
  }, [clearTimer, runNow, schedule])

  useEffect(() => () => stop(), [stop])

  return { start, stop, runNow, isRunning: () => runningRef.current }
}
