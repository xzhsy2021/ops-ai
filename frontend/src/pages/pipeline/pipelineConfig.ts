export interface StepConfig {
  id?: string
  originalType?: string
  type: string
  name: string
  config: Record<string, any>
}

export interface PipelineData {
  id: string
  name: string
  system_name: string
  description: string
  strategy: string
  steps: StepConfig[]
}

export const STEP_TYPES = [
  { type: 'checkout', label: '检出代码', defaults: { repo_url: '', branch: 'main' } },
  { type: 'build', label: '构建', defaults: { cmd: 'npm run build', timeout: '300' } },
  { type: 'upload', label: '上传', defaults: { local_path: '', remote_path: '' } },
  { type: 'deploy', label: '部署', defaults: { deploy_path: '', package_path: '' } },
  { type: 'switch', label: '切换版本', defaults: {} },
  { type: 'restart', label: '重启', defaults: { cmd: '' } },
  { type: 'health_check', label: '健康检查', defaults: { url: '', retries: '3' } },
  { type: 'command', label: '命令', defaults: { cmd: '', timeout: '120' } },
  { type: 'wait', label: '等待', defaults: { seconds: '5' } },
  {
    type: 'dovo_bluegreen_update',
    label: 'Dovo蓝绿',
    defaults: {
      group_code: '${service}',
      base_path: '${base_path}',
      instances: '${instances}',
      file_name: '${file_name}',
      update_script: '${update_script}',
      switch_script: '${switch_script}',
      wait_after_binupdate: '10',
      wait_after_portupdate: '5',
      binupdate_timeout: '180',
      portupdate_timeout: '180',
      log_check_timeout: '60',
      detect_timeout: '30',
      detect_command: '',
      log_check_command: '',
      log_must_contain: '',
      log_must_not_contain: '',
    },
  },
  {
    type: 'scripted_service_update',
    label: '后台updatebin',
    defaults: {
      service_dir: '${service_dir}',
      file_name: '${file_name}',
      update_script: '${update_script}',
      log_dir: 'logs',
      timeout: '600',
    },
  },
  {
    type: 'web_script_update',
    label: 'Web脚本发布',
    defaults: {
      deploy_path: '${deploy_path}',
      file_name: '${file_name}',
      update_script: '${update_script}',
      timeout: '600',
    },
  },
  {
    type: 'docker_compose_update',
    label: 'Docker Compose',
    defaults: {
      compose_dir: '/data/crypto-trader',
      compose_file: 'docker-compose.yml',
      wait_after_up: '10',
      log_tail_lines: '30',
      timeout: '600',
    },
  },
] as const

export const STRATEGIES = [
  { value: 'DIRECT', label: '直接部署' },
  { value: 'BLUE_GREEN', label: '蓝绿发布' },
  { value: 'DOCKER_COMPOSE', label: 'Docker Compose' },
]

export const BUILTIN_STEP_TYPES = new Set([
  'checkout', 'build', 'upload', 'deploy', 'switch',
  'restart', 'health_check', 'command', 'wait',
])

export const STEP_RUNBOOKS: Record<string, string[]> = {
  dovo_bluegreen_update: [
    '识别哪个实例目录是未运行的旧程序（detect_command 可覆盖）',
    '上传发布包并更新 standby 目录下的 server',
    '在 standby 目录执行 binupdate.sh',
    '等待 wait_after_binupdate 秒后做第一次日志检查',
    '在 standby 目录执行 portupdate.sh 切换端口',
    '等待 wait_after_portupdate 秒后做第二次日志检查并再次断言',
  ],
  scripted_service_update: [
    '上传服务包到 service_dir',
    '备份同名旧包为 .preops.时间戳',
    '在服务目录执行 updatebin.sh',
    '查看进程和 logs 目录日志确认程序正常',
  ],
  web_script_update: [
    '上传本地包到 deploy_path',
    '在 deploy_path 目录执行 ./www.sh',
    '执行后检查 /data/www 目录结果',
  ],
  docker_compose_update: [
    '检查 compose_dir 是否存在',
    '执行 docker compose -f compose_file pull 拉取最新镜像',
    '执行 docker compose -f compose_file up -d --remove-orphans 启动/重启容器',
    '等待 wait_after_up 秒后做容器稳定观察',
    '执行 docker compose -f compose_file ps 检查容器状态',
    '执行 docker compose -f compose_file logs --tail=log_tail_lines 查看最近日志',
  ],
}

