export function useConnectionFilters(connections: any[], connSearch: string, connEnvFilter: string) {
  const envOptions = Array.from(new Set(connections.map((c: any) => c.environment).filter(Boolean)))
  const search = connSearch.trim().toLowerCase()
  const filteredConnections = connections.filter((c: any) => {
    const text = `${c.name || ''} ${c.environment || ''} ${c.db_type || ''} ${c.host || ''} ${c.database_name || ''}`.toLowerCase()
    return (!connEnvFilter || c.environment === connEnvFilter) && (!search || text.includes(search))
  })
  const connStats = {
    total: connections.length,
    prod: connections.filter((c: any) => c.environment === 'production').length,
    tunnel: connections.filter((c: any) => c.use_ssh_tunnel).length,
  }
  return { envOptions, filteredConnections, connStats }
}
