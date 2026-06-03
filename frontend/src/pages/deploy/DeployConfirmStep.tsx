import ReleaseConfirmationPanel from './ReleaseConfirmationPanel'
import { DeploySummaryCard } from './DeploySummaryCard'
import type { DeploySummaryItem } from './DeploySummaryCard'

interface DeployConfirmStepProps {
  releaseConfirmation: any
  system: string
  service: string
  environment: string
  fileName: string
  selectedServerNames: string[]
  pipelineId: string
  parallelism: number
  failFast: boolean
  selectedService: any
  isProdEnvironment: (env: any) => boolean
}

export default function DeployConfirmStep({
  releaseConfirmation,
  system,
  service,
  environment,
  fileName,
  selectedServerNames,
  pipelineId,
  parallelism,
  failFast,
  selectedService,
  isProdEnvironment,
}: DeployConfirmStepProps) {
  const isProd = isProdEnvironment(environment)
  const riskLevel = isProd ? (failFast ? '高 (快速失败)' : '高') : (failFast ? '中' : '低')
  const riskTone: DeploySummaryItem['tone'] = isProd ? 'danger' : (failFast ? 'warning' : 'default')
  const summaryItems: DeploySummaryItem[] = [
    { label: '系统', value: system || '-' },
    { label: '服务', value: selectedService?.display_name || service || '-' },
    { label: '环境', value: environment || '-', tone: isProd ? 'danger' : 'default' },
    { label: '发布包', value: fileName || '-' },
    { label: 'Pipeline', value: pipelineId || '默认流程' },
    { label: '服务器', value: `${selectedServerNames.length} 台` },
    { label: '并发度', value: parallelism },
    { label: '失败策略', value: failFast ? '快速失败' : '继续执行' },
    { label: '生产环境', value: isProd ? '是 ⚠' : '否', tone: isProd ? 'danger' : 'default' },
    { label: '风险等级', value: riskLevel, tone: riskTone },
  ]

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <DeploySummaryCard title="发布确认摘要" items={summaryItems} />
      {isProd && (
        <div style={{ background: 'var(--danger-surface)', border: '1px solid var(--danger-border)', borderRadius: 8, padding: '10px 12px', color: 'var(--danger)', fontSize: 13 }}>
          <strong>⚠ 生产环境发布 — 请仔细检查以上摘要信息</strong>
        </div>
      )}
      <ReleaseConfirmationPanel confirmation={releaseConfirmation} />
    </div>
  )
}