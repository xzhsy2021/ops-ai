# Matrix E2EE 接入运行手册（crypto store 配置与验证）

> 适用版本：matrix-nio 0.26.x + vodozemac 0.10.x（Python 3.14 验证通过）
> 关联代码：`app/services/matrix_e2ee.py`（会话/解密）、`app/services/matrix_client.py`（HTTP 客户端）、
> `app/api/matrix.py`（HTTP 端点）、`app/services/tool_adapters/matrix_tools.py`（MCP 工具）。

## 1. 背景与能力

OPS 的 Matrix 发布链路此前只支持未加密房间：加密房间的部署包事件为
`m.room.encrypted`（Megolm）包装，媒体密文带 `content.file`（key/iv/sha256），
无法直接下载使用。

本次接入 Matrix E2EE SDK 后：

- **持久化 crypto store**：Bot 设备的 Olm 账号密钥 + Megolm 入站会话密钥
  保存在 SQLite（nio `SqliteStore`），跨进程/重启保留。
- **加密房间事件解密**：scan / deploy 链路自动解密 `m.room.encrypted` →
  还原内层 `m.room.message`（含加密媒体描述 `content.file`）。
- **加密媒体下载**：密文优先走认证端点 `/_matrix/client/v1/media/download`
  （404 回退 v3），再按 Matrix 加密附件规范（AES-CTR + SHA256）本地解密。
- **优雅降级**：未安装 SDK 或未配置 E2EE 时行为与旧版一致——明文房间照常，
  加密媒体返回 400 并附可执行提示（保留 "E2EE" 关键字契约）。

## 2. 配置项（.env）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MATRIX_E2EE_ENABLED` | `true` | 总开关；关闭后完全回退旧行为 |
| `MATRIX_HOMESERVER_URL` | 空 | 复用既有 Matrix 连接配置（必填） |
| `MATRIX_ACCESS_TOKEN` | 空 | Bot/Service 账号 access token（必填） |
| `MATRIX_USER_ID` | 空（自动） | `@bot:example.org`；留空时经 `/account/whoami` 解析 |
| `MATRIX_DEVICE_ID` | `OPS-AI-BOT` | 设备 ID；**必须稳定**，更换即全新设备、丢历史 room key |
| `MATRIX_CRYPTO_STORE_PATH` | `<APP_DATA_DIR>/matrix/crypto_store` | crypto store 目录；实际 SQLite 为 `<user_id>_<device_id>.db` |
| `MATRIX_E2EE_SYNC_TIMEOUT_MS` | `8000` | 解密前增量 sync 的长轮询超时 |

最小启用步骤：

```bash
# 1) 安装依赖（已加入 requirements.txt）
pip install "matrix-nio[e2e]"

# 2) .env 增配（Bot 账号需已在目标房间）
MATRIX_HOMESERVER_URL=https://matrix.example.org
MATRIX_ACCESS_TOKEN=syt_xxx
# 可选显式指定：
# MATRIX_USER_ID=@ops-bot:example.org
# MATRIX_DEVICE_ID=OPS-AI-BOT

# 3) 重启 OPS；首次启动自动生成 Olm 账号并上传设备密钥
```

## 3. 首次接入注意事项

### 3.0 与活跃 E2EE 客户端共存（专用设备守卫）

**绝对不要把 qclaw 等活跃 E2EE 客户端正在使用的 access token /
device_id 配置给 OPS 启用 E2EE。** 两端共用同一设备会：

1. OPS 首次运行重新生成 Olm 账号并上传，**覆盖服务端该设备的身份密钥**，
   原客户端立即无法解密新消息；
2. 两端共享同一条 to-device 队列，互相"偷走" `m.room_key`。

因此代码层有专用设备守卫：E2EE sync 只接受 `.env` 中配置的
`MATRIX_HOMESERVER_URL + MATRIX_ACCESS_TOKEN`；调用方动态传入的凭据
（Agent 参数）只用于明文操作（拉事件/下载未加密媒体），用于解密会被拒绝
（`not_configured` + 可执行提示）。如确需对其他 homeserver 启用，可显式设
`MATRIX_E2EE_ALLOW_ANY_DEVICE=true`（自担风险）。

