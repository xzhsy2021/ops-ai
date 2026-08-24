# Matrix 部署包对接（Matrix Deploy Package Integration）

通过 Matrix 房间直接触发发布：用户在房间发送部署包（`m.file` / `m.image` / `m.video` / `m.audio`，
无需 @bot），随后 @bot 下达发布指令；Agent 调用运维中心 API，自动完成
「拉房间事件 → 找最新媒体 → 下载 → 入文件中心 → 走现有发布流程 → 审计」整条链路。

## 身份与配置

### 方式一：环境变量固定配置（兜底）

在 `.env` 中配置（未配置时 Matrix 发布接口返回 503）：

```env
MATRIX_HOMESERVER_URL=https://matrix.example.org
MATRIX_ACCESS_TOKEN=syt_xxx
MATRIX_MEDIA_WINDOW_MINUTES=15   # 回看窗口（分钟），默认 15
MATRIX_HTTP_TIMEOUT=30           # HTTP 超时（秒）
MATRIX_MEDIA_MSGTYPES=m.file,m.image,m.video,m.audio
```

### 方式二：Agent 调用时动态传入（推荐，无需固定配置）

Matrix 的 `homeserver` 与 `access token` 由 Agent 在每次调用时传入，**不配置固定值**。
传入的连接参数优先于环境变量：

- HTTP API：请求体 `matrix.homeserverUrl` + `matrix.accessToken`（或顶层 `homeserver_url` / `access_token`）
- MCP 工具：参数 `homeserver_url` + `access_token`

