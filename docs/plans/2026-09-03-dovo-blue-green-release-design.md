# Dovo 后台蓝绿发版流程设计（2026-09-03）

## 1. 业务事实（与运维确认，2026-09-03）

- **多区域分组**：dovo 含 pak / bgd / tha / idn / ind(印度) 等区域；每区域多台服务器，
  各台独立提供业务（代码一致、服务对象不同），**并行部署互不影响**。
- **蓝绿双目录**：每台服务器 `/data/bin/ata/{region}1`、`{region}2` 两套程序，
  active 接任务，standby 程序在但不接任务。
- **共享配置**：`/data/bin/ata/config/global.yml` 全区共享——`task_port`（active 判定锚点）
  + `front_version`（前端版本时间戳）。**端口规律全区一致**：`{region}1=:8001`、`{region}2=:8002`。
- **进程管理**：pm2 托管 `{region}1.f` / `{region}2.f`（fork mode，root），**两个进程永远
  同时 online**；日志 `/root/.pm2/logs/{dir}.f-out.log` / `{dir}.f-error.log`
  （pm2-logrotate 按小时滚动，当前文件无时间戳后缀）。
- **脚本**：
  - `binupdate.sh`（目录内）：更新该目录程序（含 pm2 重启 standby）。
  - `portupdate.sh`（目录内，各自硬编码本目录端口）：sed 改 `global.yml` 的 task_port +
    Caddyfile `/api/*` 反代端口 + `systemctl reload caddy`。**切到哪个目录就在哪个目录执行**。
  - `www.sh`（`/data/www/`）：前端更新——解压 `dist.zip`、备份 `ata`→`ata.bak`、
    `front_version` 改为 14 位时间戳。仅前端流程涉及，后端不动此参数。
- **轮换语义**：切换后旧 active **保留运行**（pm2 online，日志停刷，不再接业务），
  自然成为下一轮的 standby。
- **包来源现状**：更新包从 Matrix 房间或本地共享目录提供，运维手动上传至服务器（待改造，
  见 §6 待确认）。
- **回滚**：验证失败自动回切（在新 active 验证失败时，于旧 active 目录执行 portupdate.sh）。

## 2. 建模（数据侧，一次配置）

系统 `dovo` 已存在（无服务无路由）。新增服务（区域一组 + 前端）：

| Service | template_variables（核心字段） | servers |
|---|---|---|
| dovo-pak | 见下方 bg 变量（dirs=pak1/pak2, ports 8001/8002） | pak 组服务器清单 |
| dovo-bgd / dovo-tha / dovo-idn / dovo-ind | 同构（dirs=bgd1/bgd2 …） | 各组清单 |
| dovo-web | `deploy_path=/data/www`、`update_script=./www.sh`、`template=generic_frontend` | 各区域全体 |

蓝绿服务变量示例（dovo-pak）：

```json
{
  "template": "blue_green",
  "bg_base_dir": "/data/bin/ata",
  "bg_dirs": ["pak1", "pak2"],
  "bg_port_map": {"pak1": ":8001", "pak2": ":8002"},
  "bg_config_file": "/data/bin/ata/config/global.yml",
  "bg_port_field": "task_port",
  "bg_update_script": "./binupdate.sh",
  "bg_port_script": "./portupdate.sh",
  "bg_pm2_pattern": "{dir}.f",
  "bg_log_dir": "/root/.pm2/logs",
  "bg_upload_path": "<待定：binupdate.sh 的包读取路径>"
}
```

> 关键设计决策：**active/standby 是运行时瞬态，绝不能预探测写死进计划**（计划参数静态冻结
> 防篡改，而批准→执行的窗口内轮换状态可能变化）。计划只冻结静态事实（目录/端口映射/脚本），
> 瞬态判定由执行器在每台服务器上现场探测。agent 前置探测仅作人读信息，执行器不信任它。

## 3. 执行器：`SERVICE_CONTROL` 新增 `control_action=bg-update`

每台服务器独立执行（一台失败不阻断其余，沿用现有 SERVICE_CONTROL 多 targets 语义）：

