import { useEffect, useRef } from 'react'

type PulseLineProps = {
  active?: boolean
  color?: string
  speed?: number
  trailLength?: number
  className?: string
  style?: React.CSSProperties
}

export function PulseLine({
  active = true,
  color = 'var(--color-energy, #22E08A)',
  speed = 1,
  trailLength = 12,
  className = '',
  style,
}: PulseLineProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const animRef = useRef<number>(0)
  const progressRef = useRef(0)

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

    const animate = () => {
      const cw = canvas.offsetWidth
      const ch = canvas.offsetHeight
      ctx.clearRect(0, 0, cw, ch)

      if (active) {
        progressRef.current += 0.005 * speed
        if (progressRef.current > 1) progressRef.current = 0

        const progress = progressRef.current

        // Draw pulse trail
        const steps = trailLength
        for (let i = 0; i < steps; i++) {
          const t = ((progress - (i / steps) * 0.3 + 1) % 1)
          const x = t * cw
          const alpha = (1 - i / steps) * 0.6
          const radius = 2 + (1 - i / steps) * 2

          ctx.beginPath()
          ctx.arc(x, ch / 2, radius, 0, Math.PI * 2)
          ctx.fillStyle = color
          ctx.globalAlpha = alpha
          ctx.fill()
        }

        ctx.globalAlpha = 1

        // Draw pulse line
        const gradient = ctx.createLinearGradient(0, 0, cw, 0)
        gradient.addColorStop(0, 'transparent')
        gradient.addColorStop(0.4, color)
        gradient.addColorStop(0.6, color)
        gradient.addColorStop(1, 'transparent')

        ctx.beginPath()
        ctx.moveTo(0, ch / 2)
        ctx.lineTo(cw, ch / 2)
        ctx.strokeStyle = gradient
        ctx.lineWidth = 1
        ctx.globalAlpha = 0.2
        ctx.stroke()
        ctx.globalAlpha = 1
      }

      animRef.current = requestAnimationFrame(animate)
    }

    animRef.current = requestAnimationFrame(animate)

    return () => {
      cancelAnimationFrame(animRef.current)
      window.removeEventListener('resize', resize)
    }
  }, [active, color, speed, trailLength])

  return (
    <canvas
      ref={canvasRef}
      className={className}
      style={{
        width: '100%',
        height: 24,
        display: 'block',
        ...style,
      }}
      aria-hidden="true"
    />
  )
}