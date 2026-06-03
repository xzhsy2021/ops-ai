import { serverManagement } from '../api'
import type { ServerGroupItem, ServerHealthProbe, ServerItem } from '../types/server'
function unwrap<T>(res: any): T { return (res?.data ?? res) as T }
export const serversApi = {
  async list(withStatus = true): Promise<ServerItem[]> { return unwrap<ServerItem[]>(await serverManagement.list(withStatus)) },
  async groups(): Promise<ServerGroupItem[]> { return unwrap<ServerGroupItem[]>(await serverManagement.groups.list()) },
  async health(name: string, force = false): Promise<ServerHealthProbe> { return unwrap<ServerHealthProbe>(await serverManagement.opsHealth(name, force)) },
}
