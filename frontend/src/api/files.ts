import { files } from '../api'
import type { DeployPackageItem } from '../types/file'
function unwrap<T>(res: any): T { return (res?.data ?? res) as T }
export const filesApi = { async deployPackages(): Promise<DeployPackageItem[]> { return unwrap<DeployPackageItem[]>(await files.deployPackages()) } }
