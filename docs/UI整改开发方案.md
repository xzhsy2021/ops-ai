# OPS-AI 前端 UI 整改开发方案

> 基于 `原始规范.md` 设计系统与示例 HTML 页面，对现有 OPS-AI 前端 UI 进行全面整改。

---

## 一、设计系统变更总览

| 维度 | 当前实现 | 整改目标（参考规范） |
|------|---------|-------------------|
| 品牌主色 | 电光青蓝 `#38bdf8` | 荧光翠 `#35E07C` |
| 深色背景 | `#070b16` / `#0a1020` | `#050607` / `#0e1012` |
| 主题数量 | 2 (dark / light) | 3 (crystal / jade / light) |
| 字体 Display | Bricolage Grotesque | Space Grotesk |
| 字体 Body | Inter | Manrope + Noto Sans SC |
| 背景纹理 | 蓝色网格渐变 | 点阵底纹 + 环境微光 |
| 圆角体系 | 非统一 | 分级：18px / 13px / 9px / 999px |
| 阴影 | 多层 `shadow-1~4` | 统一 `0 24px 60px rgba(0,0,0,.45)` |
| 发丝线 | 无统一规范 | 1px `rgba(255,255,255,.07)` 发丝线 |
| 玻璃效果 | 全局通用 | 仅用于悬浮面板和 3D 文件袋 |

---

## 二、实施阶段

### Phase 1: 设计 Token 系统（index.css + tokens.css）

**文件：** `frontend/src/index.css`（前 250 行）

**改动：**
- 替换 `:root` 中的 CSS 变量为规范定义的三级色板
- `--accent: #35e07c` 取代 `--brand: #38bdf8`
- `--bg: #050607` / `--panel: #0e1012` / `--text: #f3f5f4` 等
- 新增 `[data-theme="crystal"]` / `[data-theme="jade"]` / `[data-theme="light"]` 三主题
- 字体：`Space Grotesk` (display) / `Manrope` + `Noto Sans SC` (body)
- 圆角：`--r-lg: 18px` / `--r-md: 13px` / `--r-sm: 9px` / `--r-pill: 999px`
- 阴影：`--shadow: 0 24px 60px rgba(0,0,0,.5)`
- 缓动：`--ease: cubic-bezier(.22,.61,.36,1)`
- 点阵底纹：`radial-gradient(var(--dot) 1px, transparent 1.4px)`
- 环境微光：`radial-gradient(... var(--glow))`

### Phase 2: 更新 Theme Engine（theme/engine.ts + App.tsx）

**文件：** `frontend/src/theme/engine.ts`、`frontend/src/App.tsx`

**改动：**
- engine.ts：移除旧的 JSON token 注入，直接用 CSS 变量
- App.tsx：三主题切换（crystal → jade → light），保存到 localStorage
- 顶栏主题切换按钮改为三向循环
- 性能模式保留（standard / low-resource）
- 专注模式保留

### Phase 3: 更新 App Shell 布局

**文件：** `frontend/src/App.tsx`、`frontend/src/index.css`（侧边栏/顶栏部分）

**改动：**
- 侧边栏：背景 `linear-gradient(180deg, var(--panel), var(--bg2))`，发丝线 `border-right: 1px solid var(--line)`
- 品牌标识：绿色渐变 logo，显示 "AI" 文字
- 导航项：规范中定义的样式（active 态有绿色边框/阴影）
- 侧边栏自动收窄：鼠标移出收窄至 72px 图标态
- 顶栏：移至左侧导航下方，与工作区在同一区域
- 面包屑字体规范

### Phase 4: 更新核心 UI 组件

**文件：** `frontend/src/components/ui/GlassPanel.tsx`、`GlassCard.tsx`、`Button.tsx`、`Badge.tsx`

**改动：**
- GlassPanel：从通用玻璃改为规范面板（发丝边 + 渐变背景）
- GlassCard：面板样式 + 悬浮效果
- Button：新增 accent 按钮（绿色渐变，与规范一致）
- Badge：使用规范颜色语义

### Phase 5: 更新页面级样式

**文件：** `frontend/src/index.css`（后半部分）

**改动：**
- 卡片 `.card`、`.stat-card`：规范圆角、发丝边、绿色背景光晕
- 分页/筛选/表格：规范发丝线分隔
- 模态框：规范样式
- Toast：规范样式（底部居中，绿色圆点）
- 滚动条：规范样式

### Phase 6: 构建验证

- 运行 `npm run build` 验证无编译错误
- 检查 gzip 大小
- 本地预览验证主题切换、布局、组件渲染正确

---

## 三、关键设计决策

1. **保留原有功能逻辑**：整改仅涉及视觉风格，不改变 React 组件逻辑、路由、数据流
2. **CSS 变量驱动**：所有颜色/尺寸通过 CSS 变量控制，确保三主题一致性
3. **渐进式替换**：先改设计 Token，再改布局，最后改组件，降低风险
4. **兼容性**：保持 `data-theme` 属性切换机制，与现有 `data-fx` / `data-zen` 共存
5. **排除 神经突触实验室**：该页面已有独立的全屏渲染引擎，不纳入本次整改

---

## 四、文件变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `frontend/src/index.css` | 重写前半部分 | 设计 Token + 三主题 + 点阵底纹 |
| `frontend/src/theme/tokens.css` | 保留/微调 | 保持通用工具类，更新颜色引用 |
| `frontend/src/theme/engine.ts` | 简化 | 移除 JSON token 注入，保留 CSS 变量切换 |
| `frontend/src/App.tsx` | 修改 shell 部分 | 品牌标识、主题切换、侧边栏联动 |
| `frontend/src/components/ui/GlassPanel.tsx` | 修改样式 | 使用规范面板样式 |
| `frontend/src/components/ui/GlassCard.tsx` | 修改样式 | 使用规范卡片样式 |
| `frontend/src/components/ui/Button.tsx` | 新增 accent 变体 | 绿色渐变按钮 |
| `frontend/src/components/ui/Badge.tsx` | 修改颜色 | 使用规范状态色 |
| `frontend/index.html` | 添加字体链接 | Space Grotesk + Manrope + Noto Sans SC |