import { EnvironmentOption, ServiceOption } from './useDeployFormState'
import { useMemo } from 'react'

export function serverNameFromAny(value: any): string {
  if (value == null) return ''
  if (typeof value === 'string' || typeof value === 'number') return String(value).trim()
  if (typeof value === 'object') {
    const candidates = [
      value.name,
      value.server_name,
      value.serverName,
      value.display_name,
      value.displayName,
      value.host,
      value.ip,
      value.id,
    ]
    for (const item of candidates) {
      const text = String(item || '').trim()
      if (text) return text
    }
  }
  return String(value || '').trim()
}

export function parseServerList(value: any): string[] {
  if (Array.isArray(value)) return value.map(serverNameFromAny).filter(Boolean)
  return String(value || '')
    .split(/[\n,，]+/)
    .map((x) => x.trim())
    .filter(Boolean)
}

export function uniqueServers(items: any[]): string[] {
  const seen = new Set<string>()
  const result: string[] = []
  items.forEach((item) => {
    const name = serverNameFromAny(item)
    if (!name || seen.has(name)) return
    seen.add(name)
    result.push(name)
  })
  return result
}


export interface ServerMeta {
  name: string
  displayName: string
  host?: string
  ip?: string
  environment?: string
  group?: string
  status?: string
  source?: string
}

function serverMetaFromAny(value: any, source: string = '', extra: Partial<ServerMeta> = {}): ServerMeta | null {
  const name = serverNameFromAny(value)
  if (!name) return null
  const obj = value && typeof value === 'object' ? value : {}
  return {
    name,
    displayName: String(obj.display_name || obj.displayName || obj.name || obj.server_name || name).trim() || name,
    host: obj.host || obj.hostname || obj.ip || extra.host,
    ip: obj.ip || obj.host || extra.ip,
    environment: obj.environment || obj.env || extra.environment,
    group: obj.group || obj.group_name || extra.group,
    status: obj.status || obj.state || extra.status,
    source: source || extra.source,
  }
}

function mergeServerMeta(target: Map<string, ServerMeta>, item: ServerMeta | null) {
  if (!item) return
  const current = target.get(item.name) || { name: item.name, displayName: item.displayName }
  target.set(item.name, {
    ...current,
    ...Object.fromEntries(Object.entries(item).filter(([, value]) => value !== undefined && value !== '')),
    displayName: item.displayName || current.displayName || item.name,
  })
}

export const normalizeDeployName = (value: any) => String(value || '').trim().toLowerCase().replace(/_/g, '-')

export function envAlias(value: any) {
  const v = normalizeDeployName(value)
  if (['prod', 'production', 'online', 'release', 'live', '线上', '生产'].includes(v)) return 'prod'
  if (['test', 'testing', 'qa', 'stage', 'staging', 'uat', 'dev', '测试'].includes(v)) return 'test'
  return v
}

type UseDeployServerSelectionArgs = {
  services: ServiceOption[]
  environments: EnvironmentOption[]
  serverGroupList: any[]
  serverInventory: any[]
  service: string
  environment: string
  servers: string
  serverGroup: string
  serverAutoMode: boolean
  pipelineId: string
  pipelineSteps?: any[]
  pipelines?: any[]
  setService: (value: string) => void
  setServers: (value: string) => void
  setServerAutoMode: (value: boolean) => void
  setPipelineId: (value: string) => void
}

