import { useState } from 'react'
import type { ReactNode } from 'react'
import { RiskConfirmDialog } from './ui'

export type RiskActionGuardProps = {
  riskLevel: 'low' | 'medium' | 'high' | 'critical'
  title: string
  target: string
  description?: string
  details?: Array<{ label: string; value: ReactNode }>
  requireReason?: boolean
  confirmText?: string
  confirmMode?: 'type' | 'one-click'
  onConfirm: (reason?: string) => Promise<void> | void
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
  onConfirm,
  children,
}: RiskActionGuardProps) {
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState('')
  const [reason, setReason] = useState('')
  const [pending, setPending] = useState(false)

  const handleConfirm = async () => {
    setPending(true)
    try {
      await onConfirm(reason || undefined)
    } finally {
      setPending(false)
      setOpen(false)
      setValue('')
      setReason('')
    }
  }

  const handleCancel = () => {
    setOpen(false)
    setValue('')
    setReason('')
  }

  return (
    <>
      {children(() => setOpen(true))}
      <RiskConfirmDialog
        open={open}
        title={title}
        description={description}
        target={target}
        confirmText={confirmText || '确认执行'}
        value={value}
        onValueChange={setValue}
        onCancel={handleCancel}
        onConfirm={handleConfirm}
        riskLevel={riskLevel}
        details={details}
        reason={reason}
        onReasonChange={setReason}
        reasonRequired={requireReason}
        confirmMode={confirmMode}
        confirmDisabled={pending}
      />
    </>
  )
}