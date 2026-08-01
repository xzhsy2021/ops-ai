/* ============ RenderEngine React Context Provider ============ */

import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { createRenderEngine } from '../renderer/engine'
import type { RenderEngine, FxMode, ThemeMode } from '../renderer/engine'

interface RenderEngineContextValue {
  engine: RenderEngine | null
  glCanvasRef: React.RefObject<HTMLCanvasElement | null>
  dustCanvasRef: React.RefObject<HTMLCanvasElement | null>
  synCanvasRef: React.RefObject<HTMLCanvasElement | null>
  fps: number
  theme: ThemeMode
  fx: FxMode
  setFx: (fx: FxMode) => void
  setTheme: (theme: ThemeMode) => void
}

const RenderEngineContext = createContext<RenderEngineContextValue | null>(null)

export function useRenderEngine(): RenderEngineContextValue {
  const ctx = useContext(RenderEngineContext)
  if (!ctx) throw new Error('useRenderEngine must be used within RenderEngineProvider')
  return ctx
}

export function RenderEngineProvider({
  children,
  initialTheme = 'dark',
  initialFx = 'balanced',
}: {
  children: ReactNode
  initialTheme?: ThemeMode
  initialFx?: FxMode
}) {
  const glCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const dustCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const synCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const engineRef = useRef<RenderEngine | null>(null)
  const [fps, setFps] = useState(0)
  const [theme, setThemeState] = useState<ThemeMode>(initialTheme)
  const [fx, setFxState] = useState<FxMode>(initialFx)

  // 初始化引擎
  useEffect(() => {
    const gl = glCanvasRef.current
    const dust = dustCanvasRef.current
    const syn = synCanvasRef.current
    if (!gl || !dust || !syn) return

    const engine = createRenderEngine(gl, dust, syn)
    engineRef.current = engine
    engine.start()

    // FPS 轮询
    const fpsInterval = setInterval(() => {
      setFps(engine.fps)
    }, 500)

    return () => {
      engine.stop()
      clearInterval(fpsInterval)
    }
  }, [])

  const setFx = (fx: FxMode) => {
    setFxState(fx)
    engineRef.current?.setFx(fx)
  }

  const setTheme = (theme: ThemeMode) => {
    setThemeState(theme)
    engineRef.current?.setTheme(theme)
  }

  return (
    <RenderEngineContext.Provider
      value={{
        engine: engineRef.current,
        glCanvasRef,
        dustCanvasRef,
        synCanvasRef,
        fps,
        theme,
        fx,
        setFx,
        setTheme,
      }}
    >
      <canvas
        ref={glCanvasRef}
        id="gl"
        style={{
          position: 'fixed',
          inset: 0,
          zIndex: 0,
          pointerEvents: 'none',
        }}
      />
      <canvas
        ref={dustCanvasRef}
        id="dust"
        style={{
          position: 'fixed',
          inset: 0,
          zIndex: 1,
          pointerEvents: 'none',
        }}
      />
      <canvas
        ref={synCanvasRef}
        id="syn"
        style={{
          position: 'fixed',
          inset: 0,
          zIndex: 2,
          pointerEvents: 'none',
        }}
      />
      {children}
    </RenderEngineContext.Provider>
  )
}