export const STEP_FIELD_LABELS: Record<string, Record<string, string>> = {
  dovo_bluegreen_update: {
    group_code: '分组代码',
    base_path: '实例根目录',
    instances: '实例列表',
    file_name: '发布包/程序文件名',
    update_script: '更新脚本 (binupdate.sh)',
    switch_script: '端口切换脚本 (portupdate.sh)',
    wait_after_binupdate: 'binupdate 后等待秒数',
    wait_after_portupdate: 'portupdate 后等待秒数',
    binupdate_timeout: 'binupdate 执行超时(秒)',
    portupdate_timeout: 'portupdate 执行超时(秒)',
    log_check_timeout: '日志检查超时(秒)',
    detect_timeout: 'standby 探测超时(秒)',
    detect_command: '自定义 standby 探测脚本',
    log_check_command: '自定义日志检查命令',
    log_must_contain: '日志必须包含',
    log_must_not_contain: '日志不允许出现',
    remote_path: '远程临时包路径',
    local_path: '本地包路径',
  },
  scripted_service_update: {
    service_dir: '服务目录',
    file_name: '发布包文件名',
    update_script: '更新脚本',
    log_dir: '日志目录',
    timeout: '超时时间',
    remote_path: '远程包路径',
    local_path: '本地包路径',
  },
  web_script_update: {
    deploy_path: 'Web目录',
    file_name: '发布包文件名',
    update_script: '更新脚本',
    timeout: '超时时间',
    remote_path: '远程包路径',
    local_path: '本地包路径',
  },
  docker_compose_update: {
    compose_dir: 'compose 目录',
    compose_file: 'compose 文件名',
    wait_after_up: '启动后等待秒数',
    log_tail_lines: '日志显示行数',
    timeout: '操作超时(秒)',
  },
}

export function normalizeConfigForView(config: Record<string, any>): Record<string, any> {
  if (!config || typeof config !== 'object') return {}
  if (config.fields && typeof config.fields === 'object') {
    const normalized: Record<string, any> = {}
    Object.entries(config.fields).forEach(([key, value]: [string, any]) => {
      if (value && typeof value === 'object' && value.mode === 'binding') {
        normalized[key] = '${' + (value.var || key) + '}'
      } else if (value && typeof value === 'object' && value.mode === 'literal') {
        normalized[key] = value.value ?? ''
      } else {
        normalized[key] = value
      }
    })
    return normalized
  }
  return config
}

export function normalizeConfigForEdit(rawConfig: Record<string, any> | undefined) {
  const out: Record<string, any> = {}
  const bindings: Record<string, string> = {}
  if (!rawConfig || typeof rawConfig !== 'object') return { config: out, bindings }
  if (rawConfig.fields && typeof rawConfig.fields === 'object') {
    Object.entries(rawConfig.fields).forEach(([k, v]: [string, any]) => {
      if (v && typeof v === 'object' && v.mode === 'binding') {
        bindings[k] = v.var || ''
        out[k] = ''
      } else if (v && typeof v === 'object' && v.mode === 'literal') {
        out[k] = v.value ?? ''
      } else {
        out[k] = v
      }
    })
    return { config: out, bindings }
  }
  Object.assign(out, rawConfig)
  return { config: out, bindings }
}

function stringifyConfigValue(value: any) {
  if (Array.isArray(value)) return value.join(', ')
  if (value && typeof value === 'object') return JSON.stringify(value)
  return String(value ?? '')
}

const PLACEHOLDER_RE = /\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g

export function resolvePlaceholdersInValue(value: any, vars: Record<string, string>): any {
  if (value == null) return value
  if (typeof value === 'string') {
    return value.replace(PLACEHOLDER_RE, (match, name) =>
      Object.prototype.hasOwnProperty.call(vars, name) && vars[name] !== '' && vars[name] != null
        ? String(vars[name])
        : match
    )
  }
  if (Array.isArray(value)) return value.map((v) => resolvePlaceholdersInValue(v, vars))
  if (typeof value === 'object') {
    const out: Record<string, any> = {}
    for (const [k, v] of Object.entries(value)) out[k] = resolvePlaceholdersInValue(v, vars)
    return out
  }
  return value
}

export function collectUsedVarNames(value: any, acc: Set<string>) {
  if (value == null) return
  if (typeof value === 'string') {
    let m: RegExpExecArray | null
    const re = new RegExp(PLACEHOLDER_RE.source, 'g')
    while ((m = re.exec(value)) !== null) acc.add(m[1])
    return
  }
  if (Array.isArray(value)) {
    value.forEach((v) => collectUsedVarNames(v, acc))
    return
  }
  if (typeof value === 'object') {
    Object.values(value).forEach((v) => collectUsedVarNames(v, acc))
  }
}

export { stringifyConfigValue }