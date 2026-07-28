# 服务器迁移 SOP（标准操作流程）

> 基于 bacteria 服务器迁移实战提炼，适用于后续 20+ 台业务服务器的迁移工作。
> 配套主控脚本：`migrate_server.py`

## 适用场景

- 通过跳板机管理的生产业务服务器迁移
- 服务以 PM2 + systemd 混合方式管理
- 业务文件部署在固定目录（如 /data/bin/xxx）
- 涉及 redis/psql 等外部依赖切换

## 前置条件

1. **OPS 平台已运行**，本地可调 tool_registry
2. **新旧跳板机密钥**已放置到 `data/keys/`
3. **新机已初始化**（OS 装好、网络通、能 apt update）
4. **新 redis/psql 地址**已知（凭据是否变化需提前确认）

## 完整流程（8 阶段）

### 阶段 1：盘点旧机

**目标**：查清旧机跑着什么、怎么部署、依赖什么。

```bash
# 1.1 查 OPS DB 里旧机配置（密钥/jump_host）
python scripts/migration/migrate_server.py inventory --old-server "旧机名"

# 1.2 SSH 进旧机盘点
python scripts/migration/migrate_server.py ssh-inventory --old-server "旧机名"
```

**盘点要点**：
- 业务进程（PM2 jlist / systemctl / ps）
- 部署目录（/data/bin/ /opt/ /srv/）
- 配置文件里的外部依赖地址（redis/psql/mq）
- 运行时依赖版本（redis/node/pm2/caddy/etcd）
- systemd 自启项
- 磁盘/内存

### 阶段 2：登记新机到 OPS

**目标**：把新跳板机、新目标机登记到 OPS DB。

```bash
python scripts/migration/migrate_server.py register \
  --jump-name "新跳板名" --jump-host x.x.x.x --jump-port 33890 \
  --jump-user root --jump-key "密钥名" \
  --target-name "新目标机名" --target-host y.y.y.y --target-port 22 \
  --target-user root --target-key "密钥名"
```

### 阶段 3：连通性测试 + SSH 代理配置

**目标**：建立本地经跳板连目标机的 SSH 代理通道。

```bash
python scripts/migration/migrate_server.py setup-proxy \
  --jump-name "新跳板名" --target-host y.y.y.y
```

**生成** `data/tmp/ssh_proxy_config`，后续所有 SSH/scp 都用 `-F data/tmp/ssh_proxy_config`。

**Windows 密钥权限修复**（必须）：
```powershell
icacls data\keys\密钥名 /inheritance:r /grant:r "$($env:USERNAME):F"
```

### 阶段 4：同步业务文件

**目标**：把旧机业务文件原样拷到新机。

```bash
python scripts/migration/migrate_server.py sync-files \
  --old-server "旧机名" --target-host y.y.y.y \
  --paths "/data/bin/业务目录 /data/www/前端目录 /etc/caddy/Caddyfile" \
  --exclude "*.log *.bak"
```

**流程**：旧机 tar 打包 → 下载到本地 → 上传到新跳板 → scp 到新机 → 解压

**关键点**：
- 打包时排除 logs，但 **不要排除 .bak**（后续要补传）
- 跨机传输用 `scp -3`（经本地中转）

### 阶段 5：改配置 + 装依赖

**目标**：改新机配置里的依赖地址，装运行时依赖。

```bash
# 5.1 改配置（sed 替换 redis/psql host）
python scripts/migration/migrate_server.py patch-config \
  --target-host y.y.y.y \
  --old-redis "旧redis地址" --new-redis "新redis地址" \
  --old-psql "旧psql地址" --new-psql "新psql地址"

# 5.2 装依赖
python scripts/migration/migrate_server.py install-deps \
  --target-host y.y.y.y --deps "redis caddy node pm2 etcd"
```

**版本对齐**：
- 优先 apt 装，版本不一致时从源码编译或从旧机拷贝二进制
- **注意 GLIBC 兼容性**：跨 Debian 大版本（13→12）的二进制可能不兼容
- etcd 等无 apt 源的，直接从旧机拷贝二进制 + 数据目录

### 阶段 6：核对一致性

**目标**：确保新机文件/配置与旧机完全一致。

```bash
python scripts/migration/migrate_server.py verify \
  --old-server "旧机名" --target-host y.y.y.y
```

