/* ============ Canvas 突触网络（神经放电模型） ============ */

import { liveTokens } from './renderTokens'
import { RISK_COLORS } from './types'
import type { NodeDef, EdgeDef, Pulse, Ripple, LogEntry } from './types'

export const NODES: NodeDef[] = [
  { id: 'metrics.read', label: 'metrics.read', risk: 'info', x: 0.50, y: 0.50 },
  { id: 'deploy.start', label: 'deploy.start', risk: 'warn', x: 0.20, y: 0.30 },
  { id: 'inspect.run', label: 'inspect.run', risk: 'info', x: 0.40, y: 0.18 },
  { id: 'server.list', label: 'server.list', risk: 'info', x: 0.68, y: 0.22 },
  { id: 'mcp.debug', label: 'mcp.debug', risk: 'alert', x: 0.84, y: 0.44 },
  { id: 'audit.export', label: 'audit.export', risk: 'info', x: 0.76, y: 0.74 },
  { id: 'backup.now', label: 'backup.now', risk: 'alert', x: 0.48, y: 0.84 },
  { id: 'config.get', label: 'config.get', risk: 'info', x: 0.22, y: 0.70 },
]

export const EDGES: EdgeDef[] = [
  [0, 1], [0, 2], [0, 3], [0, 4], [0, 5], [0, 6], [0, 7],
  [1, 7], [3, 4], [5, 6], [2, 3],
]

export let pulses: Pulse[] = []
export let ripples: Ripple[] = []
export let logEntries: LogEntry[] = []
let logIdCounter = 0
let hoverNode = -1
let highlightEdge = -1
let highlightUntil = 0
let discharges = 0

/** 节点 flash 状态 */
const nodeFlash: number[] = NODES.map(() => 0)

/** 获取节点位置（相对于 stage 容器） */
export function getNodePos(
  node: NodeDef,
  stageRect: DOMRect,
  pad = 52,
): { x: number; y: number } {
  return {
    x: stageRect.left + pad + node.x * (stageRect.width - 2 * pad),
    y: stageRect.top + pad + node.y * (stageRect.height - 2 * pad),
  }
}

/** 触发一次放电 */
export function firePulse(
  from: number,
  to: number,
  highlight: boolean,
): void {
  const target = NODES[to]
  pulses.push({
    from,
    to,
    progress: 0,
    risk: target.risk,
    tool: target.label,
    highlight: !!highlight,
  })
  if (highlight) {
    const idx = EDGES.findIndex(
      (e) => (e[0] === from && e[1] === to) || (e[0] === to && e[1] === from),
    )
    if (idx >= 0) {
      highlightEdge = idx
      highlightUntil = performance.now() + 1600
    }
  }
}

/** 随机自动放电 */
export function autoFire(): void {
  const leaf = 1 + Math.floor(Math.random() * 7)
  firePulse(0, leaf, false)
}

