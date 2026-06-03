import { StatusBadge } from '../../components/ui'

interface DeployRunStepProps {
  deployDisabledReasons: string[]
  deployButtonDisabled: boolean
  loading: boolean
  confirmingRelease: boolean
  status: string
  statusColor: string
  handleDeploy: () => void
}

export default function DeployRunStep({
  deployDisabledReasons,
  deployButtonDisabled,
  loading,
  confirmingRelease,
  status,
  statusColor,
  handleDeploy,
}: DeployRunStepProps) {
  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <button
          className="btn btn-primary"
          onClick={handleDeploy}
          disabled={deployButtonDisabled}
          title={deployDisabledReasons.join('；') || '开始发布'}
          style={{ padding: '10px 24px', fontSize: 15, fontWeight: 700 }}
        >
          {loading ? '发布中...' : confirmingRelease ? '确认中...' : '开始发布'}
        </button>
        {deployDisabledReasons.length > 0 && (
          <span style={{ fontSize: 12, color: 'var(--text-muted)', maxWidth: 420 }}>
            {deployDisabledReasons[0]}{deployDisabledReasons.length > 1 ? `，另有 ${deployDisabledReasons.length - 1} 项待处理` : ''}
          </span>
        )}
        {status && (
          <span style={{ fontSize: 14, color: statusColor }}>
            状态: <StatusBadge value={status} />
          </span>
        )}
      </div>
      {!status && (
        <div style={{ color: 'var(--text-muted)', fontSize: 13 }}>
          请确认以上各步骤信息无误后，点击「开始发布」执行部署。生产环境发布将需要额外风险确认。
        </div>
      )}
    </div>
  )
}