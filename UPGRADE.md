# OPS 本地升级指南

适用场景：Windows / Linux / macOS 小团队本地部署，尤其是单进程模式。

## 标准升级流程

1. 停止当前 OPS 服务。
2. 备份运行数据目录。

Windows：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\backup_before_upgrade.ps1
```

Linux / macOS：

```bash
bash scripts/backup_before_upgrade.sh
```

3. 解压新版本代码包。建议不要直接覆盖 `data/`、`.env` 和密钥文件。
4. 运行升级前检查。

Windows：

```powershell
py -3 scripts\migrate_check.py
```

Linux / macOS：

```bash
python3 scripts/migrate_check.py
```

5. 启动服务。

Windows 单进程：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_single_process.ps1
```

Linux / macOS 单进程：

```bash
bash scripts/start_single_process.sh
```

6. 打开 `/system/diagnostics` 查看安装诊断。

## 升级时必须保留

- `data/ops.db`
- `data/uploads/`
- `data/packages/`，如果存在
- `data/backups/`
- `data/keys/`
- `.env` 或本地环境变量配置

## 回滚建议

如果升级后无法启动，停止服务，解压升级前备份包，恢复 `data/` 目录，再启动旧版本。