/** 创建日志条目 */
export function createLog(from: number, to: number): LogEntry {
  const target = NODES[to]
  const result = target.risk === 'alert' ? 'QUEUED' : 'OK'
  discharges++
  const now = new Date()
  const ts = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`
  const entry: LogEntry = {
    id: ++logIdCounter,
    time: ts,
    tool: target.label,
    toolId: target.id,
    risk: target.risk,
    result,
    from,
    to,
    timestamp: now.getTime(),
  }
  logEntries.unshift(entry)
  if (logEntries.length > 50) logEntries.length = 50
  return entry
}

/** 获取放电计数 */
export function getDischargeCount(): number {
  return discharges
}

/** 重置放电计数 */
export function resetDischarges(): void {
  discharges = 0
}

/** 设置 hover 节点 */
export function setHoverNode(idx: number): void {
  hoverNode = idx
}

/** 获取 hover 节点 */
export function getHoverNode(): number {
  return hoverNode
}

/** 渲染突触网络 */
export function drawSynapse(
  ctx: CanvasRenderingContext2D,
  stageRect: DOMRect,
  dt: number,
  now: number,
  isDark: boolean,
  monoFont: string,
): void {
  const W = ctx.canvas.width
  const H = ctx.canvas.height
  const low = liveTokens.parallax < 0.01 // 近似判断 low mode
  const glowR = 4 + liveTokens['depth-blur'] * 7

  ctx.clearRect(0, 0, W, H)

  const edgeColor = isDark
    ? 'rgba(255,255,255,'
    : 'rgba(15,27,45,'

  // 绘制边
  EDGES.forEach((e, i) => {
    const A = getNodePos(NODES[e[0]], stageRect)
    const B = getNodePos(NODES[e[1]], stageRect)
    const hot =
      (i === highlightEdge && now < highlightUntil) ||
      pulses.some(
        (p) =>
          (p.from === e[0] && p.to === e[1]) ||
          (p.from === e[1] && p.to === e[0]),
      )
    const col = RISK_COLORS[NODES[e[1]].risk]
    ctx.strokeStyle = hot
      ? `rgba(${col},.55)`
      : edgeColor + (hot ? '.3' : '.08') + ')'
    ctx.lineWidth = hot ? 1.6 : 1
    ctx.beginPath()
    ctx.moveTo(A.x, A.y)
    ctx.lineTo(B.x, B.y)
    ctx.stroke()
  })

  // 更新脉冲
  for (let i = pulses.length - 1; i >= 0; i--) {
    const p = pulses[i]
    if (!low) p.progress += dt * 0.55 * liveTokens['pulse-speed']

    const A = getNodePos(NODES[p.from], stageRect)
    const B = getNodePos(NODES[p.to], stageRect)
    const e = Math.min(1, p.progress)
    const ee = e < 0.5 ? 2 * e * e : 1 - Math.pow(-2 * e + 2, 2) / 2
    const x = A.x + (B.x - A.x) * ee
    const y = A.y + (B.y - A.y) * ee
    const col = RISK_COLORS[p.risk]

    // 拖尾
    for (let t = 1; t <= liveTokens['pulse-trail']; t++) {
      const te = Math.max(0, e - t * 0.04)
      const tee = te < 0.5 ? 2 * te * te : 1 - Math.pow(-2 * te + 2, 2) / 2
      const tx = A.x + (B.x - A.x) * tee
      const ty = A.y + (B.y - A.y) * tee
      ctx.fillStyle = `rgba(${col},${(1 - t / liveTokens['pulse-trail']) * 0.5})`
      ctx.beginPath()
      ctx.arc(tx, ty, 2.4 * (1 - t / liveTokens['pulse-trail']) + 0.6, 0, 7)
      ctx.fill()
    }

    // 头部光晕
    const hg = ctx.createRadialGradient(x, y, 0, x, y, glowR * 1.6)
    hg.addColorStop(0, `rgba(${col},${p.highlight ? 0.9 : 0.6})`)
    hg.addColorStop(1, `rgba(${col},0)`)
    ctx.fillStyle = hg
    ctx.beginPath()
    ctx.arc(x, y, glowR * 1.6, 0, 7)
    ctx.fill()

    // 头部亮点
    ctx.fillStyle = `rgba(255,255,255,.95)`
    ctx.beginPath()
    ctx.arc(x, y, 2.6, 0, 7)
    ctx.fill()

    if (p.progress >= 1) {
      ripples.push({ x: B.x, y: B.y, progress: 0, risk: p.risk })
      nodeFlash[p.to] = 1
      if (p.highlight) highlightEdge = -1
      pulses.splice(i, 1)
    }
  }

  // 涟漪
  for (let i = ripples.length - 1; i >= 0; i--) {
    const r = ripples[i]
    r.progress += dt * 1.6
    const col = RISK_COLORS[r.risk]
    const rad = r.progress * 34
    const al = (1 - r.progress) * 0.6
    ctx.strokeStyle = `rgba(${col},${al})`
    ctx.lineWidth = 1.4
    ctx.beginPath()
    ctx.arc(r.x, r.y, rad, 0, 7)
    ctx.stroke()
    if (r.progress >= 1) ripples.splice(i, 1)
  }

  // 节点
  NODES.forEach((n, i) => {
    const P = getNodePos(n, stageRect)
    const flash = nodeFlash[i] || 0
    const hover = i === hoverNode
    const breathe = 1 + Math.sin(now * 0.002 + i) * 0.03
    if (nodeFlash[i]) nodeFlash[i] = Math.max(0, flash - dt * 2.2)

    const col = RISK_COLORS[n.risk]
    const R = (hover ? 9 : 7) * (1 + flash * 0.6) * breathe

    // 节点光晕
    const g = ctx.createRadialGradient(
      P.x, P.y, 0,
      P.x, P.y, R + glowR * (0.4 + flash + (hover ? 0.5 : 0)),
    )
    g.addColorStop(0, `rgba(${col},${0.35 + flash * 0.5 + (hover ? 0.2 : 0)})`)
    g.addColorStop(1, `rgba(${col},0)`)
    ctx.fillStyle = g
    ctx.beginPath()
    ctx.arc(P.x, P.y, R + glowR * (0.4 + flash + (hover ? 0.5 : 0)), 0, 7)
    ctx.fill()

    // 节点圆
    ctx.fillStyle = isDark ? '#0C1014' : '#FFFFFF'
    ctx.beginPath()
    ctx.arc(P.x, P.y, R, 0, 7)
    ctx.fill()

    ctx.strokeStyle = `rgba(${col},${hover || flash ? 1 : 0.8})`
    ctx.lineWidth = hover || flash ? 2.2 : 1.6
    ctx.beginPath()
    ctx.arc(P.x, P.y, R, 0, 7)
    ctx.stroke()

    // 标签
    ctx.fillStyle = hover || flash
      ? (isDark ? '#EAF0F4' : '#0E1A28')
      : (isDark ? 'rgba(234,240,244,.6)' : 'rgba(14,26,40,.6)')
    ctx.font = `600 10px ${monoFont}`
    ctx.textAlign = 'center'
    ctx.fillText(n.label, P.x, P.y + R + 14)

    // HUB 标记
    if (i === 0) {
      ctx.fillStyle = `rgba(${col},.5)`
      ctx.font = `600 8px ${monoFont}`
      ctx.fillText('HUB', P.x, P.y - R - 7)
    }
  })
}

/** 重置所有状态 */
export function resetSynapse(): void {
  pulses.length = 0
  ripples.length = 0
  logEntries.length = 0
  logIdCounter = 0
  hoverNode = -1
  highlightEdge = -1
  highlightUntil = 0
  discharges = 0
  for (let i = 0; i < nodeFlash.length; i++) nodeFlash[i] = 0
}