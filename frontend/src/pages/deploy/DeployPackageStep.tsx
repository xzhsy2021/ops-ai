interface DeployPackageStepProps {
  pipelines: any[]
  deployPackages: any[]
  system: string
  fileName: string
  pipelineId: string
  pipelineSteps: any[]
  selectStyle: React.CSSProperties
  setFileName: (value: string) => void
  setPipelineId: (value: string) => void
}

export default function DeployPackageStep(props: DeployPackageStepProps) {
  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <div>
          <label style={{ display: 'block', marginBottom: 6, fontSize: 14, color: 'var(--text-secondary)' }}>Pipeline</label>
          <select value={props.pipelineId} onChange={(e) => props.setPipelineId(e.target.value)} style={props.selectStyle}>
            <option value="">-- 默认流程 --</option>
            {props.pipelines.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </div>
        <div>
          <label style={{ display: 'block', marginBottom: 6, fontSize: 14, color: 'var(--text-secondary)' }}>版本/文件名</label>
          <input
            list="deploy-packages-list"
            value={props.fileName}
            onChange={(e) => props.setFileName(e.target.value)}
            placeholder={props.deployPackages.length > 0 ? '选择或输入包名...' : '输入包名...'}
            style={{ width: '100%' }}
          />
          <datalist id="deploy-packages-list">
            {props.deployPackages.map((p) => (
              <option key={p.name} value={p.name}>{p.name} ({p.size_mb} MB)</option>
            ))}
          </datalist>
          {props.system && props.deployPackages.length === 0 && (
            <div style={{ marginTop: 4, fontSize: 12, color: 'var(--warning)' }}>
              暂无匹配的发布包，可手动输入包名或先上传发布包
            </div>
          )}
          {props.system && props.deployPackages.length > 0 && (
            <div style={{ marginTop: 4, fontSize: 12, color: 'var(--text-muted)' }}>
              共 {props.deployPackages.length} 个可用发布包
            </div>
          )}
        </div>
      </div>

      {props.pipelineSteps.length > 0 && (
        <div style={{ background: 'var(--bg-page)', borderRadius: 8, padding: 12 }}>
          <h3 style={{ marginBottom: 8, color: 'var(--text-secondary)', fontSize: 14 }}>Pipeline 步骤预览:</h3>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {props.pipelineSteps.map((s, i: number) => (
              <span key={i} style={{ background: 'var(--bg-surface)', padding: '4px 10px', borderRadius: 4, fontSize: 13, color: 'var(--brand)' }}>
                #{i + 1} {s.name || s.step_type} ({s.step_type || s.type})
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}