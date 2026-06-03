# 系统服务查看与编辑

本次补充“系统服务配置”能力，用于解决发布页服务下拉不可维护、无法直观看到服务目录/脚本/服务器规则的问题。

## 入口

进入：系统 -> 选择系统 -> 服务选项卡 -> 服务配置。

页面会展示当前系统下的所有服务，数据来源于系统配置 `systems.<system>.services`。

## 可维护字段

每个服务支持维护：

- 服务名称：发布页传给后端的服务标识，例如 `crypto-system`。
- 显示名称：发布页服务下拉显示名，例如 `System`。
- 发布模板：例如 `generic_backend_direct`、`generic_frontend`、`dovo_bluegreen_update`。
- 服务目录：`template_variables.service_dir`，例如 `/data/bin/crypto-trader/system`。
- Web 部署目录：`template_variables.deploy_path`，例如 `/data/www`。
- 更新脚本：`template_variables.update_script`，例如 `./updatebin.sh` 或 `./www.sh`。
- 进程/服务名：`template_variables.service_name`、`template_variables.pm2_name`。
- 服务器匹配关键字：`template_variables.server_keywords`，用于发布页按服务推荐服务器。
- 环境服务器映射：`template_variables.servers_by_env`，用于精确配置 test/prod 不同目标服务器。
- 完整 `template_variables` JSON：高级配置入口。

## 发布页联动

发布页选择系统后，会加载当前系统下所有服务。选择服务后：

1. 显示当前系统服务数量。
2. 显示当前服务模板、目录、脚本。
3. 根据服务关键字、环境、服务器组推荐目标服务器。
4. 从系统服务列表进入发布页时，可以点击“查看/编辑服务”跳回系统的服务配置页。

## 量化示例

`crypto-trader` 下可以维护多个服务：

- `crypto-system` -> `/data/bin/crypto-trader/system`
- `crypto-puller` -> `/data/bin/crypto-trader/puller`
- `crypto-risk` -> `/data/bin/crypto-trader/risk`
- `crypto-trader` -> `/data/bin/crypto-trader/trader`
- `crypto-transaction` -> `/data/bin/crypto-trader/transaction`

每个服务可以配置不同的关键字和环境服务器：

```json
{
  "server_keywords": ["system", "主节点", "main"],
  "servers_by_env": {
    "prod": ["43.106.12.129-量化-主节点"],
    "test": ["203.0.113.10-量化测试"]
  }
}
```
