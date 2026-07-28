import { useState, useCallback } from 'react'
import type { ReactNode } from 'react'
import { RiskConfirmDialog } from './ui'

export type RiskActionResultLink = {
  label: string
  to: string
  tone?: 'neutral' | 'brand' | 'success' | 'warning' | 'danger'
}

export type RiskActionResult = {
  success: boolean
  message?: string
  auditId?: string
  taskId?: string
  reportId?: string
  links?: RiskActionResultLink[]
  error?: string
}

export type RiskActionGuardProps = {
  riskLevel: 'low' | 'medium' | 'high' | 'critical'
  title: string
  target: string
  description?: string
  details?: Array<{ label: string; value: ReactNode }>
  requireReason?: boolean
  confirmText?: string
  confirmMode?: 'type' | 'one-click'
  /** 默认提供的执行后链接（在 onConfirm 返回的 links 之外追加） */
  postConfirmLinks?: RiskActionResultLink[]
  /** 支持异步返回结果；返回 void 时仍展示默认成功结果 */
  onConfirm: (reason?: string) => Promise<RiskActionResult | void> | RiskActionResult | void
  children: (open: () => void) => ReactNode
}

export function RiskActionGuard({
  riskLevel,
  title,
  target,
  description,
  details,
  requireReason = false,
  confirmText,
  confirmMode = 'type',
  postConfirmLinks,
  onConfirm,
  children,
}: RiskActionGuardProps) {
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState('')
  const [reason, setReason] = useState('')
  const [pending, setPending] = useState(false)
  const [result, setResult] = useState<RiskActionResult | null>(null)

  const handleOpen = useCallback(() => {
    setResult(null)
    setValue('')
    setReason('')
    setOpen(true)
  }, [])

  const handleClose = useCallback(() => {
    setOpen(false)
    setValue('')
    setReason('')
    // 结果面板不立即清空，下次打开时由 handleOpen 重置
  }, [])

  const handleConfirm = async () => {
    setPending(true)
    try {
      const res = await onConfirm(reason || undefined)
      const merged: RiskActionResult = {
        success: true,
        message: '操作已执行',
        ...(res || {}),
        links: [...(postConfirmLinks || []), ...(res?.links || [])],
      }
      if (res && res.success === false) {
        merged.success = false
        merged.message = res.error || res.message || '操作失败'
      }
      setResult(merged)
    } catch (err: any) {
      setResult({
        success: false,
        message: err?.message || String(err || '操作失败'),
      })
    } finally {
      setPending(false)
    }
  }

  return (
    <>
      {children(handleOpen)}
      <RiskConfirmDialog
        open={open}
        title={title}
        description={description}
        target={target}
        confirmText={confirmText || '确认执行'}
        value={value}
        onValueChange={setValue}
        onCancel={handleClose}
        onConfirm={handleConfirm}
        riskLevel={riskLevel}
        details={details}
        reason={reason}
        onReasonChange={setReason}
        reasonRequired={requireReason}
        confirmMode={confirmMode}
        confirmDisabled={pending}
        result={result}
      />
    </>
  )
}
