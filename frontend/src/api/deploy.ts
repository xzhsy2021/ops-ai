import { deployment } from '../api'
import type { DeploymentHistoryFilters, DeploymentHistoryPayload, DeploymentReport } from '../types/deploy'

function unwrap<T>(res: any): T {
  return (res?.data ?? res) as T
}

export const deployApi = {
  async list(filters: DeploymentHistoryFilters = {}): Promise<DeploymentHistoryPayload> {
    return unwrap<DeploymentHistoryPayload>(await deployment.list(filters))
  },
  async report(deploymentId: string): Promise<DeploymentReport> {
    return unwrap<DeploymentReport>(await deployment.report(deploymentId))
  },
  reportMarkdownUrl(deploymentId: string): string {
    return deployment.reportMarkdownUrl(deploymentId)
  },
}
