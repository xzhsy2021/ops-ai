import { useEffect, useState } from 'react'
import { files, pipeline as pipelineApi, resource, serverGroups as serverGroupsApi, serverManagement } from '../../api'
import { EnvironmentOption, PipelineOption, ServiceOption, SystemOption } from './useDeployFormState'

type UseDeployOptionsLoaderArgs = {
  system: string
  service: string
  environment: string
  pipelineId: string
  setSystems: (items: SystemOption[]) => void
  setServices: (items: ServiceOption[]) => void
  setEnvironments: (items: EnvironmentOption[]) => void
  setPipelines: (items: PipelineOption[]) => void
  setServerGroupList: (items: any[]) => void
  setServerInventory: (items: any[]) => void
  setDeployPackages: (items: any[]) => void
  setPipelineSteps: (items: any[]) => void
}

function unwrapData(res: any) {
  return res?.data ?? res
}

function normalizeSystemGroups(raw: any): any[] {
  if (!raw || typeof raw !== 'object') return []
  if (Array.isArray(raw)) return raw
  return Object.entries(raw).map(([code, cfg]: [string, any]) => {
    const c = cfg && typeof cfg === 'object' ? cfg : {}
    const servers: any[] = []
    if (Array.isArray(c.servers)) servers.push(...c.servers)
    if (c.server) servers.push(c.server)
    if (c.servers_by_env && typeof c.servers_by_env === 'object') {
      Object.values(c.servers_by_env).forEach((v: any) => {
        if (Array.isArray(v)) servers.push(...v)
      })
    }
    const dedup = Array.from(new Set(servers.filter(Boolean).map(String)))
    return {
      id: `sys:${code}`,
      name: code,
      display_name: c.display_name || code,
      description: c.description || '',
      server_names: dedup,
      servers: dedup,
      server_keywords: c.server_keywords || [],
      servers_by_env: c.servers_by_env || {},
      variables: c.variables || {},
      source: 'system',
    }
  })
}

export function useDeployOptionsLoader(args: UseDeployOptionsLoaderArgs) {
  const {
    system,
    service,
    environment,
    pipelineId,
    setSystems,
    setServices,
    setEnvironments,
    setPipelines,
    setServerGroupList,
    setServerInventory,
    setDeployPackages,
    setPipelineSteps,
  } = args

  const [optionsLoading, setOptionsLoading] = useState(true)
  const [dbGroups, setDbGroups] = useState<any[]>([])
  const [systemGroups, setSystemGroups] = useState<any[]>([])

  useEffect(() => {
    let canceled = false
    let pending = 3
    const done = () => { if (!canceled && --pending === 0) setOptionsLoading(false) }
    resource.systems().then((res: any) => { if (!canceled) { setSystems(unwrapData(res) || []); done() } }).catch(() => { if (!canceled) { setSystems([]); done() } })
    serverGroupsApi.list().then((res: any) => { if (!canceled) { setDbGroups(unwrapData(res) || []); done() } }).catch(() => { if (!canceled) { setDbGroups([]); done() } })
    serverManagement.list(false).then((res: any) => { if (!canceled) { setServerInventory(unwrapData(res) || []); done() } }).catch(() => { if (!canceled) { setServerInventory([]); done() } })
    return () => { canceled = true }
  }, [setSystems, setServerInventory])

  useEffect(() => {
    let canceled = false
    if (!system) {
      setSystemGroups([])
      return () => { canceled = true }
    }
    const load = async () => {
      try {
        if (environment) {
          const envRes = await resource.systemGroups.list(system, environment)
          if (canceled) return
          const envGroups = normalizeSystemGroups(unwrapData(envRes))
          if (envGroups.length > 0) {
            setSystemGroups(envGroups)
            return
          }
        }
        const res = await resource.systemGroups.list(system)
        if (!canceled) setSystemGroups(normalizeSystemGroups(unwrapData(res)))
      } catch {
        if (!canceled) setSystemGroups([])
      }
    }
    void load()
    return () => { canceled = true }
  }, [system, environment])

  useEffect(() => {
    const seen = new Set<string>()
    const merged: any[] = []
    for (const g of [...systemGroups, ...dbGroups]) {
      const key = g?.name || g?.id
      if (!key || seen.has(key)) continue
      seen.add(key)
      merged.push(g)
    }
    setServerGroupList(merged)
  }, [systemGroups, dbGroups, setServerGroupList])

  useEffect(() => {
    let canceled = false
    if (!system) {
      setServices([])
      setEnvironments([])
      setPipelines([])
      return () => { canceled = true }
    }
    resource.services(system, environment || undefined).then((res: any) => { if (!canceled) setServices(unwrapData(res) || []) }).catch(() => { if (!canceled) setServices([]) })
    resource.environments(system).then((res: any) => { if (!canceled) setEnvironments(unwrapData(res) || []) }).catch(() => { if (!canceled) setEnvironments([]) })
    pipelineApi.list(system).then((res: any) => { if (!canceled) setPipelines(unwrapData(res) || []) }).catch(() => { if (!canceled) setPipelines([]) })
    return () => { canceled = true }
  }, [system, environment, setServices, setEnvironments, setPipelines])

  useEffect(() => {
    let canceled = false
    files.deployPackages({ system: system || undefined, service: service || undefined })
      .then(async (res: any) => {
        if (canceled) return
        const filtered = unwrapData(res) || []
        if (filtered.length > 0 || !system) {
          setDeployPackages(filtered)
          return
        }

        const systemRes = await files.deployPackages({ system: system || undefined })
        if (canceled) return
        const systemPackages = unwrapData(systemRes) || []
        if (systemPackages.length > 0) {
          setDeployPackages(systemPackages)
          return
        }

        const allRes = await files.deployPackages()
        if (!canceled) setDeployPackages(unwrapData(allRes) || [])
      })
      .catch(() => { if (!canceled) setDeployPackages([]) })
    return () => { canceled = true }
  }, [system, service, setDeployPackages])

  useEffect(() => {
    let canceled = false
    if (!pipelineId) {
      setPipelineSteps([])
      return () => { canceled = true }
    }
    pipelineApi.get(pipelineId).then((res: any) => {
      if (canceled) return
      const payload = unwrapData(res) || {}
      setPipelineSteps(payload.steps || payload.pipeline?.steps || [])
    }).catch(() => { if (!canceled) setPipelineSteps([]) })
    return () => { canceled = true }
  }, [pipelineId, setPipelineSteps])

  return { optionsLoading }
}
