import { useState, useEffect, useCallback } from 'react'
import axios from 'axios'
import type { PipelineData } from './pipelineConfig'

const baseUrl = (typeof window !== 'undefined' && (window as any).__API_BASE_URL__) || '/api/v2'

export function usePipelineData() {
  const [pipelines, setPipelines] = useState<PipelineData[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await axios.get(`${baseUrl}/api/pipelines`)
      const data = res.data?.pipelines || res.data?.data || res.data || []
      setPipelines(Array.isArray(data) ? data : [])
    } catch (err: any) {
      setError(err?.response?.data?.error || err?.message || '加载失败')
      setPipelines([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  return { pipelines, loading, error, refresh: load }
}

export function usePipelineActions(onRefresh: () => void) {
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const save = useCallback(async (pipeline: PipelineData) => {
    setSaving(true)
    try {
      await axios.put(`${baseUrl}/api/pipelines/${pipeline.id}`, pipeline)
      onRefresh()
    } finally {
      setSaving(false)
    }
  }, [onRefresh])

  const remove = useCallback(async (id: string) => {
    setDeleting(true)
    try {
      await axios.delete(`${baseUrl}/api/pipelines/${id}`)
      onRefresh()
    } finally {
      setDeleting(false)
    }
  }, [onRefresh])

  const duplicate = useCallback(async (pipeline: PipelineData) => {
    setSaving(true)
    try {
      const payload = { ...pipeline, name: `${pipeline.name} (副本)`, id: undefined }
      await axios.post(`${baseUrl}/api/pipelines`, payload)
      onRefresh()
    } finally {
      setSaving(false)
    }
  }, [onRefresh])

  return { save, remove, duplicate, saving, deleting }
}