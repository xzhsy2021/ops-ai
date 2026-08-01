/* ============ 神经突触实验室 · 页面组件 ============ */
/* 视觉参考 Neural Space 工作台 (rail + topbar + content) */
/* 功能遵循原始规范: render tokens × agent discharge */

import { useEffect, useRef, useCallback, useState } from 'react'
import '../theme/neural-lab.css'
import { createRenderEngine } from '../renderer/engine'
import type { RenderEngine } from '../renderer/engine'
import {
  NODES, EDGES, logEntries, firePulse, createLog,
  drawSynapse, setHoverNode, getDischargeCount, resetSynapse,
  PRESETS, liveTokens, CTRL_DEFS, setToken, applyPreset, isCustom,
} from '../renderer'
import type { FxMode, LogEntry } from '../renderer'

const WD = ['日', '一', '二', '三', '四', '五', '六']
const pad = (n: number) => String(n).padStart(2, '0')

/* ============ SVG Icons ============ */
const svg = (p: string) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${p}</svg>`

const ICONS = {
  dash: '<path d="M3 13h8V3H3zM13 21h8V11h-8zM3 21h8v-6H3zM13 3v6h8V3z"/>',
  conf: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M9 13h6M9 17h6"/>',
  rocket: '<path d="M4.5 16.5c-1.5 1.3-2 5-2 5s3.7-.5 5-2c.7-.8.7-2 0-2.8a2 2 0 0 0-3 0zM12 15l-3-3a22 22 0 0 1 8-10c2.5 0 5 2.5 5 5a22 22 0 0 1-10 8z"/>',
  task: '<path d="M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
  heart: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/>',
  server: '<rect x="2" y="3" width="20" height="8" rx="2"/><rect x="2" y="13" width="20" height="8" rx="2"/><path d="M6 7h.01M6 17h.01"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
  flow: '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4v6a4 4 0 0 1-4 4H6M6 9v3"/>',
  wrench: '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.5-.7-.7-2.5z"/>',
  audit: '<path d="M9 2h6a1 1 0 0 1 1 1v1h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2V3a1 1 0 0 1 1-1z"/><path d="M9 12l2 2 4-4"/>',
  ai: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 9h6v6H9zM9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2"/>',
  lab: '<circle cx="6" cy="6" r="2.4"/><circle cx="18" cy="7" r="2.4"/><circle cx="12" cy="17" r="2.4"/><path d="M7.8 7.2 10.4 15M16.4 8.6 13.4 15M8 6.4 16 6.8"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  keys: '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  bolt: '<path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z"/>',
}

const NAV = [
  { icon: 'dash', title: '工作台', desc: '运行健康与快捷入口', path: '/' },
  { icon: 'conf', title: '配置中心', desc: '项目、服务、环境与分组', path: '/systems' },
  { icon: 'rocket', title: '发布', desc: '发布预检与部署日志', path: '/deploy' },
  { icon: 'task', title: '任务', desc: '平台任务与执行状态', path: '/tasks' },
  { icon: 'heart', title: '状态诊断', desc: '平台状态、安装自检', path: '/system' },
  { icon: 'shield', title: '巡检中心', desc: '轻量巡检、台账与报告', path: '/inspection' },
  { icon: 'server', title: '服务器', desc: '资产、终端与文件', path: '/servers' },
  { icon: 'file', title: '文件', desc: '本地发布包管理', path: '/files' },
  { icon: 'flow', title: '流程', desc: '部署流程编排', path: '/pipelines' },
  { icon: 'wrench', title: '维护', desc: '备份与运行维护', path: '/maintenance' },
  { icon: 'audit', title: '审计', desc: '操作审计与导出', path: '/audit' },
  { icon: 'lab', title: '神经突触实验室', desc: 'Render Tokens × Agent Discharge', path: '/lab' },
]

const RISK_VARS: Record<string, string> = {
  info: '--n-info',
  warn: '--n-warn',
  alert: '--n-alert',
}

const NODE_ICONS: Record<string, string> = {
  'metrics.read': '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  'deploy.start': ICONS.rocket,
  'inspect.run': ICONS.shield,
  'server.list': ICONS.server,
  'mcp.debug': ICONS.ai,
  'audit.export': ICONS.audit,
  'backup.now': ICONS.upload,
  'config.get': ICONS.file,
}

export default function NeuralLabPage() {
  const glCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const dustCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const synCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const engineRef = useRef<RenderEngine | null>(null)
  const stageRef = useRef<HTMLDivElement>(null)
  const tipRef = useRef<HTMLDivElement>(null)
  const holoRef = useRef<HTMLDivElement>(null)
  const [dischargeCount, setDischargeCount] = useState(0)
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [currentMode, setCurrentMode] = useState<FxMode>('balanced')
  const [customMode, setCustomMode] = useState(false)
  const [theme, setThemeState] = useState<'light' | 'dark'>('dark')
  const [density, setDensity] = useState<'full' | 'compact'>('full')
  const [commandOpen, setCommandOpen] = useState(false)
  const [fps, setFps] = useState(0)
  const [clock, setClock] = useState('')
  const [date, setDate] = useState('')
  const [latency, setLatency] = useState(12)
  const [selNode, setSelNode] = useState(0)
  const [collapsed, setCollapsed] = useState(false)
  const toastTimer = useRef<ReturnType<typeof setTimeout>>()

  // 初始化引擎
  useEffect(() => {
    // 初始化 data attributes
    document.documentElement.dataset.density = 'full'
    document.documentElement.dataset.fx = 'balanced'
    document.documentElement.dataset.theme = 'dark'

    const gl = glCanvasRef.current
    const dust = dustCanvasRef.current
    const syn = synCanvasRef.current
    if (!gl || !dust || !syn) return

    const engine = createRenderEngine(gl, dust, syn)
    engineRef.current = engine
    engine.start()

    const monoFont = getComputedStyle(document.documentElement)
      .getPropertyValue('--font-mono').trim() || 'monospace'

    engine.setSynDrawCallback((ctx, dt, now) => {
      if (!stageRef.current) return
      const rect = stageRef.current.getBoundingClientRect()
      drawSynapse(ctx, rect, dt, now, engine.currentTheme === 'dark', monoFont)
    })

    const fpsInterval = setInterval(() => setFps(engine.fps), 500)
    return () => {
      engine.stop()
      clearInterval(fpsInterval)
    }
  }, [])

  // 轮询放电计数和日志
  useEffect(() => {
    const interval = setInterval(() => {
      setDischargeCount(getDischargeCount())
      setLogs([...logEntries])
    }, 200)
    return () => clearInterval(interval)
  }, [])

  // 自动放电循环
  const autoTimerRef = useRef(0)
  useEffect(() => {
    if (currentMode === 'low') return
    const interval = setInterval(() => {
      autoTimerRef.current += 1
      const iv = currentMode === 'full' ? 1.5 : 2.4
      if (autoTimerRef.current >= iv * 5) {
        autoTimerRef.current = 0
        autoFireDischarge()
      }
    }, 200)
    return () => clearInterval(interval)
  }, [currentMode])

  const autoFireDischarge = useCallback(() => {
    const leaf = 1 + Math.floor(Math.random() * 7)
    firePulse(0, leaf, false)
    createLog(0, leaf)
  }, [])

  // 时钟 + 抖动遥测
  useEffect(() => {
    const tick = () => {
      const d = new Date()
      setClock(`${pad(d.getHours())}:${pad(d.getMinutes())}`)
      setDate(`${pad(d.getMonth() + 1)}/${pad(d.getDate())} 周${WD[d.getDay()]} · Local`)
      setLatency(10 + Math.floor(Math.random() * 6))
    }
    tick()
    const interval = setInterval(tick, currentMode === 'low' ? 60000 : 1000)
    return () => clearInterval(interval)
  }, [currentMode])

  // 注入放电
  const handleInject = useCallback(() => {
    const leaf = 1 + Math.floor(Math.random() * 7)
    firePulse(0, leaf, true)
    createLog(0, leaf)
  }, [])

  // 选中节点（cover-flow 风格点击切换）
  const handleCardClick = useCallback((idx: number) => {
    setSelNode(idx)
    if (idx !== 0) {
      const edge = EDGES.find(e => e[1] === idx) || EDGES.find(e => e[0] === idx)
      if (edge) {
        const from = edge[0] === idx ? edge[1] : edge[0]
        firePulse(from, idx, true)
        createLog(from, idx)
      }
    } else {
      // 中心 HUB 点击：向所有叶子放电
      for (let i = 1; i <= 7; i++) {
        firePulse(0, i, false)
        createLog(0, i)
      }
    }
  }, [])

  // hover 节点显示 tooltip
  const handleCardHover = useCallback((idx: number | null) => {
    setHoverNode(idx ?? -1)
    if (idx !== null && idx >= 0 && tipRef.current) {
      const node = NODES[idx]
      const tip = tipRef.current
      const riskVar = RISK_VARS[node.risk] || '--n-info'
      tip.innerHTML = `<b>${node.label}</b><span style="color:var(${riskVar})">${node.risk.toUpperCase()} · click to discharge</span>`
      tip.classList.add('on')
      // 简单放在底部
      tip.style.left = '50%'
      tip.style.bottom = '70px'
      tip.style.top = 'auto'
      tip.style.transform = 'translateX(-50%)'
    } else if (tipRef.current) {
      tipRef.current.classList.remove('on')
    }
  }, [])

  // 日志行重放
  const handleLogReplay = useCallback((entry: LogEntry) => {
    firePulse(entry.from, entry.to, true)
    setSelNode(entry.to)
  }, [])

  // 更新 utilization 进度条
  function updateUtilTrack() {
    const t = liveTokens
    const pct = Math.round((t.glow / 1.5 + t.nebula / 1.5 + t.parallax / 80) / 3 * 100)
    const fill = document.querySelector('#util-track .fill') as HTMLElement
    if (fill) fill.style.width = pct + '%'
    const lab = document.querySelector('#util-track-lab')
    if (lab) lab.textContent = pct + '%'
  }

  // 滑块变化
  const handleRangeChange = useCallback((key: string, value: number) => {
    setToken(key as keyof typeof liveTokens, value)
    const custom = isCustom(currentMode)
    setCustomMode(custom)
    const ctrl = CTRL_DEFS.find(c => c.key === key)
    if (ctrl) {
      const pct = ((value - ctrl.min) / (ctrl.max - ctrl.min)) * 100
      const input = document.querySelector(`#r-${key}`) as HTMLInputElement
      if (input) input.style.setProperty('--p', pct + '%')
    }
    // 更新显示值
    const vSpan = document.querySelector(`#v-${key}`)
    if (vSpan) vSpan.textContent = String(value)
    // 更新进度条
    updateUtilTrack()
  }, [currentMode])

  // 分段按钮切换
  const handleSegChange = useCallback((key: string, value: number) => {
    setToken(key as keyof typeof liveTokens, value)
    const custom = isCustom(currentMode)
    setCustomMode(custom)
    const container = document.querySelector(`#s-${key}`)
    if (container) {
      container.querySelectorAll('button').forEach(b => {
        b.classList.toggle('on', Number(b.dataset.v) === value)
      })
    }
    // 更新显示值
    const vSpan = document.querySelector(`#v-${key}`)
    if (vSpan) vSpan.textContent = String(value)
  }, [currentMode])

  // FX 模式切换
  const handleFxChange = useCallback((mode: FxMode) => {
    applyPreset(mode)
    setCurrentMode(mode)
    setCustomMode(false)
    document.documentElement.dataset.fx = mode
    engineRef.current?.setFx(mode)
    // 同步 UI
    const v = liveTokens
    CTRL_DEFS.forEach(({ key, type, min, max }) => {
      const val = v[key as keyof typeof v]
      // 更新显示值
      const vSpan = document.querySelector(`#v-${key}`)
      if (vSpan) vSpan.textContent = String(val)
      if (type === 'range') {
        const input = document.querySelector(`#r-${key}`) as HTMLInputElement
        if (input) {
          input.value = String(val)
          const pct = ((val - min) / (max - min)) * 100
          input.style.setProperty('--p', pct + '%')
        }
      } else {
        const container = document.querySelector(`#s-${key}`)
        if (container) {
          container.querySelectorAll('button').forEach(b => {
            b.classList.toggle('on', Number(b.dataset.v) === val)
          })
        }
      }
    })
    // 同步 FX bar
    document.querySelectorAll('#fxbar .seg').forEach(b => {
      b.classList.toggle('on', b.classList.contains(`seg-${mode}`))
    })
    // 同步 holo
    if (holoRef.current) {
      const sel = NODES[selNode]
      holoRef.current.querySelector('.k')!.textContent = sel?.label?.toUpperCase() || '—'
      holoRef.current.querySelector('.v')!.textContent = sel ? `${dischargeCount}` : '0'
    }
    // 同步进度条
    updateUtilTrack()
  }, [selNode, dischargeCount])

  // 主题切换
  const toggleTheme = useCallback(() => {
    const next = theme === 'dark' ? 'light' : 'dark'
    setThemeState(next)
    document.documentElement.dataset.theme = next
    engineRef.current?.setTheme(next)
  }, [theme])

  // 密度切换
  const toggleDensity = useCallback(() => {
    const next = density === 'full' ? 'compact' : 'full'
    setDensity(next)
    document.documentElement.dataset.density = next
  }, [density])

  // 重置（清空 logs + discharge count + 节点选择）
  const handleReset = useCallback(() => {
    setSelNode(0)
    setCustomMode(false)
    resetSynapse()
    applyPreset(currentMode)
    showToastMsg('reset · cleared logs & restored preset')
  }, [currentMode])

  // 动态添加一个突触节点（演示）
  const handleAddSynapse = useCallback(() => {
    setSelNode((i) => (i + 1) % NODES.length)
    const leaf = 1 + Math.floor(Math.random() * 7)
    firePulse(0, leaf, true)
    createLog(0, leaf)
    showToastMsg('synapse added · discharge fired')
  }, [])

  // Ctrl+K 命令面板
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setCommandOpen((v) => !v)
      }
      if (e.key === 'Escape' && commandOpen) {
        setCommandOpen(false)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [commandOpen])

  // cover-flow 节点列布局
  const cardLayout = (i: number) => {
    const off = i - selNode
    const focus = i === selNode
    const x = off * 68
    const ry = off * 13
    const tz = -Math.abs(off) * 68 + (focus ? 72 : 0)
    const sc = focus ? 1.06 : 1
    const op = Math.max(0.07, 1 - Math.abs(off) * 0.17)
    return {
      transform: `translateX(${x}px) rotateY(${ry}deg) translateZ(${tz}px) scale(${sc})`,
      opacity: i === selNode ? 1 : op,
      zIndex: 100 - Math.abs(off),
      filter:
        i === selNode || currentMode !== 'full'
          ? 'none'
          : `blur(${Math.min(4, Math.abs(off) * 0.9)}px)`,
    }
  }

  // 导出 DTCG
  const handleExport = useCallback(() => {
    const json = exportJSON()
    navigator.clipboard.writeText(json)
    showToastMsg('DTCG copied · render 随当前 FX mode')
  }, [currentMode])

  const handleDownload = useCallback(() => {
    const json = exportJSON()
    const blob = new Blob([json], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'ops-synapse-tokens.json'
    a.click()
    showToastMsg('downloaded · ops-synapse-tokens.json')
  }, [currentMode])

  function exportJSON() {
    const live = { ...liveTokens }
    const baseRender = toDTCG(PRESETS.full)
    const balRender = toDTCG(PRESETS.balanced)
    const lowRender = toDTCG(PRESETS.low)
    Object.assign(
      currentMode === 'full' ? baseRender : currentMode === 'balanced' ? balRender : lowRender,
      toDTCG(live),
    )
    return JSON.stringify({
      $metadata: {
        tokenSetOrder: ['base', 'light', 'dark', 'fx-full', 'fx-balanced', 'fx-low', 'density-full', 'density-compact'],
        collections: {
          base: { modes: ['default'] },
          color: { modes: ['Light', 'Dark'] },
          shadow: { modes: ['Light', 'Dark'] },
          'fx+render': { modes: ['Full', 'Balanced', 'Low'] },
          density: { modes: ['Full', 'Compact'] },
        },
      },
      $themes: [
        { id: 'light', name: 'Light', group: 'Theme', selectedTokenSets: { base: 'enabled', light: 'enabled', 'shadow-light': 'enabled' } },
        { id: 'dark', name: 'Dark', group: 'Theme', selectedTokenSets: { base: 'enabled', dark: 'enabled', 'shadow-dark': 'enabled' } },
        { id: 'fx-full', name: 'Full', group: 'FX+Render', selectedTokenSets: { 'fx-full': 'enabled' } },
        { id: 'fx-balanced', name: 'Balanced', group: 'FX+Render', selectedTokenSets: { 'fx-balanced': 'enabled' } },
        { id: 'fx-low', name: 'Low', group: 'FX+Render', selectedTokenSets: { 'fx-low': 'enabled' } },
      ],
      base: {
        font: { sans: { $value: 'Sora, system-ui, sans-serif', $type: 'fontFamilies' }, mono: { $value: 'JetBrains Mono, ui-monospace, monospace', $type: 'fontFamilies' }, display: { $value: 'Chakra Petch, sans-serif', $type: 'fontFamilies' } },
        radius: { md: { $value: '12px', $type: 'dimension' }, lg: { $value: '16px', $type: 'dimension' }, pill: { $value: '999px', $type: 'dimension' } },
      },
      light: { space: { glow: { $value: 'rgba(14,132,168,0.10)', $type: 'color' }, nebula: { $value: 'rgba(14,159,99,0.05)', $type: 'color' }, grid: { $value: 'rgba(15,27,45,0.05)', $type: 'color' } } },
      dark: { space: { glow: { $value: 'rgba(34,224,138,0.22)', $type: 'color' }, nebula: { $value: 'rgba(34,224,138,0.12)', $type: 'color' }, grid: { $value: 'rgba(92,255,176,0.04)', $type: 'color' } } },
      'fx-full': { fx: { scan: { $value: 1, $type: 'number' }, '3d': { $value: 1, $type: 'number' } }, render: baseRender },
      'fx-balanced': { fx: { scan: { $value: 0, $type: 'number' }, '3d': { $value: 1, $type: 'number' } }, render: balRender },
      'fx-low': { fx: { scan: { $value: 0, $type: 'number' }, '3d': { $value: 0, $type: 'number' } }, render: lowRender },
    }, null, 2)
  }

  function toDTCG(p: Record<string, number>) {
    const o: Record<string, any> = {}
    for (const k in p) o[k] = { $value: p[k], $type: 'number' }
    return o
  }

  function showToastMsg(msg: string) {
    const el = document.querySelector('#labToast')
    if (el) {
      el.textContent = msg
      el.classList.add('show')
      clearTimeout(toastTimer.current)
      toastTimer.current = setTimeout(() => el.classList.remove('show'), 1900)
    }
  }

  // 初始化控件 UI
  useEffect(() => {
    const v = liveTokens
    CTRL_DEFS.forEach(({ key, type, min, max }) => {
      const val = v[key as keyof typeof v]
      // 更新显示值
      const vSpan = document.querySelector(`#v-${key}`)
      if (vSpan) vSpan.textContent = String(val)
      if (type === 'range') {
        const input = document.querySelector(`#r-${key}`) as HTMLInputElement
        if (input) {
          input.value = String(val)
          const pct = ((val - min) / (max - min)) * 100
          input.style.setProperty('--p', pct + '%')
        }
      } else {
        const container = document.querySelector(`#s-${key}`)
        if (container) {
          container.querySelectorAll('button').forEach(b => {
            b.classList.toggle('on', Number(b.dataset.v) === val)
          })
        }
      }
    })
  }, [])

  // Reveal 动画
  useEffect(() => {
    const io = new IntersectionObserver(
      entries => entries.forEach(x => {
        if (x.isIntersecting) x.target.classList.add('in')
      }),
      { threshold: 0.1 },
    )
    document.querySelectorAll('.reveal').forEach((el, i) => {
      ;(el as HTMLElement).style.transitionDelay = (i * 55) + 'ms'
      io.observe(el)
    })
    return () => io.disconnect()
  }, [])

  const sel = NODES[selNode] || NODES[0]
  const selStat = sel.risk === 'alert' ? 'QUEUED' : sel.risk === 'warn' ? 'PENDING' : 'OK'
  const selVar = RISK_VARS[sel.risk] || '--n-info'

  return (
    <>
      {/* 三张全屏画布 */}
      <canvas ref={glCanvasRef} style={{ position: 'fixed', inset: 0, zIndex: 0, pointerEvents: 'none' }} />
      <canvas ref={dustCanvasRef} style={{ position: 'fixed', inset: 0, zIndex: 1, pointerEvents: 'none' }} />
      <canvas ref={synCanvasRef} style={{ position: 'fixed', inset: 0, zIndex: 2, pointerEvents: 'none' }} />

      <div className={`neural-lab-page${collapsed ? ' collapsed' : ''}`}>
        <span className="hc-fix tl" />
        <span className="hc-fix tr" />
        <span className="hc-fix bl" />
        <span className="hc-fix br" />

        {/* ============ RAIL ============ */}
        <aside className="rail">
          <div className="rail-top">
            <div className="rail-logo" dangerouslySetInnerHTML={{ __html: svg(ICONS.lab) }} />
            <div className="rail-brand">
              <b>OPS</b>
              <span>NEURAL SYNAPSE</span>
            </div>
            <button className="collapse" onClick={() => setCollapsed((v) => !v)}>‹</button>
          </div>
          <div className="rail-label">
            <span>WORKSPACE</span>
            <span className="mono">SEC-01</span>
          </div>
          <nav className="rail-nav">
            {NAV.map((n, i) => (
              <a key={n.title} className={i === NAV.length - 1 ? 'on' : ''} href={n.path} onClick={(e) => { if (n.path !== '/lab') { e.preventDefault(); window.location.href = n.path } }}>
                <span className="rail-tile" dangerouslySetInnerHTML={{ __html: svg(ICONS[n.icon as keyof typeof ICONS]) }} />
                <span className="t">
                  <b>{n.title}</b>
                  <small>{n.desc}</small>
                </span>
              </a>
            ))}
          </nav>
          <div className="rail-foot">
            <span className="dot" /> <b>System Online</b>
            <span className="mono up">API / Session Ready</span>
          </div>
        </aside>

        {/* ============ MAIN ============ */}
        <div className="main-col">
          {/* TOPBAR */}
          <header className="topbar">
            <div className="crumb up">OPS / 神经突触实验室<b>Render Tokens × Agent Discharge</b></div>
            <div className="cap clock">
              <b>{clock || '--:--'}</b>
              <small>{date || '-- · Local'}</small>
            </div>
            <div className="cap ver">
              <span className="d" /> 团队稳定版 · <b>2.1.13</b>
            </div>
            <div className="cmdslot" onClick={() => setCommandOpen(true)} title="Ctrl K">
              <span dangerouslySetInnerHTML={{ __html: svg(ICONS.search) }} />
              <span>搜索或跳转</span>
              <span className="k">Ctrl K</span>
            </div>
            <div className="tools">
              <button className="sw" onClick={toggleTheme} title="昼夜切换">
                <span dangerouslySetInnerHTML={{ __html: svg(ICONS.moon) }} />
                <span>{theme === 'dark' ? 'DARK' : 'LIGHT'}</span>
              </button>
              <button className="sw" title="专注">
                <span dangerouslySetInnerHTML={{ __html: svg(ICONS.eye) }} />
                <span>专注</span>
              </button>
              <button className="sw" title="快捷键">
                <span dangerouslySetInnerHTML={{ __html: svg(ICONS.keys) }} />
                <span>键</span>
              </button>
              <div className="who"><b>admin</b> <small>(admin)</small></div>
              <button className="sw" onClick={() => window.location.href = '/'}>退出</button>
            </div>
          </header>

          {/* CONTENT */}
          <div className="content">
            {/* Toolbar */}
            <div className="toolbar reveal">
              <div className="group">
                <span className="pill">◰ Folders <b>14</b></span>
                <span className="pill">◳ Tags <b>44</b></span>
                <span className="pill" onClick={toggleDensity}>▤ {density === 'full' ? '完整' : '紧凑'} ▾</span>
              </div>
              <div className="group">
                <span className="pill" onClick={handleExport}>⤓ Export TOKEN</span>
                <span className="pill" onClick={handleReset}>⟳ Reset</span>
                <span className="pill" onClick={handleAddSynapse}>＋ Add Synapse</span>
              </div>
            </div>

            {/* STAGE - 神经突触场（cover-flow 节点） */}
            <section
              className="stage glass cornered reveal"
              ref={stageRef}
              onMouseLeave={() => handleCardHover(null)}
            >
              <span className="c a" /><span className="c b" /><span className="c d" /><span className="c e" />
              <span className="coord l up">SYNAPSE-FIELD · 8 NODES</span>
              <span className="coord r up" id="fps">{fps} fps · fat-client</span>
              <span className="coord bl up">CLICK NODE ↔ DISCHARGE</span>

              <div className="arc" />

              {/* 全息读数浮层 */}
              <div className="holo on" ref={holoRef}>
                <span className="scanln" />
                <span className="ar">→</span>
                <div className="k">{sel.label} · {sel.risk.toUpperCase()}</div>
                <div className="v">{dischargeCount}</div>
                <span className={`badge ${sel.risk === 'alert' ? 'alert' : sel.risk === 'warn' ? 'warn' : 'info'}`} data-state={sel.risk}>
                  {selStat}
                </span>
              </div>

              <div className="flow" id="flow">
                {NODES.map((n, i) => (
                  <div
                    key={n.id}
                    className={`node-card${i === selNode ? ' sel' : ''}`}
                    style={cardLayout(i)}
                    onClick={() => handleCardClick(i)}
                    onMouseEnter={() => handleCardHover(i)}
                  >
                    <div className="ic" dangerouslySetInnerHTML={{ __html: svg(NODE_ICONS[n.id] || ICONS.flow) }} />
                    <div>
                      <div className="ln m" />
                      <div className="ln s" style={{ marginTop: 6 }} />
                      <div className="ln m" style={{ marginTop: 6 }} />
                    </div>
                    <span className="tag up">{n.label}</span>
                  </div>
                ))}
              </div>

              <div className="tip" ref={tipRef} />

              <button className="inject" onClick={handleInject}>
                <span dangerouslySetInnerHTML={{ __html: svg(ICONS.bolt) }} /> INJECT CALL
              </button>
            </section>

            {/* TELEMETRY 遥测条 */}
            <div className="telemetry glass reveal">
              <span className="t"><span className="blink" />UPLINK <b>STABLE</b></span>
              <span className="t">LATENCY <b>{latency}ms</b></span>
              <span className="t">NODES <b>8/8</b></span>
              <span className="t">DISCHARGES <b>{dischargeCount}</b></span>
              <span className="t">RENDER <b id="tel-fx">{currentMode.toUpperCase()}</b></span>
              <span className="t" style={{ marginLeft: 'auto' }}>THIN-SERVER · FAT-CLIENT</span>
            </div>

            {/* GRID 3 - 三列卡片 */}
            <section className="grid3">
              {/* 左：SYNAPSE LOG */}
              <div className="panel glass cornered reveal">
                <span className="c a" /><span className="c b" /><span className="c d" /><span className="c e" />
                <div className="pt">Synapse Log</div>
                <div className="ps">agent tool calls · live telemetry</div>
                <div className="log-list">
                  {logs.length === 0 ? (
                    <div className="log-empty">
                      <b>等待第一次突触放电…</b>
                      <p>当 Agent 调用 MCP tool，或你注入一次调用，对应节点间会拉出能量弧，并在此留痕。点任意记录可重放。</p>
                    </div>
                  ) : (
                    logs.slice(0, 6).map((entry) => (
                      <div
                        key={entry.id}
                        className="log-row"
                        onClick={() => handleLogReplay(entry)}
                      >
                        <span className={`bar ${entry.risk}`} />
                        <div className="l" style={{ flex: 1 }}>
                          {entry.tool}
                          <small>{entry.time} · from {NODES[entry.from]?.label} · data-state="{entry.risk}"</small>
                        </div>
                        <span className={`badge ${entry.result === 'QUEUED' ? 'warn' : 'online'}`}>
                          {entry.result}
                        </span>
                      </div>
                    ))
                  )}
                </div>
              </div>

              {/* 中：RENDER PHYSICS（能量卡片） */}
              <div className="panel energy glass cornered reveal">
                <span className="c a" /><span className="c b" /><span className="c d" /><span className="c e" />
                <div className="phys-header">
                  <b>Render Physics</b>
                  <span className="mode up">MODE · {currentMode.toUpperCase()}</span>
                  <span className={`custom${customMode ? ' on' : ''}`}>● CUSTOM — 导出即所得</span>
                </div>
                <div className="ctrls">
                  {CTRL_DEFS.map(({ key, label, min, max, step, type, segValues }) => (
                    <div className="ctrl" key={key}>
                      <label>
                        {label}
                        <span className="v" id={`v-${key}`}>{liveTokens[key]}</span>
                      </label>
                      {type === 'range' ? (
                        <input
                          type="range"
                          id={`r-${key}`}
                          min={min}
                          max={max}
                          step={step}
                          defaultValue={liveTokens[key]}
                          onInput={(e) => handleRangeChange(key, Number((e.target as HTMLInputElement).value))}
                        />
                      ) : (
                        <div className="miniseg" id={`s-${key}`}>
                          {(segValues || []).map((v) => (
                            <button
                              key={v}
                              data-v={v}
                              className={liveTokens[key] === v ? 'on' : ''}
                              onClick={() => handleSegChange(key, v)}
                            >
                              {v}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
                {/* 进度条 + 文件行 + 审查（参考实现风格） */}
                <div className="track-lab"><span>Render Token Utilization</span><span id="util-track-lab">{Math.round((liveTokens.glow / 1.5 + liveTokens.nebula / 1.5 + liveTokens.parallax / 80) / 3 * 100)}%</span></div>
                <div className="track" id="util-track"><div className="fill" style={{ width: `${Math.round((liveTokens.glow / 1.5 + liveTokens.nebula / 1.5 + liveTokens.parallax / 80) / 3 * 100)}%` }}></div></div>
                <div className="filerow">
                  <div className="fi"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg></div>
                  <div><div className="fn">ops-synapse-tokens.json</div><div className="fp">render@{currentMode} · {new Date().toLocaleDateString('zh-CN')}</div></div>
                </div>
                <div className="review">
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className="stack"><span></span><span></span></span>
                    Engine Coordinator
                  </div>
                  <span className="mono" style={{ fontSize: 10 }}>{fps} FPS</span>
                </div>
                <div className="export-row">
                  <button className="gh" onClick={handleExport}>⧉ 复制 TOKEN</button>
                  <button className="gh" onClick={handleDownload}>⤓ 下载 JSON</button>
                </div>
              </div>

              {/* 右：Quick Stats */}
              <div className="panel glass cornered reveal">
                <span className="c a" /><span className="c b" /><span className="c d" /><span className="c e" />
                <div className="pt">Quick Stats</div>
                <div className="ps">current node · risk profile</div>
                <div className="log-list">
                  <div className="log-row">
                    <div className="l" style={{ flex: 1 }}>
                      PARTICLES
                      <small>dust density</small>
                    </div>
                    <span className="r" id="ro-par">{liveTokens.particles}</span>
                  </div>
                  <div className="log-row">
                    <div className="l" style={{ flex: 1 }}>
                      GLOW
                      <small>shader volumetric</small>
                    </div>
                    <span className="r" id="ro-glow">{liveTokens.glow.toFixed(2)}</span>
                  </div>
                  <div className="log-row">
                    <div className="l" style={{ flex: 1 }}>
                      NEBULA
                      <small>star field</small>
                    </div>
                    <span className="r" id="ro-neb">{liveTokens.nebula.toFixed(2)}</span>
                  </div>
                  <div className="log-row">
                    <div className="l" style={{ flex: 1 }}>
                      PARALLAX
                      <small>mouse 视差</small>
                    </div>
                    <span className="r" id="ro-px">{liveTokens.parallax}</span>
                  </div>
                  <div className="log-row">
                    <div className="l" style={{ flex: 1 }}>
                      Selected
                      <small>{sel.label}</small>
                    </div>
                    <span className={`badge ${sel.risk === 'alert' ? 'alert' : sel.risk === 'warn' ? 'warn' : 'info'}`} data-state={sel.risk}>
                      {sel.risk.toUpperCase()}
                    </span>
                  </div>
                  <div className="log-row" style={{ borderBottom: 'none' }}>
                    <div className="l" style={{ flex: 1 }}>
                      Engine Status
                      <small>render coordinator</small>
                    </div>
                    <span className="here" id="engine-status">WE'RE HERE</span>
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 11, fontSize: 11.5 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className="stack"><span></span><span></span></span>
                    Synapse Network
                  </div>
                  <span className="mono" style={{ fontSize: 10 }}>{NODES.length} nodes</span>
                </div>
              </div>
            </section>

            {/* FX BAR */}
            <div className="fxbar reveal" id="fxbar">
              <span className="up">RENDER-FX</span>
              <span className={`seg seg-full${currentMode === 'full' ? ' on' : ''}`} onClick={() => handleFxChange('full')}>FULL</span>
              <span className={`seg seg-balanced${currentMode === 'balanced' ? ' on' : ''}`} onClick={() => handleFxChange('balanced')}>BALANCED</span>
              <span className={`seg seg-low${currentMode === 'low' ? ' on' : ''}`} onClick={() => handleFxChange('low')}>LOW</span>
              <span className="meta">部署=单文件静态壳 · 渲染=访问侧 GPU · WebGL 失败自动降级 CSS 深空</span>
            </div>
          </div>
        </div>
      </div>

      {/* Toast */}
      <div className="lab-toast" id="labToast" />

      {/* Command Palette (Ctrl+K) */}
      {commandOpen && (
        <div className="scrim open" onClick={(e) => { if (e.target === e.currentTarget) setCommandOpen(false) }}>
          <div className="palette glass glow-edge">
            <div className="inp">
              <span dangerouslySetInnerHTML={{ __html: svg(ICONS.search) }} />
              <input
                autoFocus
                placeholder="输入命令…  deploy / inspect / mcp / discharge"
                onKeyDown={(e) => { if (e.key === 'Escape') setCommandOpen(false) }}
              />
              <span className="mono up">ESC</span>
            </div>
            <div className="cmd-list">
              <div className="cmd" onClick={() => { handleInject(); setCommandOpen(false) }}>
                <span className="tile" dangerouslySetInnerHTML={{ __html: svg(ICONS.bolt) }} />
                <b>注入放电</b>
                <small>discharge.inject</small>
                <span className="badge info" data-state="info">SAFE</span>
                <span className="hk">↵</span>
              </div>
              <div className="cmd" onClick={() => { handleFxChange('full'); setCommandOpen(false) }}>
                <span className="tile" dangerouslySetInnerHTML={{ __html: svg(ICONS.flow) }} />
                <b>切到 FULL 档</b>
                <small>fx.full</small>
                <span className="hk">↵</span>
              </div>
              <div className="cmd" onClick={() => { handleFxChange('balanced'); setCommandOpen(false) }}>
                <span className="tile" dangerouslySetInnerHTML={{ __html: svg(ICONS.flow) }} />
                <b>切到 BALANCED 档</b>
                <small>fx.balanced</small>
                <span className="hk">↵</span>
              </div>
              <div className="cmd" onClick={() => { handleFxChange('low'); setCommandOpen(false) }}>
                <span className="tile" dangerouslySetInnerHTML={{ __html: svg(ICONS.flow) }} />
                <b>切到 LOW 档</b>
                <small>fx.low</small>
                <span className="hk">↵</span>
              </div>
              <div className="cmd" onClick={() => { handleExport(); setCommandOpen(false) }}>
                <span className="tile" dangerouslySetInnerHTML={{ __html: svg(ICONS.copy) }} />
                <b>导出 DTCG TOKEN</b>
                <small>dtcg.export</small>
                <span className="hk">↵</span>
              </div>
              <div className="cmd" onClick={() => { toggleTheme(); setCommandOpen(false) }}>
                <span className="tile" dangerouslySetInnerHTML={{ __html: svg(ICONS.moon) }} />
                <b>切换主题</b>
                <small>theme.toggle</small>
                <span className="hk">↵</span>
              </div>
              <div className="cmd" onClick={() => { handleReset(); setCommandOpen(false) }}>
                <span className="tile" dangerouslySetInnerHTML={{ __html: svg(ICONS.wrench) }} />
                <b>重置 Render Tokens</b>
                <small>render.reset</small>
                <span className="badge warn" data-state="warn">WARN</span>
                <span className="hk">↵</span>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
