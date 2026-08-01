/* ============ 主渲染引擎协调器 ============ */
/* 管理三张画布：gl（WebGL 深空）、dust（Canvas 尘埃）、syn（Canvas 突触由外部绘制） */

import { initGL, sizeGL, renderGL, type GLState } from './webgl'
import { sizeDust, drawDust } from './dust'
import type { FxMode, ThemeMode } from './types'

export interface RenderEngine {
  glState: GLState
  start: () => void
  stop: () => void
  resize: () => void
  setFx: (fx: FxMode) => void
  setTheme: (theme: ThemeMode) => void
  readonly fps: number
  readonly themeCurrent: number
  readonly mouseX: number
  readonly mouseY: number
  readonly currentFx: FxMode
  readonly currentTheme: ThemeMode
  setSynDrawCallback: (fn: ((ctx: CanvasRenderingContext2D, dt: number, now: number) => void) | null) => void
}

export function createRenderEngine(
  glCanvas: HTMLCanvasElement,
  dustCanvas: HTMLCanvasElement,
  synCanvas: HTMLCanvasElement,
): RenderEngine {
  const glState = initGL(glCanvas)
  const dustCtx = dustCanvas.getContext('2d')!

  let running = false
  let rafId = 0
  let t0 = performance.now()
  let frames = 0
  let fpsTime = 0
  let lastDrawTime = 0

  let themeTarget = 1
  let themeCurrent = 1
  let mouseX = 0.5
  let mouseY = 0.5
  let mouseTX = 0.5
  let mouseTY = 0.5
  let currentFx: FxMode = 'balanced'
  let currentTheme: ThemeMode = 'dark'
  let fpsValue = 0

  /** 外部钩子：每帧被调用，用于绘制 synCanvas */
  let onSynDraw: ((ctx: CanvasRenderingContext2D, dt: number, now: number) => void) | null = null

  function resizeAll() {
    if (glState.ok) sizeGL(glState)
    const w = innerWidth
    const h = innerHeight
    dustCanvas.width = w
    dustCanvas.height = h
    synCanvas.width = w
    synCanvas.height = h
    sizeDust(w, h)
  }

  resizeAll()

  function loop(now: number) {
    if (!running) return
    const dt = Math.min(0.05, (now - t0) / 1000)
    t0 = now

    const low = currentFx === 'low'

    // FX·LOW 帧率节流 ~4.5fps
    if (low && now - lastDrawTime < 220) {
      rafId = requestAnimationFrame(loop)
      return
    }
    lastDrawTime = now

    // 平滑过渡
    themeCurrent += (themeTarget - themeCurrent) * 0.06
    mouseX += (mouseTX - mouseX) * 0.08
    mouseY += (mouseTY - mouseY) * 0.08

    // WebGL 深空
    if (glState.ok) {
      const time = low ? 3.0 : now / 1000
      renderGL(glState, time, themeCurrent, currentFx, mouseX, mouseY)
    }

    // Canvas 尘埃
    drawDust(dustCtx, currentTheme === 'dark', mouseX, mouseY)

    // Canvas 突触（外部钩子）
    const synCtx = synCanvas.getContext('2d')
    if (synCtx) {
      synCtx.clearRect(0, 0, synCanvas.width, synCanvas.height)
      if (onSynDraw) onSynDraw(synCtx, dt, now)
    }

    // FPS
    frames++
    if (now - fpsTime > 500) {
      fpsValue = Math.round((frames * 1000) / (now - fpsTime))
      frames = 0
      fpsTime = now
    }

    rafId = requestAnimationFrame(loop)
  }

  function start() {
    if (running) return
    running = true
    t0 = performance.now()
    fpsTime = t0
    lastDrawTime = t0
    rafId = requestAnimationFrame(loop)
  }

  function stop() {
    running = false
    cancelAnimationFrame(rafId)
  }

  function resize() {
    resizeAll()
  }

  function setFx(fx: FxMode) {
    currentFx = fx
    if (glState.ok) sizeGL(glState)
  }

  function setTheme(theme: ThemeMode) {
    currentTheme = theme
    themeTarget = theme === 'dark' ? 1 : 0
  }

  // 全局鼠标追踪
  addEventListener('mousemove', (e) => {
    mouseTX = e.clientX / innerWidth
    mouseTY = e.clientY / innerHeight
  })

  addEventListener('resize', resize)

  return {
    glState,
    start,
    stop,
    resize,
    setFx,
    setTheme,
    get fps() { return fpsValue },
    get themeCurrent() { return themeCurrent },
    get mouseX() { return mouseX },
    get mouseY() { return mouseY },
    get currentFx() { return currentFx },
    get currentTheme() { return currentTheme },
    // 暴露 setSynDrawCallback 以便外部注册
    setSynDrawCallback(fn: ((ctx: CanvasRenderingContext2D, dt: number, now: number) => void) | null) {
      onSynDraw = fn
    },
  }
}