import { useState } from 'react'

export interface SystemOption {
  name: string
  display_name: string
  strategy: string
  environment_count: number
  service_count: number
}

export interface ServiceOption {
  id: string
  name: string
  display_name: string
  system_name: string
  repo: string
  template?: string
  template_variables?: Record<string, any>
  servers?: string[]
  servers_by_env?: Record<string, string[]>
  server_keywords?: string[]
  pipeline_id?: string
  source?: string
}

export interface EnvironmentOption {
  id: string
  name: string
  display_name?: string
  type: string
  category?: string
  description?: string
  deploy_path?: string
  health_url?: string
  variables?: Record<string, any>
  servers?: string[]
}

export interface PipelineOption {
  id: string
  name: string
  system_name: string
  description: string
  strategy: string
}

export function useDeployFormState() {
  const [systems, setSystems] = useState<SystemOption[]>([])
  const [services, setServices] = useState<ServiceOption[]>([])
  const [environments, setEnvironments] = useState<EnvironmentOption[]>([])
  const [pipelines, setPipelines] = useState<PipelineOption[]>([])
  const [serverGroupList, setServerGroupList] = useState<any[]>([])
  const [serverInventory, setServerInventory] = useState<any[]>([])
  const [deployPackages, setDeployPackages] = useState<any[]>([])

  const [system, setSystem] = useState('')
  const [service, setService] = useState('')
  const [environment, setEnvironment] = useState('')
  const [fileName, setFileName] = useState('')
  const [servers, setServers] = useState('')
  const [pipelineId, setPipelineId] = useState('')
  const [pipelineSteps, setPipelineSteps] = useState<any[]>([])
  const [serverGroup, setServerGroup] = useState('')
  const [serverAutoMode, setServerAutoMode] = useState(true)
  const [parallelism, setParallelism] = useState(1)
  const [failFast, setFailFast] = useState(true)

  const [resolveResult, setResolveResult] = useState<any>(null)
  const [releaseConfirmation, setReleaseConfirmation] = useState<any>(null)
  const [confirmingRelease, setConfirmingRelease] = useState(false)
  const [resolving, setResolving] = useState(false)

  return {
    systems, setSystems,
    services, setServices,
    environments, setEnvironments,
    pipelines, setPipelines,
    serverGroupList, setServerGroupList,
    serverInventory, setServerInventory,
    deployPackages, setDeployPackages,
    system, setSystem,
    service, setService,
    environment, setEnvironment,
    fileName, setFileName,
    servers, setServers,
    pipelineId, setPipelineId,
    pipelineSteps, setPipelineSteps,
    serverGroup, setServerGroup,
    serverAutoMode, setServerAutoMode,
    parallelism, setParallelism,
    failFast, setFailFast,
    resolveResult, setResolveResult,
    releaseConfirmation, setReleaseConfirmation,
    confirmingRelease, setConfirmingRelease,
    resolving, setResolving,
  }
}
