/**
 * Design System — Unified component export
 *
 * Re-exports all UI primitives, workspace components, and v10 business
 * components from a single entry point.
 */

// Core UI primitives
export { GlassPanel } from '../components/ui/GlassPanel'
export { GlassCard } from '../components/ui/GlassCard'
export { Button } from '../components/ui/Button'
export { Badge } from '../components/ui/Badge'
export { MetricCard } from '../components/ui/MetricCard'
export { ThemeToast } from '../components/ui/ThemeToast'
export { SettingsModal } from '../components/ui/SettingsModal'
export type { ThemeMode } from '../components/ui/SettingsModal'

// Workspace components (3D folder stage + gantt strip)
export { FolderStage, toneFromStatus } from '../components/workspace/FolderStage'
export type { FolderTone, FolderItem } from '../components/workspace/FolderStage'
export { GanttStrip } from '../components/workspace/GanttStrip'
export type { GanttItem } from '../components/workspace/GanttStrip'

// V10 business components (diagnosis / pipeline / action)
export { default as DiagnosisCard } from '../components/v10/DiagnosisCard'
export { default as ActionButton } from '../components/v10/ActionButton'
export { default as PipelineStage, PipelineStageRail } from '../components/v10/PipelineStage'
export type { PipelineStatus } from '../components/v10/PipelineStage'
