import { ThresholdSettingsSection } from './ThresholdSettingsSection'

export type ServerAdvancedSettingsProps = {
  batchConcurrency: number
  batchSize: number
  commandTimeoutSeconds: number
  runTimeoutSeconds: number
  onBatchConcurrencyChange: (next: number) => void
  onBatchSizeChange: (next: number) => void
  onCommandTimeoutSecondsChange: (next: number) => void
  onRunTimeoutSecondsChange: (next: number) => void
}

export function ServerAdvancedSettings({
  batchConcurrency,
  batchSize,
  commandTimeoutSeconds,
  runTimeoutSeconds,
  onBatchConcurrencyChange,
  onBatchSizeChange,
  onCommandTimeoutSecondsChange,
  onRunTimeoutSecondsChange,
}: ServerAdvancedSettingsProps) {
  return (
    <details className="inspection-advanced">
      <summary>???? ? ?? / ?? / ????</summary>
      <div className="inspection-advanced-body">
        <label>??? (1-8)
          <input type="number" min={1} max={8} value={batchConcurrency} onChange={(e) => onBatchConcurrencyChange(Math.max(1, Math.min(8, Number(e.target.value || 1))))} />
        </label>
        <label>???? (1-20)
          <input type="number" min={1} max={20} value={batchSize} onChange={(e) => onBatchSizeChange(Math.max(1, Math.min(20, Number(e.target.value || 1))))} />
        </label>
        <label>????? (?)
          <input type="number" min={5} max={120} value={commandTimeoutSeconds} onChange={(e) => onCommandTimeoutSecondsChange(Math.max(5, Math.min(120, Number(e.target.value || 20))))} />
        </label>
        <label>??????? (?)
          <input type="number" min={30} max={1800} value={runTimeoutSeconds} onChange={(e) => onRunTimeoutSecondsChange(Math.max(30, Math.min(1800, Number(e.target.value || 180))))} />
        </label>
      </div>
      <div style={{ borderTop: '1px solid var(--border)', margin: '4px 14px 0' }} />
      <div style={{ padding: '12px 14px 14px' }}>
        <ThresholdSettingsSection compact />
      </div>
    </details>
  )
}
