/* ============ Render Token 预设 + Live 状态 ============ */

import type { RenderTokens, FxMode } from './types'

/** 三档 FX 预设（render.* 子树） */
export const PRESETS: Record<FxMode, RenderTokens> = {
  full: {
    dpr: 2,
    particles: 90,
    glow: 1.1,
    nebula: 1.1,
    'nebula-scale': 2.6,
    parallax: 46,
    scan: 1,
    star: 1,
    'depth-blur': 1.4,
    'pulse-speed': 1.2,
    'pulse-trail': 12,
  },
  balanced: {
    dpr: 1.5,
    particles: 48,
    glow: 0.75,
    nebula: 0.8,
    'nebula-scale': 2.3,
    parallax: 26,
    scan: 0,
    star: 1,
    'depth-blur': 0.8,
    'pulse-speed': 0.9,
    'pulse-trail': 7,
  },
  low: {
    dpr: 1,
    particles: 0,
    glow: 0.4,
    nebula: 0.4,
    'nebula-scale': 2.0,
    parallax: 0,
    scan: 0,
    star: 0,
    'depth-blur': 0.3,
    'pulse-speed': 0.6,
    'pulse-trail': 3,
  },
}

/** 深拷贝 helper */
export function clone<T>(o: T): T {
  return JSON.parse(JSON.stringify(o))
}

/** 当前 live token 值 */
export let liveTokens: RenderTokens = clone(PRESETS.balanced)

/** 滑块控件定义 */
export interface CtrlDef {
  key: keyof RenderTokens
  label: string
  min: number
  max: number
  step: number
  type: 'range' | 'seg'
  segValues?: number[]
}

export const CTRL_DEFS: CtrlDef[] = [
  { key: 'particles', label: 'PARTICLES', min: 0, max: 120, step: 1, type: 'range' },
  { key: 'glow', label: 'GLOW', min: 0, max: 1.5, step: 0.05, type: 'range' },
  { key: 'nebula', label: 'NEBULA', min: 0, max: 1.5, step: 0.05, type: 'range' },
  { key: 'nebula-scale', label: 'NEB-SCALE', min: 1.2, max: 4.5, step: 0.1, type: 'range' },
  { key: 'parallax', label: 'PARALLAX', min: 0, max: 80, step: 1, type: 'range' },
  { key: 'pulse-speed', label: 'PULSE-SPD', min: 0.3, max: 2, step: 0.05, type: 'range' },
  { key: 'pulse-trail', label: 'TRAIL', min: 0, max: 14, step: 1, type: 'range' },
  { key: 'depth-blur', label: 'GLOW-SPREAD', min: 0, max: 2, step: 0.1, type: 'range' },
  { key: 'dpr', label: 'DPR', min: 1, max: 2, step: 0.5, type: 'seg', segValues: [1, 1.5, 2] },
  { key: 'scan', label: 'SCAN', min: 0, max: 1, step: 1, type: 'seg', segValues: [0, 1] },
  { key: 'star', label: 'STAR', min: 0, max: 1, step: 1, type: 'seg', segValues: [0, 1] },
]

/** 设置单个 token 值 */
export function setToken(key: keyof RenderTokens, value: number): void {
  liveTokens[key] = value
}

/** 应用 FX preset */
export function applyPreset(fx: FxMode): void {
  liveTokens = clone(PRESETS[fx])
}

/** 检查是否有自定义值 */
export function isCustom(fx: FxMode): boolean {
  const preset = PRESETS[fx]
  return (Object.keys(liveTokens) as (keyof RenderTokens)[]).some(
    (k) => Math.abs(liveTokens[k] - preset[k]) > 1e-6,
  )
}