import type { CSSProperties } from 'react'
import { EntityPicker } from '../../components/EntityPicker'

interface DeployTargetStepProps {
  systems: any[]
  services: any[]
  environments: any[]
  system: string
  service: string
  environment: string
  selectedService: any
  serverAutoMode: boolean
  selectStyle: CSSProperties
  navigate: (path: string) => void
  setSystem: (value: string) => void
  setService: (value: string) => void
  setEnvironment: (value: string) => void
  setPipelineId: (value: string) => void
  applyServiceDefaults: (value: string, options?: any) => void
  resolveServerNamesForTarget: (service: any) => string[]
  autoFillServers: (options?: any) => void
}

export default function DeployTargetStep(props: DeployTargetStepProps) {
  const systemOptions = props.systems.map((s) => ({ value: s.name, label: s.display_name || s.name, description: s.name !== s.display_name ? s.name : undefined }))
  const serviceOptions = props.services.map((s) => ({ value: s.name, label: s.display_name || s.name, description: s.template ? `流程: ${s.template}` : undefined }))
  const envOptions = props.environments.map((e) => ({ value: e.name, label: `${e.display_name || e.name}${e.category ? ` · ${e.category}` : ''}`, description: e.display_name && e.display_name !== e.name ? e.name : undefined }))

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
      <div>
        <label style={{ display: 'block', marginBottom: 6, fontSize: 14, color: 'var(--text-secondary)' }}>系统</label>
        <EntityPicker
          options={systemOptions}
          value={props.system}
          onChange={(v) => { props.setSystem(v as string); props.setService(''); props.setEnvironment(''); props.setPipelineId('') }}
          placeholder="-- 选择系统 --"
          searchPlaceholder="搜索系统..."
          showRecent
          recentKey="deploy-system"
        />
      </div>
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          <label style={{ fontSize: 14, color: 'var(--text-secondary)' }}>服务</label>
          {props.system && props.service && (
            <button
              type="button"
              className="btn"
              onClick={() => props.navigate(`/systems/${encodeURIComponent(props.system)}/services/${encodeURIComponent(props.service)}/edit`)}
              style={{ padding: '2px 8px', fontSize: 12, background: 'var(--border-strong)', color: 'var(--text-primary)' }}
            >
              查看/编辑服务
            </button>
          )}
        </div>
        <EntityPicker
          options={serviceOptions}
          value={props.service}
          onChange={(v) => props.applyServiceDefaults(v as string, { forceAuto: props.serverAutoMode })}
          placeholder="-- 选择服务 --"
          searchPlaceholder="搜索服务..."
          emptyText={`当前系统共 ${props.services.length} 个服务`}
          showRecent
          recentKey="deploy-service"
        />
      </div>
      <div>
        <label style={{ display: 'block', marginBottom: 6, fontSize: 14, color: 'var(--text-secondary)' }}>环境</label>
        <EntityPicker
          options={envOptions}
          value={props.environment}
          onChange={(v) => {
            const val = v as string
            props.setEnvironment(val)
            props.autoFillServers({ envName: val, force: props.serverAutoMode })
          }}
          placeholder="-- 选择环境 --"
          searchPlaceholder="搜索环境..."
          showRecent
          recentKey="deploy-environment"
        />
      </div>
    </div>
  )
}