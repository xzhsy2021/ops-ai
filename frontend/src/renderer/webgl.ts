/* ============ WebGL 深空渲染引擎 ============ */

import { VS, FS } from './shaders'
import { liveTokens } from './renderTokens'
import type { FxMode } from './types'

export interface GLState {
  gl: WebGLRenderingContext | null
  ok: boolean
  prog: WebGLProgram | null
  uniforms: Record<string, WebGLUniformLocation | null>
  canvas: HTMLCanvasElement
}

function compileShader(
  gl: WebGLRenderingContext,
  type: number,
  source: string,
): WebGLShader | null {
  const sh = gl.createShader(type)
  if (!sh) return null
  gl.shaderSource(sh, source)
  gl.compileShader(sh)
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    console.warn('WebGL shader compile error:', gl.getShaderInfoLog(sh))
    return null
  }
  return sh
}

/** 初始化 WebGL */
export function initGL(canvas: HTMLCanvasElement): GLState {
  const gl = canvas.getContext('webgl') || (canvas.getContext('experimental-webgl') as WebGLRenderingContext | null)
  if (!gl) return { gl: null, ok: false, prog: null, uniforms: {}, canvas }

  const vs = compileShader(gl, gl.VERTEX_SHADER, VS)
  const fs = compileShader(gl, gl.FRAGMENT_SHADER, FS)
  if (!vs || !fs) return { gl, ok: false, prog: null, uniforms: {}, canvas }

  const prog = gl.createProgram()
  if (!prog) return { gl, ok: false, prog: null, uniforms: {}, canvas }

  gl.attachShader(prog, vs)
  gl.attachShader(prog, fs)
  gl.linkProgram(prog)
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
    console.warn('WebGL program link error')
    return { gl, ok: false, prog: null, uniforms: {}, canvas }
  }

  gl.useProgram(prog)

  // Fullscreen quad
  const buf = gl.createBuffer()
  gl.bindBuffer(gl.ARRAY_BUFFER, buf)
  gl.bufferData(
    gl.ARRAY_BUFFER,
    new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
    gl.STATIC_DRAW,
  )

  const loc = gl.getAttribLocation(prog, 'p')
  gl.enableVertexAttribArray(loc)
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0)

  const uniforms: Record<string, WebGLUniformLocation | null> = {
    res: gl.getUniformLocation(prog, 'u_res'),
    time: gl.getUniformLocation(prog, 'u_time'),
    theme: gl.getUniformLocation(prog, 'u_theme'),
    fx: gl.getUniformLocation(prog, 'u_fx'),
    glow: gl.getUniformLocation(prog, 'u_glow'),
    neb: gl.getUniformLocation(prog, 'u_neb'),
    nebs: gl.getUniformLocation(prog, 'u_nebs'),
    par: gl.getUniformLocation(prog, 'u_par'),
    scan: gl.getUniformLocation(prog, 'u_scan'),
    star: gl.getUniformLocation(prog, 'u_star'),
    mouse: gl.getUniformLocation(prog, 'u_mouse'),
  }

  return { gl, ok: true, prog, uniforms, canvas }
}

/** 调整 WebGL 尺寸 */
export function sizeGL(state: GLState): void {
  const { canvas, gl } = state
  if (!gl) return
  const dpr = liveTokens.dpr
  canvas.width = innerWidth * dpr
  canvas.height = innerHeight * dpr
  canvas.style.width = innerWidth + 'px'
  canvas.style.height = innerHeight + 'px'
  gl.viewport(0, 0, canvas.width, canvas.height)
}

/** 渲染一帧 WebGL */
export function renderGL(
  state: GLState,
  time: number,
  themeCurrent: number,
  fx: FxMode,
  mouseX: number,
  mouseY: number,
): void {
  const { gl, ok, uniforms } = state
  if (!gl || !ok) return

  const fxVal = fx === 'full' ? 2 : fx === 'low' ? 0 : 1

  gl.uniform2f(uniforms.res, state.canvas.width, state.canvas.height)
  gl.uniform1f(uniforms.time, time)
  gl.uniform2f(uniforms.mouse, mouseX, mouseY)
  gl.uniform1f(uniforms.theme, themeCurrent)
  gl.uniform1f(uniforms.fx, fxVal)
  gl.uniform1f(uniforms.glow, liveTokens.glow)
  gl.uniform1f(uniforms.neb, liveTokens.nebula)
  gl.uniform1f(uniforms.nebs, liveTokens['nebula-scale'])
  gl.uniform1f(uniforms.par, Math.max(0.01, liveTokens.parallax))
  gl.uniform1f(uniforms.scan, liveTokens.scan)
  gl.uniform1f(uniforms.star, liveTokens.star)
  gl.drawArrays(gl.TRIANGLES, 0, 6)
}