# Enterprise AI Command Center UI 改造计划

## 项目定位
> OPS Command Center → Enterprise AI Command Center
> Token 驱动 + Agent Ready + Spatial UI

## 目录结构

```
frontend/
├── tokens/                    # Phase 1: Design Token 系统
│   ├── base.json
│   ├── color.json
│   ├── fx.json
│   ├── render.json
│   ├── motion.json
│   └── density.json
├── src/
│   ├── theme/                 # Phase 2: Theme Engine
│   │   ├── engine.ts
│   │   └── tokens.css
│   ├── components/
│   │   ├── ui/                # Phase 3: Glass UI 组件
│   │   │   ├── GlassPanel.tsx
│   │   │   ├── GlassCard.tsx
│   │   │   ├── Button.tsx
│   │   │   ├── Badge.tsx
│   │   │   └── index.ts
│   │   ├── visualization/     # Phase 5: 可视化组件
│   │   │   ├── ParticleField.tsx
│   │   │   ├── NeuralGraph.tsx
│   │   │   ├── Timeline.tsx
│   │   │   └── PulseLine.tsx
│   │   └── agent/             # Phase 5: Agent 组件
│   │       ├── AgentNode.tsx
│   │       ├── ToolEvent.tsx
│   │       └── RiskBadge.tsx
│   └── App.tsx                # Phase 4: Layout 重构
```

## Phase 1: Design Token 系统

创建 tokens/ 目录，定义 6 个 JSON 文件：

- **base.json**: 字体族、圆角(12px/20px)、间距(4px 步进)、阴影
- **color.json**: Dark/Light 双主题，主色 `energy: #22E08A`
- **fx.json**: FULL/BALANCED/LOW 性能模式参数
- **render.json**: Canvas/WebGL 渲染参数
- **motion.json**: 动画时长(duration-fast/slow) + 缓动(ease-emphasized/decel/accel/spring)
- **density.json**: 紧凑/标准/舒适间距

## Phase 2: Theme Engine

- `theme/engine.ts`: 读取 tokens JSON → 通过 `document.documentElement.style.setProperty` 注入 CSS 变量
- `theme/tokens.css`: 玻璃面板标准样式 + 全局 CSS 变量

## Phase 3: Glass UI 组件库

- `GlassPanel`: `<div>` 毛玻璃容器 (backdrop-filter: blur(20px), background: rgba(255,255,255,0.06), border: 1px rgba(255,255,255,0.12), border-radius: 20px)
- `GlassCard`: 继承 GlassPanel + hover 提升动效 + 内容插槽
- `Button`: primary (能量绿 #22E08A) / ghost / destructive 三种变体
- `Badge`: 风险颜色标识 (blue=read, orange=write, red=destructive)

## Phase 4: Layout 重构

- App.tsx 改组: Header 玻璃半透明 → Sidebar 240px 玻璃面板 → Main Canvas 全高
- 品牌: "OPS Command Center" → 能量绿主色 + 品牌 logo
- DashboardPage: 移除卡片网格 → Section 布局 + 可视化锚点
- LoginPage: 全屏玻璃面板 + 粒子背景
- 环境光晕: 增强 shell-ambient 效果

## Phase 5: 可视化 & Agent 组件

- ParticleField: 粒子背景 (Three.js 可选)
- NeuralGraph: 工具调用关系图
- Timeline: Agent 事件时间线
- PulseLine: Agent 调用脉冲动画
- AgentNode: MCP Tool 可视化节点
- RiskBadge: 风险级别徽章

## 实施顺序

1. ✅ Phase 1: Token 文件
2. ✅ Phase 2: Theme Engine + CSS
3. ✅ Phase 3: Glass 组件
4. ✅ Phase 4: Layout 重构 + 页面改造
5. ✅ Phase 5: 可视化组件 + Agent 组件