**核对项**：
- 业务二进制 md5
- .bak 文件 md5
- config.yaml 端口 + 依赖地址
- 前端文件 md5
- 静态资源目录文件数 + 大小
- start.sh 脚本内容
- Caddyfile 内容
- etcd 目录 + 数据

### 阶段 7：切换

**目标**：停旧机 → 等待 → 启新机。

```bash
python scripts/migration/migrate_server.py switch \
  --old-server "旧机名" --target-host y.y.y.y \
  --ecosystem /root/ecosystem.config.js \
  --wait-seconds 60 \
  --systemd-services "caddy" \
  --skip-services "redis-server"
```

**切换后收尾**：
- `pm2 save` 保存进程配置
- `systemctl enable` 设置开机自启
- 验证监听端口 + 进程稳定性（等 10 秒看有无 crash）

## 常见坑 & 解决方案

### 1. PowerShell 引号嵌套
**问题**：ssh 命令里的 `$(...)`、`$var`、`'...'` 在 PowerShell 里被解析。
**解决**：复杂命令写成 .sh 脚本 → scp 上传 → `bash 脚本` 执行。

### 2. Windows 密钥权限
**问题**：ssh 拒绝使用权限过开放的密钥。
**解决**：`icacls 密钥路径 /inheritance:r /grant:r "$($env:USERNAME):F"`

### 3. SSH ProxyJump 超时
**问题**：paramiko 的 direct-tcpip 实现经某些跳板会超时。
**解决**：用 ssh_config 文件的 `ProxyJump` 指令，走 OpenSSH 原生实现。

### 4. GLIBC 版本不兼容
**问题**：旧机 Debian 13（GLIBC 2.41）的二进制拷到新机 Debian 12（GLIBC 2.36）跑不了。
**解决**：从源码编译，或用静态链接二进制（Go 二进制天然静态链接，无此问题）。

### 5. .bak 文件漏传
**问题**：打包时 `--exclude='*.bak'` 导致备份漏传。
**解决**：打包时只排除 `*.log`，不排除 `*.bak`；或漏传后单独补传。

### 6. pm2 install 自动起 daemon
**问题**：`pm2 install pm2-logrotate` / `pm2 --version` 会自动启动 daemon。
**解决**：安装后立即 `pm2 kill` + `pkill -9 -f PM2`。

### 7. heredoc 经多层 ssh 失效
**问题**：`cat > file << 'EOF'` 经 SSH ProxyJump 嵌套会丢失换行。
**解决**：本地创建文件 → scp 上传。

### 8. etcd 管理方式不一致
**问题**：旧机 etcd 由 PM2 管理，新机默认用 systemd。
**解决**：保持与旧机一致。同步旧机 etcd 目录 + start.sh，移除新机 systemd unit。

## 核对清单（迁移前必查）

- [ ] 新跳板机密钥已放 `data/keys/`
- [ ] 新目标机密钥已放 `data/keys/`
- [ ] 新 redis/psql 地址已知，凭据是否变化已确认
- [ ] 旧机业务进程清单已盘点（PM2/systemd/手工）
- [ ] 旧机部署目录已盘点
- [ ] 旧机外部依赖地址已抓取（redis/psql/mq）
- [ ] 新机 OS 版本 >= 旧机（避免 GLIBC 问题）
- [ ] 新机磁盘空间 >= 旧机业务文件大小
- [ ] 新跳板机 SSH 端口已确认（可能非 22）
- [ ] SSH ProxyJump 通道已测通
- [ ] 业务文件 md5 核对通过
- [ ] 配置文件依赖地址已改
- [ ] 运行时依赖版本已对齐
- [ ] 切换时间窗口已确认（业务低峰）

## 阶段 8：清理临时文件

**目标**：迁移收尾，清掉本地和远端的迁移残留，保持环境干净。

**清理顺序**：先清远端（要用 ssh_proxy_config 通道）→ 最后删本地 ssh_proxy_config。

### 8.1 清理新机远端临时文件

```bash
# 8.1.1 先列出新机 /tmp 残留，确认要删什么（不要盲删）
ssh -F data/tmp/ssh_proxy_config -o ConnectTimeout=15 root@新机 \
  "ls /tmp/ | grep -iE 'bact|redis|migrat|\\.tar|\\.sh$|\\.tgz$'"

# 8.1.2 按实际残留清理（示例：bacteria 迁移产生的 caddy.tgz 等）
ssh -F data/tmp/ssh_proxy_config -o ConnectTimeout=15 root@新机 \
  "rm -f /tmp/caddy.tgz /tmp/bacteria_key /tmp/redis802.tar.gz \
        /tmp/redis-src.tar.gz /tmp/migration_sync.tar.gz /tmp/_migrate_run.sh && \
   rm -rf /tmp/redis-8.0.2 && \
   echo cleaned"

# 8.1.3 验证清理结果
ssh -F data/tmp/ssh_proxy_config -o ConnectTimeout=15 root@新机 \
  "ls /tmp/ | grep -iE 'caddy|bact|redis|migrat' || echo no-migration-leftover"
```