```
① 探测   cat global.yml → task_port → port_map 反查 → active_dir / standby_dir
          （每台现探，不信任计划前的探测结果）
② 更新    cd {standby_dir} && ./binupdate.sh        ← 失败→中止该台，端口未动，线上零影响
③ 验证    pm2 jlist：standby 进程 online 且 pid 已换（重启生效）
          out 日志 mtime 刷新 + error 日志无新增         ← 失败→同上中止
④ 切换    cd {standby_dir} && ./portupdate.sh        ← task_port→standby 端口 + caddy 重载
⑤ 复验    global.yml task_port == standby 端口
          standby out 日志持续刷新（已接任务）          ← 失败→自动回滚 ↓
⑥ 回滚    cd {active_dir} && ./portupdate.sh（切回旧端口）
          回滚后复验 task_port 归位；回滚失败→标记 CRITICAL（该台需人工介入）
```

- **验证锚点全部取自用户提供的真实环境**：pm2 jlist（online+pid 换代）、out 日志 mtime
  （程序在刷日志）、error 日志无新增（不报错）、global.yml task_port（切换生效）。
- **旧 active 不做任何 stop/restart**（pm2 继续在线，自然降级为下轮 standby）。

## 4. 两条 Flow Guide（参数化）

### dovo-bg-release（后台蓝绿）
```
触发：dovo + 区域词（pak/bgd/tha/idn/ind/印度/孟加拉…）+ 更新/发版/后台
① scan_media_events：附件是否已在文件中心（L010 事件指纹判定）
② resolve_message_target：消息→dovo-{region}
③ prepare_plan：一次审批覆盖整链
   steps_template = [MATRIX_PULL（若①为 false）
                     FILE_UPLOAD（包→每台 {bg_upload_path}）
                     SERVICE_CONTROL(bg-update, targets=组内全部)]
④ reply_template → 审批人批准
⑤ execute_plan → 逐台汇报：active→standby 切换结果 + 验证结论
```

### dovo-frontend-release（前端）
**完全复用现有 generic_frontend 链路**（MATRIX_PULL + FILE_UPLOAD dist.zip→/data/www/
+ SERVICE_CONTROL update 跑 www.sh）——与 crypto-trader-web 同构，只是服务配置不同，
无需新代码。front_version 时间戳由 www.sh 内部维护。

## 5. 审批与安全（全部复用现有机制）

- **一次审批整链**：MATRIX_PULL + FILE_UPLOAD + SERVICE_CONTROL(bg-update) 一个计划一批
  审批文案（targets=服务器清单，影响面一屏可见）。
- **测试环境自审批**：bg-update 归入 SERVICE_CONTROL 动作集，现有 TEMPORARY_SELF_APPROVAL
  授权（含 SERVICE_CONTROL）天然覆盖。
- **防误删**：FILE_UPLOAD 的 remote_path 走 confirm_path 双确认（现有机制）。

## 6. 待确认（实施前最后三项）

1. **binupdate.sh 内容**（唯一未知脚本）：包从哪个路径读取（决定 FILE_UPLOAD 的
   remote_path 约定）？是否内部自带 pm2 restart？若按 OPS 约定改造：建议统一上传到
   `{bg_base_dir}/incoming/{package}`，binupdate.sh 改为从 `../incoming/` 取包。
2. **各区域服务器清单**：每组发版 targets 用哪些（如 pak 组 6 台主 + 3 台 bak——
   bak 服务器是否参与轮换发版？）。
3. **dovo Matrix 路由**：消息路由房间 + 审批人（systems.message_routing 现为空）。

## 7. 实施清单（确认后执行）

- [ ] `blue_green.py` 执行器（探测/更新/验证/切换/复验/回滚 六段式，SSH 复用现有层）
- [ ] `bg-update` 并入 SERVICE_CONTROL 动作分发（plan_executor + server_tools 命令层）
- [ ] FLOW_GUIDES 新增 dovo-bg-release / dovo-frontend-release（参数化）
- [ ] 契约测试：执行序列、验证失败中止、自动回滚、端口探测反查、pm2 判定
- [ ] 数据录入：5 区域服务 + dovo-web + dovo 路由（待 §6 三项确认）