当前部署实况（2026-08-22 已完成方案 A）：OPS 专用设备 **`OPS-AI-BOT`**
已登录 @han:hubtel.xyz @ matrix.hubtel.xyz（token 与开关已写入 `.env`，
E2EE 已开启）。端到端自检通过：私密加密房间内 Megolm 加密发送 →
`/messages` 拉取密文 → `decrypt_room_event` 解密回读一致。
qclaw 设备 `XHBNAXVZFQ` 全程未受影响。

1. **新设备可见性**：首次启动会在 homeserver 注册新设备（device_id =
   `MATRIX_DEVICE_ID`）。房间成员的客户端会出现"新设备"提示；Element 默认
   不向未验证设备自动分发 room key。若解密报 `unknown_session`：
   - 让发送者**重新上传一次部署包**（最简单）；或
   - 在成员端把 Bot 设备标记为"信任/已验证"；或
   - 从已有已验证端导出 room keys 后导入 OPS 的 crypto store。
2. **room key 只投递一次**：to-device 的 `m.room_key` 由服务端投递给设备后
   即消费。OPS 必须始终用同一 device_id + 同一 crypto store 同步；
   换 device_id 或清空 store = 无法解密历史消息。
3. **备份**：将 `MATRIX_CRYPTO_STORE_PATH` 目录纳入备份策略。
4. **安全**：access token 与 crypto store 组合等价于完整身份凭据，目录权限
   应限制为运行账号可读（Windows 下注意继承 ACL）。

## 4. 验证清单

```powershell
cd D:\code\ops-ai
& "C:\Users\admin\AppData\Local\Python\bin\python.exe" -m pytest tests\test_matrix_e2ee.py tests\test_matrix_client.py tests\test_matrix_deploy_api.py tests\test_matrix_tools.py -q
```

运行时自检（登录态 / crypto store / 解密能力）：

```bash
curl -s -H "Authorization: Bearer <ops-session-or-token>" \
  http://localhost:8000/api/v2/matrix/status | jq .data.e2ee
```

期望字段：`sdk_available=true`、`enabled=true`、`configured=true`；
触发过一次解密后 `session_active=true`、`olm_account_ready=true`。

端到端验证：

1. 在加密房间用个人账号上传一个测试包（tar.gz）。
2. 调 `POST /api/v2/matrix/rooms/{roomId}/scan`（带 sender）：应能看到
   `events[].encrypted=true` 且文件名正确；响应 `e2ee.decryptor_active=true`。
3. 调 `POST /api/deploy`（或 MCP 工具 `ops.matrix.pull_attachment`）拉包入
   文件中心，核对 SHA256 与原文件一致。

## 5. 故障排查

| 现象 | 原因与处置 |
| --- | --- |
| status 里 `sdk_available=false` | 未安装 `matrix-nio[e2e]`；安装后重启进程 |
| 400 提示 "E2EE 未配置完整" | 缺 `MATRIX_HOMESERVER_URL` / `MATRIX_ACCESS_TOKEN` |
| 400 提示 auth_failed | token 失效或无 whoami 权限；重新签发 Bot access token |
| 400 提示 unknown_session | 无该房间历史 room key：重发包 / 信任设备 / 导入 keys |
| sync_failed | 检查 homeserver 连通性；调大 `MATRIX_E2EE_SYNC_TIMEOUT_MS` |
| media_decrypt_failed | 密文被截断或哈希不匹配：重新让用户上传附件 |
| store 目录报错 | `MATRIX_CRYPTO_STORE_PATH` 不可写；修正权限后重启 |

## 6. 行为契约（回归要点）

- 未配置/不可用时的加密媒体错误保持 **HTTP 400 且 detail 含 "E2EE"**
  （`test_matrix_deploy_e2ee_encrypted`、`test_pull_attachment_e2ee_blocked`）。
- `MatrixClient.download_media` 仍走 v3 未认证端点
  （`test_download_media` 断言路径）。
- 明文 m.room.message 带 `content.file`（无 url）仍识别为 encrypted=True
  （`test_list_media_events_detects_e2ee_encrypted`）。
- `debug_recent_events` 新增 `encrypted_events` / `encrypted_in_window`
  计数，hint 追加加密房间提示；既有键不变。