E2EE 加密房间：已接入 Matrix E2EE SDK（`matrix-nio[e2e]`，vodozemac 后端）。
配置 `MATRIX_E2EE_*`（见 `.env.example` 与 `docs/runbooks/MATRIX_E2EE_SETUP.md`）后，
加密房间的 `m.room.encrypted` 事件会被解密参与匹配，加密媒体自动下载解密入库；
Bot 设备密钥与 room keys 持久化在 crypto store（默认 `data/matrix/crypto_store/`）。
未配置或解密失败时保持旧行为：返回带原因的 400 错误提示。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v2/matrix/status` | 连接与配置状态（session 鉴权） |
| POST | `/api/v2/matrix/rooms/{room_id}/scan` | 预览房间内匹配的媒体事件，不触发发布（session 鉴权）。events 为空时附带 `debug` 诊断（msgtype 分布 / 文字含文件名提示）|
| POST | `/api/v2/matrix/deploy` | 规范发布入口（session 或 Bearer tool token）。未找到媒体事件时 404 detail 附带 `debug` 信息，提示用户真上传附件 |
| POST | `/api/deploy` | 简短别名，Agent 常用（同一 handler） |

### 诊断信息（为什么没匹配到媒体事件？）

`ops.matrix.scan_media_events` / HTTP scan 在 events 为空时自动附带 `debug` 字段：

```json
{
  "events": [],
  "debug": {
    "total_events": 8,
    "room_message_count": 8,
    "in_window": 8,
    "out_of_window": 0,
    "window_minutes": 30,
    "msgtype_distribution": {"m.text": 7, "m.file": 0, "m.image": 0},
    "sender_distribution": {"@jack.han:hubtel.xyz": 8},
    "text_with_filename_hint": [
      {"event_id": "$x", "sender": "@jack.han:hubtel.xyz", "body": "crypto-trader-web.tar.gz"}
    ],
    "hint": "若 m.file / m.image / m.video / m.audio 计数为 0，说明用户在房间只发送了文字（m.text），并未真上传 Matrix 附件；请提示用户拖拽文件上传或使用附件按钮。"
  }
}
```

- **若 `msgtype_distribution` 中 `m.text >> m.file`**：用户只发了文件名文字，未真上传附件。提示用户用「附件按钮」或拖拽文件到房间。
- **若 `m.file` 存在但 sender 不匹配**：发送者身份不对（bot 抓到的 sender 与真实用户不同）。
- **若 `m.file` 存在但 `in_window=0`**：文件事件在时间窗口之外，调大 `minutes` 或调整扫描时间点。
- **若 `total_events=0`**：limit 太小或 Bot 没拉到事件（房间消息太多已超出 limit；可调大 `limit`，默认 200，Matrix API 上限）。

### 发布请求体（Agent 传入连接参数）

```json
{
  "env": "test",
  "system": "crypto-trader",
  "service": "crypto-frontend",
  "version": "crypto-frontend-v2.tar.gz",
  "matrix": {
    "roomId": "!room:example.org",
    "sender": "@alice:example.org",
    "triggerEventId": "$1539-xyz",
    "filename": "crypto-frontend",
    "homeserverUrl": "https://matrix.example.org",
    "accessToken": "syt_xxx"
  },
  "confirm_text": "CONFIRM",
  "reason": "matrix deploy"
}
```

说明：

- `env` 必填；生产环境（`prod` / `production`）需要管理员 session 或
  `allow_prod` tool token，并附 `confirm_text`（`CONFIRM` / `确认发布 xxx`）走现有二次确认。
- `service` 必填，`system` 可选（可留空由现有拓扑推断）。
- `matrix.roomId`、`matrix.sender` 必填。
- `matrix.triggerEventId` 可选：记录触发指令的事件 id（审计）。
- `matrix.filename` 可选：指令里带文件名时进一步按 body / filename 匹配。
- `matrix.minutes` 可选：覆盖默认回看窗口。
- 匹配规则：`sender == matrix.sender`，`msgtype` 在 `MATRIX_MEDIA_MSGTYPES` 内，
  `origin_server_ts` 在触发前 N 分钟内（默认 15 分钟），取最新一条；
  可选文件名匹配 `content.body` / `content.filename`。

### 返回

```json
{
  "success": true,
  "data": {
    "task_id": "ab12cd34ef56",
    "deployment_id": 123,
    "queued": true,
    "matrix": {
      "room_id": "!room:example.org",
      "media_event_id": "$1538-media",
      "trigger_event_id": "$1539-xyz",
      "sender": "@alice:example.org",
      "filename": "crypto-frontend-v2.tar.gz",
      "mxc_url": "mxc://example.org/AbCdEf"
    }
  }
}
```

## 与现有发布流程的复用

`app/api/deploy/executions.py` 抽取了共享的 `queue_deploy_v2(user, data, db)`：
Web 发布与 Matrix 发布复用同一套「确认 → 派生服务器 → 合并变量 → 环境一致性 →
建 Deployment → 记录制品引用 → 加部署锁 → 建 DeployTask → 拉起 worker → 审计 →
发布通知」逻辑，不重复实现。

Matrix 发布额外：

- 媒体下载到文件中心：`save_package_fileobj` 计算 SHA-256、按保留策略检查扩展名
  与大小（允许 `.tar.gz` / `.tgz` / `.tar` / `.zip` / `.jar` / `.war` / `.gz` / `.bin`）。
- **任意普通格式**：`ops_matrix_pull_attachment`（及 MATRIX_PULL 计划步骤）默认
  不限扩展名，`.txt` / `.pdf` / `.log` / `.conf` 等普通文件均可拉入文件中心
  （大小上限仍受 `max_upload_size_mb` 约束）。设 `MATRIX_PULL_ALLOW_ANY_EXTENSION=0`
  可恢复部署包白名单；`deploy_from_matrix` 发布动作保持部署包白名单不变。
- **入库自由、发布受限（两者不冲突）**：发版流程在两个卡点强制校验制品格式——
  `execute_release`（执行计划 RELEASE 步骤 / 旧审批发布共用）与
  `queue_deploy_v2`（Web/Matrix 发布入口）。非白名单后缀的包名会被拒绝
  （HTTPException 400，RELEASE 步骤转 FAILED），普通文件仅供入库留存，
  不能经自动回填或手动选择被当作制品发布。守卫实现见
  `package_retention.ensure_releasable_artifact`。
- `DeployPackage` 记录 `source_context` / `source_message_key`
  （`matrix:default:{room}:{event_id}`），可在文件中心追溯来源。
- 额外审计 `deploy.execute.matrix`，记录 `roomId`、`mediaEventId`、
  `triggerEventId`、`sender`、`env`、`service`、`file`、`sha256`、`task_id`。

## Agent 调用示例

### HTTP API（OpenClaw Agent 直接调用，homeserver/token 由 Agent 传入）

```bash
curl -X POST http://127.0.0.1:8000/api/deploy \
  -H "Authorization: Bearer ops_tool_xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "env": "test",
    "service": "crypto-frontend",
    "matrix": {
      "roomId": "!room:example.org",
      "sender": "@alice:example.org",
      "triggerEventId": "$1539-xyz",
      "homeserverUrl": "https://matrix.example.org",
      "accessToken": "syt_xxx"
    }
  }'
