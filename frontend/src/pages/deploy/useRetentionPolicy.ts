import { useCallback, useState } from 'react'
import { deployment } from '../../api'

interface UseRetentionPolicyOptions {
  system?: string
  onDeploymentsChanged?: (rows: any[]) => void
}

export function useRetentionPolicy({ system = '', onDeploymentsChanged }: UseRetentionPolicyOptions = {}) {
  const [retentionPolicy, setRetentionPolicy] = useState<Record<string, any>>({})
  const [retentionPreview, setRetentionPreview] = useState<any>(null)
  const [retentionLoading, setRetentionLoading] = useState(false)
  const [retentionMessage, setRetentionMessage] = useState('')
  const [retentionError, setRetentionError] = useState('')

  const loadRetention = useCallback(async () => {
    setRetentionLoading(true)
    setRetentionError('')
    try {
      const res: any = await deployment.retention()
      setRetentionPolicy(res.data?.policy || res.data || {})
      setRetentionPreview(res.data?.preview || null)
    } catch (e: any) {
      setRetentionError(e?.message || String(e))
    } finally {
      setRetentionLoading(false)
    }
  }, [])

  const saveRetention = useCallback(async () => {
    setRetentionLoading(true)
    setRetentionMessage('')
    setRetentionError('')
    try {
      const res: any = await deployment.updateRetention(retentionPolicy)
      setRetentionPolicy(res.data?.policy || retentionPolicy)
      setRetentionPreview(res.data?.preview || null)
      setRetentionMessage('清理策略已保存')
    } catch (e: any) {
      setRetentionError(e?.message || String(e))
    } finally {
      setRetentionLoading(false)
    }
  }, [retentionPolicy])

  const previewRetention = useCallback(async () => {
    setRetentionLoading(true)
    setRetentionMessage('')
    setRetentionError('')
    try {
      const res: any = await deployment.previewRetention()
      setRetentionPreview(res.data)
      setRetentionMessage('已生成清理预览，未删除任何数据')
    } catch (e: any) {
      setRetentionError(e?.message || String(e))
    } finally {
      setRetentionLoading(false)
    }
  }, [])

  const cleanupRetention = useCallback(async () => {
    setRetentionLoading(true)
    setRetentionMessage('')
    setRetentionError('')
    try {
      const res: any = await deployment.cleanupRetention({ dry_run: false })
      setRetentionPreview(res.data)
      setRetentionMessage('清理完成，发布历史和审计记录已按策略处理')
      if (onDeploymentsChanged) {
        deployment.list(50, system || undefined).then((r: any) => onDeploymentsChanged(r.data || [])).catch(() => {})
      }
    } catch (e: any) {
      setRetentionError(e?.message || String(e))
    } finally {
      setRetentionLoading(false)
    }
  }, [retentionPreview, system, onDeploymentsChanged])

  const updateRetentionField = useCallback((key: string, value: any) => {
    setRetentionPolicy((prev) => ({ ...prev, [key]: value }))
  }, [])

  return {
    retentionPolicy,
    retentionPreview,
    retentionLoading,
    retentionMessage,
    retentionError,
    loadRetention,
    previewRetention,
    saveRetention,
    cleanupRetention,
    updateRetentionField,
  }
}
