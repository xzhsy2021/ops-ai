/**
 * Theme Engine
 * Simplified — CSS variables are defined in index.css.
 * Engine only manages data-theme attribute and FX/motion/density from JSON.
 */

import fxTokens from '../../tokens/fx.json'
import renderTokens from '../../tokens/render.json'
import motionTokens from '../../tokens/motion.json'
import densityTokens from '../../tokens/density.json'

export type ThemeMode = 'crystal' | 'jade' | 'light'
type FxMode = 'full' | 'balanced' | 'low'
type DensityMode = 'compact' | 'standard' | 'comfortable'

function flatten(obj: Record<string, any>, prefix = ''): Record<string, string> {
  const result: Record<string, string> = {}
  for (const [key, value] of Object.entries(obj)) {
    const k = prefix ? `${prefix}-${key}` : key
    if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
      Object.assign(result, flatten(value, k))
    } else {
      result[k] = String(value)
    }
  }
  return result
}

function injectVariables(vars: Record<string, string>, prefix = '') {
  const root = document.documentElement
  for (const [key, value] of Object.entries(vars)) {
    const varName = prefix ? `--${prefix}-${key}` : `--${key}`
    root.style.setProperty(varName, value)
  }
}

export function applyTheme(theme: ThemeMode) {
  const root = document.documentElement
  root.dataset.theme = theme
  root.classList.toggle('dark', theme === 'crystal' || theme === 'jade')
}

export function applyFxMode(mode: FxMode) {
  const fx = fxTokens[mode]
  if (!fx) return
  injectVariables(flatten(fx), 'fx')
  const root = document.documentElement
  root.dataset.fx = mode
}

export function applyRenderTokens() {
  injectVariables(flatten(renderTokens), 'render')
}

export function applyMotionTokens() {
  injectVariables(flatten(motionTokens), 'motion')
  const d = motionTokens.duration
  injectVariables({
    'dur-instant': d.instant,
    'dur-fast': d.fast,
    'dur-base': d.base,
    'dur-slow': d.slow,
    'dur-slower': d.slower,
    'dur-slowest': d.slowest,
  })
  const e = motionTokens.easing
  injectVariables({
    'ease-emphasized': e.emphasized,
    'ease-decel': e.decel,
    'ease-accel': e.accel,
    'ease-spring': e.spring,
    'ease-linear': e.linear,
  })
}

export function applyDensityMode(mode: DensityMode) {
  const density = densityTokens[mode]
  if (!density) return
  injectVariables(flatten(density), 'density')
  const root = document.documentElement
  root.dataset.density = mode
}

export function initThemeEngine(options?: {
  theme?: ThemeMode
  fx?: FxMode
  density?: DensityMode
}) {
  applyRenderTokens()
  applyMotionTokens()
  applyTheme(options?.theme || 'crystal')
  applyFxMode(options?.fx || 'balanced')
  applyDensityMode(options?.density || 'standard')
}

export { fxTokens, renderTokens, motionTokens, densityTokens }
export type { FxMode, DensityMode }