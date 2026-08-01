# Enterprise AI Command Center UI 开发规范 v1.0

> 面向本地 AI Agent（Claude Code / Codex / Cursor / Trae /
> Qoder）开发使用。
>
> 目标：构建 Token 驱动、Agent Ready、Spatial UI 的企业级 AI 控制台。

------------------------------------------------------------------------

# 1. 项目定位

产品类型：

-   AI Agent 控制台
-   自动化运维平台
-   AI 交易控制中心
-   数据分析平台
-   风控系统

设计目标：

    Linear
    +
    OpenAI Operator
    +
    Bloomberg Terminal
    +
    Spatial Computing

核心理念：

    Token 控制物理世界

    Agent Event 驱动可视化行为

------------------------------------------------------------------------

# 2. 总体架构

采用：

    Design Token
            |
            v
    Theme Engine
            |
            v
    CSS Variables
            |
            v
    React Components
            |
            +---- Canvas
            |
            +---- WebGL Shader
            |
            +---- Animation Engine

禁止：

-   页面内部硬编码颜色
-   页面内部定义动画参数
-   单文件巨型组件
-   UI 与业务逻辑耦合

------------------------------------------------------------------------

# 3. 技术栈规范

## Frontend

必须：

-   React
-   TypeScript
-   Next.js
-   TailwindCSS

推荐：

-   Framer Motion
-   Three.js
-   React Three Fiber
-   ECharts
-   Zustand

------------------------------------------------------------------------

# 4. Design Token 系统

目录：

    tokens/

    ├── base.json
    ├── color.json
    ├── fx.json
    ├── render.json
    ├── motion.json
    └── density.json

------------------------------------------------------------------------

# 5. Token 分类

## Base Token

控制：

-   字体
-   圆角
-   间距
-   阴影

示例：

``` json
{
 "radius":{
   "md":"12px",
   "lg":"20px"
 }
}
```

------------------------------------------------------------------------

## Color Token

支持：

-   Dark
-   Light

示例：

``` json
{
 "energy":{
   "primary":"#22E08A"
 }
}
```

------------------------------------------------------------------------

## Render Token

Render Token 同时控制：

-   CSS
-   Canvas
-   WebGL
-   Shader

示例：

``` json
{
 "render":{
   "dpr":2,
   "particles":90,
   "glow":1.1,
   "nebula":1.1,
   "parallax":46,
   "pulse-speed":1.2,
   "pulse-trail":12
 }
}
```

------------------------------------------------------------------------

# 6. FX 模式规范

必须支持：

## FULL

高性能设备：

    particles:90
    glow:1.1
    dpr:2

## BALANCED

默认：

    particles:48
    glow:0.75
    dpr:1.5

## LOW

低功耗：

    particles:0
    glow:0.4
    dpr:1

------------------------------------------------------------------------

# 7. UI视觉规范

## Glass Panel

统一组件：

    <GlassPanel />

标准：

    background:
    rgba(255,255,255,0.06)

    blur:
    20px

    border:
    1px rgba(255,255,255,0.12)

    radius:
    20px

    shadow:
    0 20px 60px rgba(0,0,0,.4)

------------------------------------------------------------------------

# 8. Layout规范

桌面：

    Header

    Sidebar | Main Canvas

            Visualization

            Timeline

Grid:

    columns:

    240px 1fr

    gap:

    24px

------------------------------------------------------------------------

# 9. Component规范

目录：

    src/components

    ├── ui

    │   ├── GlassCard
    │   ├── Button
    │   ├── Badge
    │
    ├── visualization

    │   ├── ParticleField
    │   ├── NeuralGraph
    │   ├── Timeline
    │
    ├── agent

    │   ├── AgentNode
    │   ├── ToolEvent
    │   ├── RiskBadge
    │
    └── charts

        └── PerformanceChart

------------------------------------------------------------------------

# 10. Agent Ready 架构

所有 Agent 行为必须事件化：

    Agent Action

    ↓

    Event Bus

    ↓

    Visualization

    ↓

    Audit Log

------------------------------------------------------------------------

# 11. Agent Event Schema

TypeScript:

``` ts
interface AgentEvent {

 id:string;

 tool:string;

 risk:
 "info"
 |
 "warn"
 |
 "alert";

 source:string;

 target:string;

 status:
 "RUNNING"
 |
 "SUCCESS"
 |
 "QUEUED";

 timestamp:number;

}
```

------------------------------------------------------------------------

# 12. MCP Tool 可视化规范

每个 Tool 对应 Node。

Node 包含：

-   ID
-   Label
-   Risk
-   Position

例如：

    metrics.read

    deploy.start

    backup.now

    config.get

------------------------------------------------------------------------

# 13. Pulse 动画规范

Agent调用：

    Node A

      |

     Pulse

      |

    Node B

Pulse 数据：

``` json
{
"from":"agent",
"to":"deploy.start",
"risk":"warn",
"speed":1.2,
"trail":12
}
```

------------------------------------------------------------------------

# 14. 风险颜色规范

  类型          颜色
  ------------- --------
  read          blue
  write         orange
  destructive   red

规则：

危险操作：

    直接执行禁止

    必须 Human Approval

状态：

    QUEUED

------------------------------------------------------------------------

# 15. Animation规范

统一：

    motion token

控制：

-   duration
-   easing
-   delay

禁止：

``` css
animation-duration:0.5s;
```

必须：

``` css
animation-duration:
var(--motion-fast);
```

------------------------------------------------------------------------

# 16. 开发流程

## Phase 1

建立：

    Token System
    Theme Engine

------------------------------------------------------------------------

## Phase 2

组件库：

    GlassCard

    Button

    Modal

    Badge

    Layout

------------------------------------------------------------------------

## Phase 3

Visualization：

    Canvas

    Particle

    Graph

    Timeline

------------------------------------------------------------------------

## Phase 4

Agent：

    MCP

    Event Bus

    Replay

    Audit

------------------------------------------------------------------------

## Phase 5

优化：

目标：

Desktop:

60 FPS

Mobile:

30 FPS

Low:

节能模式

------------------------------------------------------------------------

# 17. AI Agent Coding Prompt

复制给 AI Agent：

    你是一名 Staff Frontend Engineer。


    请按照 Enterprise AI Command Center Design System 开发。


    要求：

    1. 所有UI必须Token驱动。

    2. 禁止硬编码颜色。

    3. 动画参数必须来自motion token。

    4. 3D效果必须来自render token。

    5. 使用React+TypeScript。

    6. 所有Agent行为必须Event化。

    7. 所有危险操作支持Human-in-loop。

    8. 创建可复用组件。


    每完成模块输出：

    - 文件结构
    - API设计
    - Token变化
    - 测试方案

------------------------------------------------------------------------

# 18. NFI-Pro AI交易平台映射

    Freqtrade Bot

    ↓

    Agent Node


    交易信号

    ↓

    Pulse Event


    开仓/平仓

    ↓

    Risk Energy


    收益曲线

    ↓

    Realtime Visualization


    交易API

    ↓

    MCP Tool

------------------------------------------------------------------------

# 最终目标

构建：

> AI Agent Native Operating System

而不是传统 Dashboard。
