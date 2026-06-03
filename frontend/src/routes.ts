import type { LucideIcon } from 'lucide-react'
import {
  ClipboardCheck,
  Files,
  FileText,
  Database,
  Gauge,
  GitBranch,
  HeartPulse,
  ListChecks,
  PlugZap,
  Rocket,
  ServerCog,
  Stethoscope,
  Wrench,
} from 'lucide-react'

export const ROUTES = {
  dashboard: '/',
  dashboardAlias: '/dashboard',
  login: '/login',
  systems: '/systems',
  deploy: '/deploy',
  tasks: '/tasks',
  taskCenterAlias: '/task-center',
  system: '/system',
  systemStatus: '/system/status',
  diagnostics: '/system/diagnostics',
  servers: '/servers',
  files: '/files',
  pipelines: '/pipelines',
  maintenance: '/maintenance',
  sqlQuery: '/sql-query',
  audit: '/audit',
  reports: '/reports',
  database: '/database',
  tools: '/tools',
  systemEdit: '/systems/:name/edit',
  systemCreate: '/systems/create',
  serviceEdit: '/systems/:systemName/services/:serviceName/edit',
  serviceCreate: '/systems/:systemName/services/create',
} as const

export type RouteKey = keyof typeof ROUTES
export type AppRoute = (typeof ROUTES)[RouteKey]

export type NavItem = {
  path: AppRoute
  label: string
  icon: LucideIcon
  desc: string
  minRole?: 'readonly' | 'operator' | 'admin'
}

export const NAV_LINKS: NavItem[] = [
  { path: ROUTES.dashboard, label: '工作台', icon: Gauge, desc: '运行健康与快捷入口' },
  { path: ROUTES.systems, label: '系统', icon: Files, desc: '系统、服务、环境与分组管理', minRole: 'admin' },
  { path: ROUTES.deploy, label: '发布', icon: Rocket, desc: '发布预检与部署日志' },
  { path: ROUTES.tasks, label: '任务', icon: ListChecks, desc: '任务流与执行状态' },
  { path: ROUTES.system, label: '状态', icon: HeartPulse, desc: '健康检查与备份提醒' },
  { path: ROUTES.diagnostics, label: '诊断', icon: Stethoscope, desc: '安装自检与回归诊断' },
  { path: ROUTES.servers, label: '服务器', icon: ServerCog, desc: '资产、终端与文件' },
  { path: ROUTES.files, label: '文件', icon: Files, desc: '本地发布包管理' },
  { path: ROUTES.pipelines, label: '流程', icon: GitBranch, desc: '部署流程编排' },
  { path: ROUTES.maintenance, label: '维护', icon: Wrench, desc: '系统备份与运行维护', minRole: 'admin' },
  { path: ROUTES.audit, label: '审计', icon: ClipboardCheck, desc: '操作审计与导出', minRole: 'admin' },
  { path: ROUTES.reports, label: '报告', icon: FileText, desc: '诊断、发布与链路报告' },
  { path: ROUTES.database, label: '数据库', icon: Database, desc: '连接、查询、清理与导出' },
  { path: ROUTES.tools, label: 'AI 工具', icon: PlugZap, desc: 'MCP / HTTP 工具接入' },
]

export const SPA_PAGE_ROUTES: AppRoute[] = [
  ROUTES.dashboard,
  ROUTES.dashboardAlias,
  ROUTES.login,
  ROUTES.systems,
  ROUTES.deploy,
  ROUTES.tasks,
  ROUTES.taskCenterAlias,
  ROUTES.system,
  ROUTES.systemStatus,
  ROUTES.diagnostics,
  ROUTES.servers,
  ROUTES.files,
  ROUTES.pipelines,
  ROUTES.maintenance,
  ROUTES.sqlQuery,
  ROUTES.audit,
  ROUTES.reports,
  ROUTES.database,
  ROUTES.tools,
  '/systems/:name/edit',
  '/systems/create',
  '/systems/:systemName/services/:serviceName/edit',
  '/systems/:systemName/services/create',
]
