# 量化生产服务器 Docker 状态与安装记录

> 执行日期：2026-09-10
> 执行方式：方案 A（管理员通道）—— 走 OPS `POST /api/v2/servers/{name}/exec`
> （`require_admin` + `validate_command` + `audit("server.exec")` + `record_execution` 落库）
> 执行工具：`fnos-migration/ops_exec.py`
> 原始需求：房间 `!LZmupTKKHXJwRvomBE`「四台服务器 43.106.12.129 / 43.106.14.247
> / 43.106.14.120 / 47.84.52.170 都先装个 docker」

## 1. 最终状态（核验于 2026-09-10 18:2x）

| 服务器 | 主机名 | Docker 版本 | dockerd | 说明 |
|---|---|---|---|---|
| 43.106.12.129-量化-主节点 | `ct-prod` | 29.0.1 | **inactive**（enabled，socket 亦 inactive） | **原本就装了**，2025-11-17 干净停止（`status=0/SUCCESS`），`/var/lib/docker` 仅 244K、无镜像无容器 |
| 43.106.14.247-量化-2节点 | `cc-prod2` | **29.8.0（本次新装）** | **active** ✅ | 新装，engine overlayfs / cgroup v2，compose v5.5.1 |
| 43.106.14.120-量化-puller | `ct-puller` | **29.8.0（本次新装）** | **active** ✅ | 新装，engine overlayfs / cgroup v2，compose v5.5.1 |
| 47.84.52.170-量化-etcd | `cc-prod3` | 29.3.1 | active ✅ | **原本就装了并运行中** |

**与原任务前提的差异**：4 台中 2 台（主节点、etcd）**早已安装 Docker**，
实际只需为 **2节点** 与 **puller** 安装。

## 2. 安装方式（与既有两台保持一致）

Docker 官方仓库（非 Debian 自带 `docker.io`），与 `ct-prod`/`cc-prod3` 相同：

```bash
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
printf "Types: deb\nURIs: https://download.docker.com/linux/debian\nSuites: bookworm\nComponents: stable\nSigned-By: /etc/apt/keyrings/docker.asc\n" \
  > /etc/apt/sources.list.d/docker.sources
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

结果：`docker-ce 5:29.8.0-1~debian.12~bookworm`，`RC=0`，服务自动 enable + start。
`/etc/apt/sources.list.d/docker.sources` 与 keyring 与既有服务器一致（keyring 3817 B）。

## 3. 关键运维要点

| 要点 | 说明 |
|---|---|
| **必须后台安装** | OPS `/servers/{name}/exec` 的 `_MAX_EXEC_TIMEOUT = 60s`（`app/api/servers.py:45`）；`apt-get install` 可能超时，若前台执行被中断会留下 dpkg 半配置状态。故用 `nohup ... < /dev/null > /root/.docker-install.log 2>&1 &` 后台跑 + 轮询 |
| **跳板机** | 4 台均须经 `tiaoban-new`（`hop_context` 显示 `is_proxied: true`）；直连 TCP:22 不通属正常 |
| **网络** | `download.docker.com` 与 `mirrors.aliyun.com` 均 200、约 0.03s；**Docker Hub 可直连**（`hello-world` 拉取 3.8s 成功），无需配置 registry mirror |
| **apt 源** | 系统源走阿里云内网 `mirrors.cloud.aliyuncs.com`；新装的两台原仅有 nodesource 源 |
| **主节点现状** | Docker 已装但守护进程 10 个月未运行、`/var/lib/docker` 空 —— 疑似**有意停用**，启动前需确认（未擅自启动） |

## 4. 复现/核验命令

```bash
# 单台核验
docker --version; systemctl is-active docker; systemctl is-enabled docker
docker info --format "server={{.ServerVersion}} driver={{.Driver}}"
docker compose version

# 功能验证（真实拉取+运行）
docker run --rm hello-world
```

批量执行工具：

```powershell
# 4 台批量
python fnos-migration\ops_exec.py --all "<command>" [timeout]
# 单台
python fnos-migration\ops_exec.py <server_id> "<command>" [timeout]
```

## 5. 审计留痕

每次执行均写入 OPS：
- `audit` 表：`server.exec`（含 user / exit / dur / cmd 掩码 / risk level）
- 命令历史：`record_execution()`（Web「服务器 → 执行历史」可回看 stdout/stderr）

## 6. 后续（待决策）

- **主节点 `ct-prod` 的 dockerd 是否启动**：若需要按需使用，可 `systemctl start docker`
  （已 enabled，亦可通过 socket 激活）；若确属有意停用则保持现状。
- 版本不齐：主节点 29.0.1 / etcd 29.3.1 / 新装两台 29.8.0。如需统一，可对前两台做
  `apt-get install --only-upgrade docker-ce docker-ce-cli containerd.io`（属升级操作，需另行确认）。

## 7. 方案 B：让 agent 自己完成这类操作（已落地）

本次「装 docker」最初的目标是**让房间里的 agent 直接处理**，但当时撞到两个墙：
`ops.exec_remote` 对 AI 硬阻断、`approval.prepare_service_control` 不支持任意命令，
agent 无路可走便静默结束了轮次。为此新增了 ad-hoc 远程命令执行审批能力
（`ops.approval.prepare_exec`）：白名单模板 + 两级黑名单护栏 → 人工一次性短码
审批 → SHA256 复核后串行执行。

设计稿与落地记录（含 7 处与初稿的偏离、运行时验证、启用与回滚方式）见
[`exec-remote-approval-design.md`](./exec-remote-approval-design.md) §11。

开关：`allow_exec_remote_tool`（**2026-09-10 已开启**）。
查看/切换：`python fnos-migration/exec_remote_switch.py [show|on|off]`。