```

### MCP 工具（qclaw / OpenClaw Agent 通过 MCP 发现与调用）

Agent 通过 `ops.describe_capabilities` 或 MCP `tools/list` 发现以下 Matrix 工具；
homeserver 与 token 由 Agent 在参数里传入（`homeserver_url` / `access_token`），不配置固定值：

| 工具名（MCP 格式） | 说明 | 权限 | 连接参数 |
| --- | --- | --- | --- |
| `ops_matrix_scan_media_events` | 预览房间内匹配的媒体事件（只读，不下载） | `ops:read` | 可选 `homeserver_url`/`access_token` |
| `ops_matrix_pull_attachment` | 从 Matrix 房间拉取最新附件到文件中心，返回 `package_name` | `ops:read` + `package:write`，需确认短语 `CONFIRM ops.matrix.pull_attachment`（schema 已暴露 `confirm_text`） | 可选 `homeserver_url`/`access_token` |
| `ops_matrix_deploy_from_matrix` | 完整发布：拉附件 → 文件中心 → 排队发布（复用现有流程） | `ops:read` + `package:write` + `deploy:execute`，需审批 | 可选 `homeserver_url`/`access_token` |

典型 MCP 调用流程（Agent 传入连接参数）：

1. **扫描** `ops_matrix_scan_media_events(room_id="!room:example.org", sender="@alice:example.org", homeserver_url="https://matrix.example.org", access_token="syt_xxx")` → 确认房间内最新媒体事件
2. **拉附件** `ops_matrix_pull_attachment(room_id="!room:example.org", sender="@alice:example.org", service="api", confirm_text="CONFIRM ops.matrix.pull_attachment", homeserver_url="...", access_token="...")` → 返回 `{package_name, sha256, event_id}`
3. **创建发布计划** `ops_create_deploy_plan(package_name="...", system="...", service="api", environment="test")` → 返回 `{plan_id, confirm_text}`
4. **执行发布** `ops_execute_deploy_plan(plan_id="...", confirm_text="CONFIRM")` → 队列发布

也可以一步到位：`ops_matrix_deploy_from_matrix(room_id="...", sender="...", service="api", env="test", homeserver_url="...", access_token="...")`。

### 推荐：执行计划批量审批（一次审批完成拉取 + 发布）

面向 qclaw/Element 消息流，优先用消息级执行计划把「拉附件」和「发布」合并为**一次审批**。
授权人批准短码后，步骤按顺序自动执行，无需逐步确认：

```json
ops_approval_prepare_plan(
  message_context = { channel, channel_account_id, conversation_id, message_id, sender_id, content_sha256 },
  system_name="crypto", service_name="api", environment="test", targets=["s1"],
  steps=[
    { "step_key": "pull",
      "action_type": "MATRIX_PULL",
      "parameters": {
        "room_id": "!room:example.org",
        "sender": "@alice:example.org",
        "minutes": 30,
        "filename": "可选文件名过滤"
      },
      "dependencies": [] },
    { "step_key": "release",
      "action_type": "RELEASE",
      "parameters": {},
      "dependencies": ["pull"] }
  ]
)
```

- `MATRIX_PULL` 参数：`room_id`、`sender` 必填；`minutes` / `filename` / `system` / `service` / `overwrite` / `homeserver_url` / `access_token` 可选。审批详情会展示将拉取的房间、发送者与文件名提示。
- 拉取成功后，`package_name` / `sha256` 写入步骤结果；依赖它的 `RELEASE` 步骤**自动回填 package_name**，无需在 manifest 中预知包名（显式指定 `parameters.package_name` 时以显式值为准）。
- 拉取失败（房间无匹配媒体 / E2EE 解密失败）→ 计划 FAILED，后续 RELEASE 不执行。
- 计划内步骤已由该次审批覆盖，执行时不再要求各工具的 `confirm_text`。

E2EE 加密房间由 OPS 专用设备（`MATRIX_DEVICE_ID`，见 `.env` 与 `docs/runbooks/MATRIX_E2EE_SETUP.md`）自动解密；
历史消息的 room key 不补发，仅能解密设备创建之后新发送的媒体。

## 测试

```bash
python -m pytest tests/test_matrix_client.py tests/test_matrix_deploy_api.py tests/test_matrix_tools.py -q
```
