import { useState, useCallback, useRef, useEffect } from 'react'

type CachedResourceOptions<T> = {
  fetcher: () => Promise<T>
  key?: string
  ttlMs?: number
  enabled?: boolean
  onError?: (error: unknown) => void
}

type CachedResourceState<T> = {
  data: T | null
  error: string | null
  loading: boolean
  stale: boolean
}

export function useCachedResource<T>({
  fetcher,
  key,
  ttlMs = 30000,
  enabled = true,
  onError,
}: CachedResourceOptions<T>) {
  const [state, setState] = useState<CachedResourceState<T>>({
    data: null,
    error: null,
    loading: enabled,
    stale: false,
  })

  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const lastFetchRef = useRef<number>(0)
  const aborterRef = useRef<AbortController | null>(null)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  const refresh = useCallback(async (force = false) => {
    const now = Date.now()
    if (!force && lastFetchRef.current && now - lastFetchRef.current < ttlMs) {
      return
    }

    aborterRef.current?.abort()
    aborterRef.current = new AbortController()

    setState((prev) => ({
      ...prev,
      loading: prev.data === null,
      stale: prev.data !== null,
      error: null,
    }))

    try {
      const data = await fetcherRef.current()
      if (!mountedRef.current) return
      lastFetchRef.current = Date.now()
      setState({ data, error: null, loading: false, stale: false })
    } catch (err: any) {
      if (err?.name === 'AbortError' || err?.name === 'CanceledError') return
      if (!mountedRef.current) return
      const message = err?.response?.data?.error || err?.message || '加载失败'
      setState((prev) => ({
        ...prev,
        error: message,
        loading: false,
        stale: false,
      }))
      onError?.(err)
    }
  }, [ttlMs, onError])

  useEffect(() => {
    if (enabled) {
      refresh()
    }
    return () => {
      aborterRef.current?.abort()
    }
  }, [enabled, key, refresh])

  return { ...state, refresh }
}