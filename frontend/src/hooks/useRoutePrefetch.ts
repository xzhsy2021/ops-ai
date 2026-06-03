import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { systemHealth, capabilityTools, resource, dbTools, deployment } from '../api'

const prefetched = new Set<string>()

function prefetchOnce(key: string, fn: () => void) {
  if (prefetched.has(key)) return
  prefetched.add(key)
  fn()
}

const ROUTE_PREFETCH_MAP: Record<string, Array<{ key: string; fn: () => void }>> = {
  '/': [
    { key: 'dashboard-health', fn: () => { systemHealth.health().catch(() => {}) } },
  ],
  '/dashboard': [
    { key: 'dashboard-health', fn: () => { systemHealth.health().catch(() => {}) } },
  ],
  '/deploy': [
    { key: 'deploy-systems', fn: () => { resource.systems().catch(() => {}) } },
    { key: 'deploy-history', fn: () => { deployment.list({ limit: 20 }).catch(() => {}) } },
  ],
  '/database': [
    { key: 'db-tables', fn: () => { dbTools.tables().catch(() => {}) } },
  ],
  '/tools': [
    { key: 'tools-capabilities', fn: () => { capabilityTools.list({ limit: 49 }).catch(() => {}) } },
  ],
}

function prefetchRoute(path: string) {
  const entries = ROUTE_PREFETCH_MAP[path]
  if (!entries) return
  for (const entry of entries) {
    prefetchOnce(entry.key, entry.fn)
  }
}

export function useRoutePrefetch() {
  const location = useLocation()

  useEffect(() => {
    prefetchRoute(location.pathname)
  }, [location.pathname])

  useEffect(() => {
    const handleMouseOver = (e: MouseEvent) => {
      const target = (e.target as HTMLElement).closest('a[href]')
      if (!target) return
      const href = target.getAttribute('href')
      if (!href) return
      const resolved = href.startsWith('/') ? href : new URL(href, window.location.origin).pathname
      prefetchRoute(resolved)
    }
    document.addEventListener('mouseover', handleMouseOver, { passive: true })
    return () => document.removeEventListener('mouseover', handleMouseOver)
  }, [])
}
