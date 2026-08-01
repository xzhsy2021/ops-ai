/* ============ 神经突触实验室 · 渲染类型 ============ */

export type ThemeMode = 'light' | 'dark'
export type FxMode = 'full' | 'balanced' | 'low'

/** Render token 值（① 场的物理） */
export interface RenderTokens {
  dpr: number
  particles: number
  glow: number
  nebula: number
  'nebula-scale': number
  parallax: number
  scan: number
  star: number
  'depth-blur': number
  'pulse-speed': number
  'pulse-trail': number
}

/** 节点（MCP tool） */
export interface NodeDef {
  id: string
  label: string
  risk: 'info' | 'warn' | 'alert'
  x: number
  y: number
}

/** 边 */
export interface EdgeDef {
  from: number
  to: number
}

/** 脉冲（放电事件） */
export interface Pulse {
  from: number
  to: number
  progress: number
  risk: 'info' | 'warn' | 'alert'
  tool: string
  highlight: boolean
}

/** 涟漪（脉冲到达时的扩散环） */
export interface Ripple {
  x: number
  y: number
  progress: number
  risk: 'info' | 'warn' | 'alert'
}

/** 日志条目 */
export interface LogEntry {
  id: number
  time: string
  tool: string
  toolId: string
  risk: 'info' | 'warn' | 'alert'
  result: 'OK' | 'QUEUED'
  from: number
  to: number
  timestamp: number
}

/** 渲染状态 */
export interface RenderState {
  theme: ThemeMode
  fx: FxMode
  tokens: RenderTokens
  mouseX: number
  mouseY: number
  themeTarget: number // 0=light, 1=dark
  themeCurrent: number
  dirty: boolean
}

/** 性能指标 */
export interface PerfStats {
  fps: number
  frames: number
  fpsTime: number
}

export const RISK_COLORS: Record<string, string> = {
  info: '54,198,255',
  warn: '255,176,32',
  alert: '255,77,106',
}

export const RISK_CSS_VARS: Record<string, string> = {
  info: '--n-info',
  warn: '--n-warn',
  alert: '--n-alert',
}