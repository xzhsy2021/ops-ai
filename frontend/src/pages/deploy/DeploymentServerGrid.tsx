export type DeploymentServerStatus = {
  name: string
  status: 'pending' | 'running' | 'success' | 'failed'
  detail?: string
}

export function DeploymentServerGrid({ servers }: { servers: DeploymentServerStatus[] }) {
  if (!servers.length) return null

  return (
    <div className="deployment-server-grid">
      {servers.map((server) => (
        <div
          key={server.name}
          className={`deployment-server-card deployment-server-card--${server.status}`}
        >
          <strong>{server.name}</strong>
          <span>
            {server.detail ||
              (server.status === 'pending'
                ? '等待中'
                : server.status === 'running'
                  ? '执行中'
                  : server.status === 'success'
                    ? '成功'
                    : server.status === 'failed'
                      ? '失败'
                      : server.status)}
          </span>
        </div>
      ))}
    </div>
  )
}