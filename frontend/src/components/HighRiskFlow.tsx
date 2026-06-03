import { useState, useCallback } from 'react'
import type { ReactNode } from 'react'
import { RiskConfirmDialog, RiskBadge } from './ui'

export type HighRiskStep = 'idle' | 'prechecking' | 'confirming' | 'executing' | 'done' | 'error'

export interface HighRiskFlowProps {
  title: string
  description?: string
  target: string
  confirmText: string
  riskLevel?: string
  onPrecheck: () => Promise<{ detail?: string; risk_level?: string; blockers?: string[]; confirm_text?: string; details?: Array<{ label: string; value: ReactNode }> }>
  onExecute: (reason: string) => Promise<{ success: boolean; message?: string; reportUrl?: string }>
  reportUrl?: string
  reportLabel?: string
  children?: ReactNode
  confirmMode?: 'type' | 'one-click'
  reasonRequired?: boolean
  reasonLabel?: string
  confirmButtonLabel?: string
}

export function HighRiskFlow({
  title,
  description,
  target,
  confirmText,
  riskLevel = 'high',
  onPrecheck,
  onExecute,
  reportUrl,
  reportLabel = '查看报告',
  children,
  confirmMode = 'one-click',
  reasonRequired = true,
  reasonLabel = '操作原因',
  confirmButtonLabel = '确认执行',
}: HighRiskFlowProps) {
  const [step, setStep] = useState<HighRiskStep>('idle')
  const [reason, setReason] = useState('')
  const [confirmValue, setConfirmValue] = useState('')
  const [precheckDetail, setPrecheckDetail] = useState('')
  const [precheckRiskLevel, setPrecheckRiskLevel] = useState(riskLevel)
  const [precheckDetails, setPrecheckDetails] = useState<Array<{ label: string; value: ReactNode }>>([])
  const [effectiveConfirmText, setEffectiveConfirmText] = useState(confirmText)
  const [executeResult, setExecuteResult] = useState<{ success: boolean; message?: string; reportUrl?: string } | null>(null)
  const [error, setError] = useState('')

  const startFlow = useCallback(async () => {
    setStep('prechecking')
    setError('')
    try {
      const result = await onPrecheck()
      if (result.blockers && result.blockers.length > 0) {
        setError(`预检未通过：${result.blockers.join('；')}`)
        setStep('error')
        return
      }
      setPrecheckDetail(result.detail || '')
      setPrecheckRiskLevel(result.risk_level || riskLevel)
      setPrecheckDetails(result.details || [])
      setEffectiveConfirmText(result.confirm_text || confirmText)
      setStep('confirming')
    } catch (e: any) {
      setError(e?.message || '预检失败')
      setStep('error')
    }
  }, [onPrecheck, riskLevel, confirmText])

  const handleConfirm = useCallback(async () => {
    setStep('executing')
    setError('')
    try {
      const result = await onExecute(reason)
      setExecuteResult(result)
      setStep(result.success ? 'done' : 'error')
      if (!result.success) {
        setError(result.message || '操作失败')
      }
    } catch (e: any) {
      setError(e?.message || '执行失败')
      setStep('error')
    }
  }, [onExecute, reason])

  const resetFlow = useCallback(() => {
    setStep('idle')
    setReason('')
    setConfirmValue('')
    setError('')
    setExecuteResult(null)
  }, [])

  return (
    <div className="high-risk-flow">
      {children}

      {step === 'idle' && (
        <button className="btn btn-danger" onClick={startFlow}>
          开始预检
        </button>
      )}

      {step === 'prechecking' && (
        <div className="card" style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span className="loading-orb" />
          <span>预检中...</span>
        </div>
      )}

      <RiskConfirmDialog
        open={step === 'confirming'}
        title={title}
        description={description || precheckDetail}
        target={target}
        confirmText={effectiveConfirmText}
        value={confirmValue}
        onValueChange={setConfirmValue}
        onCancel={resetFlow}
        onConfirm={handleConfirm}
        riskLevel={precheckRiskLevel}
        details={precheckDetails}
        reason={reason}
        onReasonChange={setReason}
        reasonRequired={reasonRequired}
        reasonLabel={reasonLabel}
        confirmButtonLabel={confirmButtonLabel}
        confirmMode={confirmMode}
      />

      {step === 'executing' && (
        <div className="card" style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span className="loading-orb" />
          <span>执行中...</span>
        </div>
      )}

      {step === 'done' && (
        <div className="card" style={{ borderColor: 'var(--success)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
            <RiskBadge level="safe" label="已完成" />
            <strong>操作已完成</strong>
          </div>
          {executeResult?.message && <p style={{ color: 'var(--text-secondary)', marginBottom: '8px' }}>{executeResult.message}</p>}
          <div style={{ display: 'flex', gap: '8px' }}>
            <button className="btn btn-subtle" onClick={resetFlow}>关闭</button>
            {(reportUrl || executeResult?.reportUrl) && (
              <a className="btn btn-primary" href={reportUrl || executeResult?.reportUrl}>{reportLabel}</a>
            )}
          </div>
        </div>
      )}

      {step === 'error' && (
        <div className="card" style={{ borderColor: 'var(--danger)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
            <RiskBadge level="critical" label="失败" />
            <strong>操作失败</strong>
          </div>
          {error && <p style={{ color: 'var(--danger)', marginBottom: '8px' }}>{error}</p>}
          <button className="btn btn-subtle" onClick={resetFlow}>重试</button>
        </div>
      )}
    </div>
  )
}