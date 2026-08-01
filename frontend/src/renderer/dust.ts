/* ============ Canvas 神经尘埃粒子系统 ============ */

import { liveTokens } from './renderTokens'

interface DustParticle {
  x: number
  y: number
  vx: number
  vy: number
  z: number
}

let dust: DustParticle[] = []
let W = 0
let H = 0

/** 调整尺寸 */
export function sizeDust(width: number, height: number): void {
  W = width
  H = height
}

/** 创建尘埃粒子 */
function mkDust(n: number): void {
  dust = []
  for (let i = 0; i < n; i++) {
    dust.push({
      x: Math.random() * W,
      y: Math.random() * H,
      vx: (Math.random() - 0.5) * 0.16,
      vy: (Math.random() - 0.5) * 0.16,
      z: 0.3 + Math.random() * 0.7,
    })
  }
}

/** 获取主题色（dark/light） */
function cols(isDark: boolean) {
  return isDark
    ? { p: '92,255,176', p2: '54,198,255', l: '92,255,176' }
    : { p: '14,132,168', p2: '14,159,99', l: '14,132,168' }
}

/** 渲染尘埃粒子 */
export function drawDust(
  ctx: CanvasRenderingContext2D,
  isDark: boolean,
  mouseX: number,
  mouseY: number,
): void {
  const n = liveTokens.particles
  if (dust.length !== n) mkDust(n)
  if (n === 0) {
    ctx.clearRect(0, 0, W, H)
    return
  }

  const c = cols(isDark)
  const px = (mouseX - 0.5) * 2
  const py = (mouseY - 0.5) * 2
  ctx.clearRect(0, 0, W, H)

  const parallax = liveTokens.parallax

  for (const a of dust) {
    a.x += a.vx
    a.y += a.vy
    if (a.x < 0) a.x += W
    if (a.x > W) a.x -= W
    if (a.y < 0) a.y += H
    if (a.y > H) a.y -= H

    const ox = a.x + px * parallax * a.z
    const oy = a.y + py * parallax * a.z

    // 连线
    for (const b of dust) {
      if (b === a) continue
      const d = Math.hypot(a.x - b.x, a.y - b.y)
      if (d < 116) {
        const al = (1 - d / 116) * 0.15 * a.z
        ctx.strokeStyle = `rgba(${c.l},${al})`
        ctx.lineWidth = 0.6
        ctx.beginPath()
        ctx.moveTo(ox, oy)
        ctx.lineTo(
          b.x + px * parallax * b.z,
          b.y + py * parallax * b.z,
        )
        ctx.stroke()
      }
    }

    // 粒子点
    ctx.fillStyle = `rgba(${Math.random() > 0.5 ? c.p : c.p2},${0.5 * a.z})`
    ctx.beginPath()
    ctx.arc(ox, oy, 1.1 * a.z, 0, 7)
    ctx.fill()
  }
}