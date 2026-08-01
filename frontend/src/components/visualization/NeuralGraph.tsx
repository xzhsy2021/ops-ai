import { useEffect, useRef, useState } from 'react'

type Node = {
  id: string
  label: string
  risk?: 'info' | 'warn' | 'alert'
  x: number
  y: number
  vx: number
  vy: number
}

type Edge = {
  from: string
  to: string
  active?: boolean
}

type NeuralGraphProps = {
  nodes: Array<{ id: string; label: string; risk?: 'info' | 'warn' | 'alert' }>
  edges?: Edge[]
  className?: string
  style?: React.CSSProperties
}

const riskColors = {
  info: 'var(--risk-read, #38bdf8)',
  warn: 'var(--risk-write, #fb923c)',
  alert: 'var(--risk-destructive, #f43f5e)',
}

export function NeuralGraph({ nodes: nodeDefs, edges = [], className = '', style }: NeuralGraphProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [hoveredNode, setHoveredNode] = useState<string | null>(null)
  const nodesRef = useRef<Node[]>([])
  const animRef = useRef<number>(0)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const resize = () => {
      canvas.width = canvas.offsetWidth * devicePixelRatio
      canvas.height = canvas.offsetHeight * devicePixelRatio
      ctx.scale(devicePixelRatio, devicePixelRatio)
    }
    resize()
    window.addEventListener('resize', resize)

    const w = canvas.offsetWidth
    const h = canvas.offsetHeight
    const cx = w / 2
    const cy = h / 2

    // Layout nodes in a circle
    nodesRef.current = nodeDefs.map((n, i) => {
      const angle = (i / nodeDefs.length) * Math.PI * 2 - Math.PI / 2
      const radius = Math.min(w, h) * 0.3
      return {
        ...n,
        x: cx + Math.cos(angle) * radius,
        y: cy + Math.sin(angle) * radius,
        vx: 0,
        vy: 0,
      }
    })

    const animate = () => {
      const cw = canvas.offsetWidth
      const ch = canvas.offsetHeight
      ctx.clearRect(0, 0, cw, ch)

      const nodes = nodesRef.current
      const nodeMap = new Map(nodes.map((n) => [n.id, n]))

      // Draw edges
      for (const edge of edges) {
        const from = nodeMap.get(edge.from)
        const to = nodeMap.get(edge.to)
        if (!from || !to) continue

        ctx.beginPath()
        ctx.moveTo(from.x, from.y)
        ctx.lineTo(to.x, to.y)
        ctx.strokeStyle = edge.active
          ? 'var(--color-energy, #22E08A)'
          : 'var(--border, rgba(148,163,184,0.12))'
        ctx.lineWidth = edge.active ? 1.5 : 0.5
        ctx.globalAlpha = edge.active ? 0.6 : 0.3
        ctx.stroke()
      }

      ctx.globalAlpha = 1

      // Draw nodes
      for (const node of nodes) {
        const isHovered = hoveredNode === node.id
        const color = riskColors[node.risk || 'info']
        const radius = isHovered ? 10 : 7

        // Glow
        if (isHovered) {
          ctx.beginPath()
          ctx.arc(node.x, node.y, radius + 6, 0, Math.PI * 2)
          ctx.fillStyle = color
          ctx.globalAlpha = 0.15
          ctx.fill()
          ctx.globalAlpha = 1
        }

        // Node circle
        ctx.beginPath()
        ctx.arc(node.x, node.y, radius, 0, Math.PI * 2)
        ctx.fillStyle = color
        ctx.globalAlpha = 0.2
        ctx.fill()
        ctx.strokeStyle = color
        ctx.lineWidth = 1.5
        ctx.globalAlpha = 0.8
        ctx.stroke()
        ctx.globalAlpha = 1

        // Label
        ctx.fillStyle = 'var(--text-primary, #d6dde9)'
        ctx.font = '10px var(--font-mono, monospace)'
        ctx.textAlign = 'center'
        ctx.fillText(node.label, node.x, node.y + radius + 14)
      }

      animRef.current = requestAnimationFrame(animate)
    }

    animate()

    const handleMouseMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect()
      const mx = e.clientX - rect.left
      const my = e.clientY - rect.top
      const found = nodesRef.current.find((n) => {
        const dx = n.x - mx
        const dy = n.y - my
        return Math.sqrt(dx * dx + dy * dy) < 12
      })
      setHoveredNode(found?.id || null)
      canvas.style.cursor = found ? 'pointer' : 'default'
    }

    canvas.addEventListener('mousemove', handleMouseMove)

    return () => {
      cancelAnimationFrame(animRef.current)
      window.removeEventListener('resize', resize)
      canvas.removeEventListener('mousemove', handleMouseMove)
    }
  }, [nodeDefs, edges, hoveredNode])

  return (
    <canvas
      ref={canvasRef}
      className={className}
      style={{
        width: '100%',
        height: '100%',
        minHeight: 200,
        display: 'block',
        ...style,
      }}
      aria-label="工具调用关系图"
    />
  )
}