export function useDeployServerSelection(args: UseDeployServerSelectionArgs) {
  const {
    services,
    environments,
    serverGroupList,
    serverInventory,
    service,
    environment,
    servers,
    serverGroup,
    serverAutoMode,
    pipelineId,
    pipelineSteps,
    pipelines,
    setService,
    setServers,
    setServerAutoMode,
    setPipelineId,
  } = args

  const stringifyServerList = (items: any[]): string => uniqueServers(items).join(', ')
  const isTestEnvironment = (value: any) => envAlias(value) === 'test'
  const isProdEnvironment = (value: any) => envAlias(value) === 'prod'

  const serverLooksLikeTest = (serverName: string) => {
    const n = normalizeDeployName(serverName)
    return ['测试', 'test', 'testing', 'qa', 'stage', 'staging', 'uat', 'dev'].some((kw) => n.includes(kw))
  }

  const filterServersByEnvironment = (serverNames: string[], envName: string) => {
    const clean = serverNames.filter(Boolean)
    if (!envName) return clean
    if (isTestEnvironment(envName)) {
      const matched = clean.filter((name) => serverLooksLikeTest(name))
      return matched.length > 0 ? matched : clean
    }
    if (isProdEnvironment(envName)) {
      const matched = clean.filter((name) => !serverLooksLikeTest(name))
      return matched.length > 0 ? matched : clean
    }
    return clean
  }

  const envValueFromMap = (mapLike: any, envName: string): string[] => {
    if (!mapLike || typeof mapLike !== 'object') return []
    const alias = envAlias(envName)
    const candidates = [envName, alias, alias === 'prod' ? 'production' : '', alias === 'test' ? 'testing' : ''].filter(Boolean)
    for (const key of candidates) {
      const val = mapLike[key]
      if (Array.isArray(val)) return val.filter(Boolean)
      if (typeof val === 'string' && val.trim()) return parseServerList(val)
    }
    return []
  }

  const serviceAliases = (svc?: ServiceOption) => {
    if (!svc) return []
    const vars = svc.template_variables || {}
    const raw = [
      svc.name, svc.display_name, vars.service_name, vars.pm2_name,
      ...(Array.isArray(svc.server_keywords) ? svc.server_keywords : []),
      ...(Array.isArray(vars.server_keywords) ? vars.server_keywords : []),
      ...(Array.isArray(vars.target_aliases) ? vars.target_aliases : []),
    ]
    const out = new Set<string>()
    raw.forEach((item: any) => {
      const n = normalizeDeployName(item)
      if (!n) return
      out.add(n)
      if (n.startsWith('crypto-')) out.add(n.replace(/^crypto-/, ''))
    })
    if (out.has('system')) ['main', 'master', '主节点', '主'].forEach((x) => out.add(x))
    if (out.has('frontend')) ['web', 'www', '前端'].forEach((x) => out.add(x))
    return Array.from(out).filter((x) => x.length >= 2)
  }

  const filterServersByService = (serverNames: string[], svc?: ServiceOption) => {
    const aliases = serviceAliases(svc)
    if (aliases.length === 0) return serverNames
    const matched = serverNames.filter((name) => {
      const n = normalizeDeployName(name)
      return aliases.some((alias) => n.includes(alias))
    })
    return matched.length > 0 ? matched : serverNames
  }

  const findGroupByKey = (groupKey?: string) => {
    if (!groupKey) return undefined
    return serverGroupList.find((g: any) => [g.id, g.name, g.display_name].includes(groupKey))
  }

  const findServiceByHint = (hint: string) => {
    const target = normalizeDeployName(hint)
    if (!target) return undefined
    return services.find((s) => {
      const vars = s.template_variables || {}
      const candidates = [s.name, s.display_name, vars.service_name, vars.pm2_name]
      return candidates.some((c) => {
        const cn = normalizeDeployName(c)
        return cn && (cn === target || cn.endsWith(`-${target}`) || target.endsWith(`-${cn}`))
      })
    })
  }

  const findServiceByDeployPath = (path?: string) => {
    if (!path) return undefined
    const normalizedPath = String(path).replace(/\/+$/, '')
    return services.find((s) => {
      const vars = s.template_variables || {}
      const svcPath = String(vars.service_dir || vars.deploy_path || '').replace(/\/+$/, '')
      return svcPath && (svcPath === normalizedPath || svcPath.startsWith(`${normalizedPath}/`))
    })
  }

  const isDockerCompose = (svc?: ServiceOption | null, steps?: any[] | null) => {
    if (svc) {
      if (svc.template === 'docker_compose' || svc.template === 'crypto_docker_compose') return true
      const vars = svc.template_variables || {}
      if (vars.compose_dir || vars.compose_file) return true
    }
    if (Array.isArray(steps) && steps.length > 0) {
      return steps.some((s: any) => {
        const t = s?.step_type || s?.type
        return t === 'docker_compose_update' || t === 'docker_compose'
      })
    }
    // pipelineSteps 异步加载期间，通过 pipeline strategy 同步判断
    if (pipelineId && Array.isArray(pipelines)) {
      const selectedPipeline = pipelines.find((p: any) => p.id === pipelineId)
      if (selectedPipeline?.strategy === 'DOCKER_COMPOSE') return true
    }
    return false
  }

  const selectedService = findServiceByHint(service)
  const isSelectedDockerCompose = isDockerCompose(selectedService, pipelineSteps)
  const selectedServerNames = parseServerList(servers)
  const selectedServerSet = new Set(selectedServerNames)

  const serverMetaByName = useMemo(() => {
    const map = new Map<string, ServerMeta>()
    serverInventory.forEach((srv: any) => {
      mergeServerMeta(map, serverMetaFromAny(srv, 'inventory', { group: srv?.group }))
    })
    serverGroupList.forEach((group: any) => {
      ;(group.server_names || []).forEach((item: any) => mergeServerMeta(map, serverMetaFromAny(item, 'server_group', { group: group.display_name || group.name })))
      ;(group.servers || []).forEach((item: any) => mergeServerMeta(map, serverMetaFromAny(item, 'server_group', { group: group.display_name || group.name })))
    })
    environments.forEach((env: any) => {
      ;(env.servers || []).forEach((item: any) => mergeServerMeta(map, serverMetaFromAny(item, 'environment', { environment: env.display_name || env.name })))
    })
    services.forEach((svc: any) => {
      ;(svc.servers || []).forEach((item: any) => mergeServerMeta(map, serverMetaFromAny(item, 'service')))
      const vars = svc.template_variables || {}
      ;[svc.servers_by_env, vars.servers_by_env, vars.server_names_by_env, vars.env_servers].forEach((byEnv: any) => {
        if (!byEnv || typeof byEnv !== 'object') return
        Object.entries(byEnv).forEach(([envName, values]: [string, any]) => {
          parseServerList(values).forEach((name) => mergeServerMeta(map, serverMetaFromAny(name, 'service_env', { environment: envName })))
        })
      })
    })
    selectedServerNames.forEach((name) => mergeServerMeta(map, serverMetaFromAny(name, 'manual')))
    return map
  }, [serverInventory, serverGroupList, environments, services, selectedServerNames])

  const serviceServersByEnv = (svc?: ServiceOption, envName: string = environment) => {
    if (!svc) return []
    const vars = svc.template_variables || {}
    for (const source of [svc.servers_by_env, vars.servers_by_env, vars.server_names_by_env, vars.env_servers]) {
      const values = envValueFromMap(source, envName)
      if (values.length > 0) return values
    }
    return []
  }

  const resolveServerNamesForTarget = (svc?: ServiceOption, groupKey: string = serverGroup, envName: string = environment) => {
    const group = findGroupByKey(groupKey)
    const envSpecific = serviceServersByEnv(svc, envName)
    const selectedEnv = environments.find((e) => e.name === envName || e.id === envName)

    let candidates: string[] = []
    if (envSpecific.length > 0) candidates = envSpecific
    else if (group?.server_names?.length) candidates = group.server_names.filter(Boolean)
    else if (svc?.servers?.length) candidates = svc.servers.filter(Boolean)
    else if (selectedEnv?.servers?.length) candidates = selectedEnv.servers.filter(Boolean)

    const envFiltered = filterServersByEnvironment(candidates, envName)
    const serviceFiltered = filterServersByService(envFiltered, svc)
    return Array.from(new Set(serviceFiltered.filter(Boolean)))
  }

  const setSelectedServers = (items: any[], autoMode = false) => {
    setServers(stringifyServerList(items))
    setServerAutoMode(autoMode)
  }

  const autoFillServers = (opts?: { svc?: ServiceOption, groupKey?: string, envName?: string, force?: boolean }) => {
    if (!opts?.force && !serverAutoMode) return
    const svc = opts?.svc || selectedService
    const groupKey = opts?.groupKey ?? serverGroup
    const envName = opts?.envName ?? environment
    const resolved = resolveServerNamesForTarget(svc, groupKey, envName)
    setSelectedServers(resolved, true)
  }

  const serverCandidatesForCurrentTarget = useMemo(() => {
    const group = findGroupByKey(serverGroup)
    const selectedEnv = environments.find((e) => e.name === environment || e.id === environment)
    const envSpecific = serviceServersByEnv(selectedService, environment)
    const candidates = [
      ...(group?.server_names || []),
      ...(envSpecific || []),
      ...(selectedService?.servers || []),
      ...(selectedEnv?.servers || []),
      ...selectedServerNames,
    ]
    return uniqueServers(candidates)
  }, [serverGroup, environments, environment, selectedService, selectedServerNames, serverGroupList])

  const candidateServerNames = serverCandidatesForCurrentTarget
  const recommendedServerNames = resolveServerNamesForTarget(selectedService, serverGroup, environment)
  const recommendedServerSet = new Set(recommendedServerNames)

  const getEnvironmentServerConflicts = (serverNames: string[] = selectedServerNames, envName: string = environment) => {
    const alias = envAlias(envName)
    if (!envName || !['prod', 'test'].includes(alias) || serverNames.length === 0) return []
    const expected = alias === 'test' ? '测试' : '线上/生产'
    return serverNames
      .map((name) => {
        const testLike = serverLooksLikeTest(name)
        const conflict = alias === 'test' ? !testLike : testLike
        return conflict ? { name, expected, actual: testLike ? '测试' : '线上/生产' } : null
      })
      .filter(Boolean) as Array<{ name: string; expected: string; actual: string }>
  }

  const selectedServerEnvironmentConflicts = getEnvironmentServerConflicts()
  const selectedServerEnvironmentConflictSet = new Set(selectedServerEnvironmentConflicts.map((item) => item.name))
  const environmentServerErrorText = selectedServerEnvironmentConflicts.length > 0
    ? `当前环境 ${environment} 只允许选择${selectedServerEnvironmentConflicts[0].expected}服务器，冲突服务器：${selectedServerEnvironmentConflicts.map((item) => `${item.name}(${item.actual})`).join('，')}`
    : ''

  const validateEnvironmentServerSelection = (actionLabel: string) => {
    const conflicts = getEnvironmentServerConflicts()
    if (conflicts.length === 0) return true
    const expected = conflicts[0].expected
    const detail = conflicts.map((item) => `- ${item.name}：识别为${item.actual}`).join('\n')
    alert(`${actionLabel}已阻断：环境与服务器不一致。\n\n当前环境：${environment || '-'}\n要求服务器：${expected}\n\n冲突项：\n${detail}\n\n请切换环境，或取消勾选冲突服务器后再继续。`)
    return false
  }

  const toggleServerSelection = (serverName: string, checked: boolean) => {
    const current = parseServerList(servers)
    const next = checked ? uniqueServers([...current, serverName]) : current.filter((name) => name !== serverName)
    setSelectedServers(next, false)
  }

  const selectRecommendedServers = () => {
    setSelectedServers(resolveServerNamesForTarget(selectedService, serverGroup, environment), true)
  }

  const selectAllCandidateServers = () => {
    setSelectedServers(candidateServerNames, false)
  }

  const clearSelectedServers = () => {
    setSelectedServers([], false)
  }

  const invertCandidateServers = () => {
    const current = new Set(parseServerList(servers))
    const next = candidateServerNames.filter((name) => !current.has(name))
    setSelectedServers(next, false)
  }

  const applyServiceDefaults = (serviceName: string, options?: { keepServers?: boolean, forceAuto?: boolean, keepPipeline?: boolean }) => {
    const svc = findServiceByHint(serviceName) || services.find((s) => s.name === serviceName)
    setService(svc?.name || serviceName)
    if (!svc) return
    if (!options?.keepServers) {
      autoFillServers({ svc, force: options?.forceAuto ?? true })
    }
    // 若当前未选 Pipeline 或已选的 Pipeline 不属于新服务，自动切到该服务的默认 Pipeline
    if (!options?.keepPipeline) {
      const defaultPipelineId = (svc as any).pipeline_id || ''
      const pipelineBelongsToService = defaultPipelineId && pipelineId === defaultPipelineId
      if (!pipelineId || (defaultPipelineId && !pipelineBelongsToService)) {
        setPipelineId(defaultPipelineId || '')
      }
    }
  }

  return {
    parseServerList,
    uniqueServers,
    normalize: normalizeDeployName,
    envAlias,
    isProdEnvironment,
    isTestEnvironment,
    serverLooksLikeTest,
    isDockerCompose,
    isSelectedDockerCompose,
    selectedService,
    selectedServerNames,
    selectedServerSet,
    candidateServerNames,
    recommendedServerSet,
    selectedServerEnvironmentConflictSet,
    serverMetaByName,
    environmentServerErrorText,
    validateEnvironmentServerSelection,
    findServiceByHint,
    findServiceByDeployPath,
    findGroupByKey,
    resolveServerNamesForTarget,
    autoFillServers,
    selectRecommendedServers,
    selectAllCandidateServers,
    clearSelectedServers,
    invertCandidateServers,
    toggleServerSelection,
    applyServiceDefaults,
  }
}
