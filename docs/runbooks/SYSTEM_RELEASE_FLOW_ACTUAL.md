# 系统发布流程（按当前实际操作落地）

本流程已在发布中心中落地为默认发布逻辑：当未选择自定义 Pipeline 时，系统会根据"系统 + 服务/分组"自动推导发布步骤与服务器。

## 1. Dovo 应用后台发布

适用分组：`bgd`、`idn`、`pak`、`tha`。

入口：发布中心选择系统 `dovo`，服务选择对应分组。

系统默认执行：

1. 根据分组配置找到服务器、`/data/bin/ata` 基础目录与实例目录，例如 `idn1`、`idn2`。
2. 检查两个实例的 `server` 进程，识别当前未运行的旧程序目录作为 standby。
3. 上传发布包到远端临时目录。
4. 将发布包写入 standby 目录；归档包会解压，普通文件会写成 `server.new`。
5. 在 standby 目录执行 `binupdate.sh`。
6. 查看 standby 程序进程与日志。
7. 在同一 standby 目录执行 `portupdate.sh` 切换端口。
8. 再次查看日志，确认切换完成。

注意：`portupdate.sh` 必须在对应实例目录执行，不能跨目录执行。

## 2. 量化应用后台发布

适用系统：`crypto-trader`。

入口：发布中心选择系统 `crypto-trader`，服务选择具体后台服务。

### 2.1 线上环境 (updatebin.sh 方式)

适用服务：`Exchange`、`Monitor`、`Puller`、`Risk`、`Sender`、`Strategy`、`Supplier`、`System`、`Trader`、`Transaction` 等。

系统默认执行：

1. 根据服务配置找到目录，例如 `/data/bin/crypto-trader/system`。
2. 上传本地发布包到目标服务目录。
3. 切换到服务目录执行 `updatebin.sh`。
4. 查看程序进程与 `logs` 下最近日志。

如同一服务需要多台服务器更新，可在"服务器"输入框填写多台服务器，系统会按服务器逐台执行相同流程。

### 2.2 测试环境 (Docker Compose 方式)

适用服务：`Docker Compose 统一部署`。

目标服务器：量化测试服务器 (`cc-test2`)、量化测试服务器2。

部署路径：`/data/crypto-trader`，镜像直接从仓库拉取。

系统默认执行：

1. 检查 compose 目录 `/data/crypto-trader` 是否存在。
2. 执行 `docker compose -f docker-compose.yml pull` 拉取最新镜像。
3. 执行 `docker compose -f docker-compose.yml up -d --remove-orphans` 启动/重启容器。
4. 等待容器稳定（默认 10 秒）。
5. 执行 `docker compose -f docker-compose.yml ps` 检查容器状态。
6. 执行 `docker compose -f docker-compose.yml logs --tail=30` 查看最近日志。

如同一服务需要多台服务器更新，可在"服务器"输入框填写多台服务器，系统会按服务器逐台执行相同流程。

## 3. Web 发布

入口：选择任意前端服务，或使用 Web 发布模板。

系统默认执行：

1. 上传本地包到 `/data/www`。
2. 切换到 `/data/www` 执行 `./www.sh`。
3. 列出目录末尾内容，作为发布结果确认。