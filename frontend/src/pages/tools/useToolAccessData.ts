import { useState, useEffect, useCallback } from 'react'
import { capabilityTools } from '../../api'

export type ToolAccessData = {
  tools: any[]
  categories: string[]
  overview: {
    total: number
    active: number
    blocked: number
    high_risk: number
    requests_last_hour: number
    requests_last_day: number
  } | null
  loading: boolean
  error: string | null
  refresh: () => void
}

export function useToolAccessData(): ToolAccessData {
  const [tools, setTools] = useState<any[]>([])
  const [categories, setCategories] = useState<string[]>([])
  const [overview, setOverview] = useState<ToolAccessData['overview']>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await capabilityTools.list({ profile: 'admin_full', include_disabled: true, include_schema: true })
      const data = res.data?.tools || res.data?.data || res.data || []
      const items = Array.isArray(data) ? data : []
      setTools(items)
      const cats = [...new Set(items.map((t: any) => t.category).filter(Boolean))]
      setCategories(cats as string[])

      const stats = res.data?.stats || res.data?.overview
      if (stats) {
        setOverview({
          total: items.length,
          active: stats.active ?? items.filter((t: any) => t.enabled !== false && t.available !== false).length,
          blocked: stats.blocked ?? items.filter((t: any) => t.available === false || t.blocked_reason).length,
          high_risk: stats.high_risk ?? items.filter((t: any) => t.risk === 'high' || t.risk === 'critical').length,
          requests_last_hour: stats.requests_last_hour || 0,
          requests_last_day: stats.requests_last_day || 0,
        })
      }
    } catch (err: any) {
      setTools([])
      setError(err?.response?.data?.error || err?.message || 'Failed to load tools')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  return { tools, categories, overview, loading, error, refresh: load }
}