**注意**：
- /tmp 下系统/服务正常文件（如 `AliyunAssistClientSingleLock.lock`、`systemd-private-*`、`aliyun_assist_service.sock`、`node-compile-cache`）**不要动**
- 如果新机用过 redis 源码编译（已废弃方案），需 `rm -rf /tmp/redis-8.0.2` 连同目录
- 跳板机侧的 known_hosts 条目可保留，无害

### 8.2 清理旧机远端临时文件（可选）

旧机切换后服务已停、机器即将下线，SSH 可能超时，**可跳过**。如确需清理：

```bash
ssh -F data/tmp/ssh_proxy_config -o ConnectTimeout=15 root@旧机 \
  "ls /tmp/ | grep -iE 'bact|redis|migrat|\\.tar' || echo no-migration-leftover"
```

SSH 超时（`Connection timed out during banner exchange`）即视为机器已下线，记录跳过即可。

### 8.3 清理本地临时文件

远端清理完成后，最后删除本地临时文件（含 ssh_proxy_config，已不再需要）：

```bash
# Windows 下用 Remove-Item；Linux/Mac 用 rm -f
# 推荐用工具批量删，避免误删 data/tmp 目录本身
Remove-Item data\tmp\bacteria_sync.tar.gz
Remove-Item data\tmp\ssh_proxy_config
Remove-Item data\tmp\*.sh            # 各阶段脚本：check_logs/diff_old_new/final_check/final_verify/old_baseline/prep_switch/start_new/stop_old/verify_deps/verify_final
Remove-Item data\tmp\*.output.txt    # 盘点/核对输出
Remove-Item data\tmp\*.json          # pm2_jlist.json
Remove-Item data\tmp\etcd.service data\tmp\caddy.service data\tmp\ecosystem.config.js
Remove-Item data\tmp\etcd_bin data\tmp\etcdctl_bin   # 二进制中转文件
```

**bacteria 迁移实际清理的 22 个本地文件清单**（参考）：
```
bacteria_sync.tar.gz      caddy.service              check_logs.sh
diff_old_new.sh           ecosystem.config.js        etcd.service
etcd_bin                  etcdctl_bin                final_check.sh
final_verify.sh           final_verify_output.txt    finalize.sh
new_diff_output.txt       old_baseline.sh            old_baseline_output.txt
pm2_jlist.json            prep_switch.sh             ssh_proxy_config
start_new.sh              stop_old.sh                verify_deps.sh
verify_final.sh
```

### 8.4 验证本地清理结果

```bash
# data/tmp 应为空（或仅剩非迁移相关的其他文件）
ls data/tmp/
```

### 保留的永久资产（不要删）

- `scripts/migration/README.md`（本 SOP 文档）
- `scripts/migration/migrate_server.py`（参数化主控脚本）
- `data/keys/` 下的跳板机/目标机密钥（后续迁移可能复用）

## 附录：PM2 ecosystem 配置模板

```javascript
// /root/ecosystem.config.js
module.exports = {
  apps: [
    { name: 'etcd', script: './start.sh', cwd: '/data/bin/etcd/etcd-v3.6.7', exec_mode: 'fork', autorestart: true },
    { name: 'core1', script: './start.sh', cwd: '/data/bin/bactera/core', exec_mode: 'fork', autorestart: true },
    // ... 按旧机 PM2 jlist 生成
  ],
};
```

从旧机 PM2 jlist 自动生成：
```bash
pm2 jlist | python3 -c "
import json, sys
data = json.load(sys.stdin)
print('module.exports = { apps: [')
for p in data:
    if p['name'] == 'pm2-logrotate': continue
    env = p.get('pm2_env', {})
    print(f\"  {{ name: '{p['name']}', script: './{env.get('pm_exec_path','').split('/')[-1]}', cwd: '{env.get('pm_cwd','')}', exec_mode: 'fork', autorestart: true }},\")
print('] };')
"
```
