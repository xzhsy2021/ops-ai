#!/usr/bin/env python3
"""
从 DBeaver 配置中读取数据库连接信息，导入到 ops-ai 系统的 database_connections 表中。

DBeaver Windows 配置路径：
  %APPDATA%\\DBeaverData\\workspace6\\General\\.dbeaver\\data-sources.json
  %APPDATA%\\DBeaverData\\workspace6\\General\\.dbeaver\\credentials-config.json

使用方式：
  python scripts/import_dbeaver_connections.py [--env ENV] [--dry-run]
"""

import argparse
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

# 将项目根目录加入路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _get_dbeaver_config_dir() -> Path:
    """获取 DBeaver 配置目录。"""
    appdata = os.environ.get("APPDATA")
    if appdata:
        path = Path(appdata) / "DBeaverData" / "workspace6" / "General" / ".dbeaver"
        if path.exists():
            return path
    # 尝试 DBeaver CE (Eclipse 版)
    home = Path.home()
    candidates = [
        home / ".dbeaver4" / "General" / ".dbeaver",
        home / ".dbeaver" / "General" / ".dbeaver",
        home / "AppData" / "Roaming" / "DBeaverData" / "workspace6" / "General" / ".dbeaver",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError("未找到 DBeaver 配置目录，请确认 DBeaver 已安装并配置过数据库连接")


def _parse_dbeaver_driver(driver_id: str) -> tuple:
    """根据 DBeaver driver ID 推断 db_type 和默认端口。"""
    driver_lower = driver_id.lower()
    if "mysql" in driver_lower:
        return "mysql", 3306
    if "postgresql" in driver_lower or "postgres" in driver_lower:
        return "postgresql", 5432
    if "oracle" in driver_lower:
        return "oracle", 1521
    if "sqlserver" in driver_lower or "mssql" in driver_lower:
        return "sqlserver", 1433
    if "sqlite" in driver_lower:
        return "sqlite", 0
    if "mongodb" in driver_lower:
        return "mongodb", 27017
    if "redis" in driver_lower:
        return "redis", 6379
    if "mariadb" in driver_lower:
        return "mysql", 3306
    return "mysql", 3306


def _read_data_sources(config_dir: Path) -> Dict[str, Any]:
    """读取 data-sources.json。"""
    ds_path = config_dir / "data-sources.json"
    if not ds_path.exists():
        raise FileNotFoundError(f"未找到数据源配置文件: {ds_path}")
    with open(ds_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_credentials(config_dir: Path) -> Dict[str, Any]:
    """读取 credentials-config.json（如果存在）。DBeaver 的 credentials-config.json 是二进制加密文件，跳过。"""
    cred_path = config_dir / "credentials-config.json"
    if not cred_path.exists():
        return {}
    # DBeaver 的 credentials-config.json 是加密的二进制文件，无法直接读取
    # 用户名/密码需要从 data-sources.json 中的配置或手动输入
    logger.warning("DBeaver 的 credentials-config.json 是加密文件，无法自动读取密码。导入后请手动在 ops 系统中补充密码。")
    return {}


def _extract_connections(data_sources: Dict[str, Any], credentials: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从 DBeaver 配置中提取连接信息。"""
    connections: List[Dict[str, Any]] = []
    sources = data_sources.get("connections", {})

    for conn_id, cfg in sources.items():
        provider = cfg.get("provider", "")
        driver = cfg.get("driver", "")
        name = cfg.get("name", conn_id)

        # 跳过文件夹/虚拟连接
        if provider == "org.jkiss.dbeaver.ext.generic" and not driver:
            continue

        db_type, default_port = _parse_dbeaver_driver(driver)

        # 提取连接参数
        host = ""
        port = default_port
        database = ""
        username = ""
        password = ""

        # 新版 DBeaver 配置结构
        if "configuration" in cfg:
            conf = cfg["configuration"]
            host = conf.get("host", "")
            port = conf.get("port", default_port)
            database = conf.get("database", "")
            if not database:
                database = conf.get("url", "").split("/")[-1].split("?")[0]
            # 从 credentials-config.json 获取凭据
            cred = credentials.get(conn_id, {})
            username = cred.get("#connection", {}).get("user", "")
            password = cred.get("#connection", {}).get("password", "")
        else:
            # 旧版或通用驱动
            host = cfg.get("host", "")
            port = cfg.get("port", default_port)
            database = cfg.get("database", "")
            username = cfg.get("user", "")

        # 跳过无效连接
        if not host and not cfg.get("url", ""):
            logger.warning(f"跳过连接 '{name}'：缺少主机信息")
            continue

        # 从 URL 解析（如果 host 为空）
        url = cfg.get("url", "")
        if not host and url:
            # 简单解析 jdbc:mysql://host:port/db
            try:
                from urllib.parse import urlparse
                parsed = urlparse(url.replace("jdbc:", ""))
                host = parsed.hostname or ""
                port = parsed.port or default_port
                database = parsed.path.lstrip("/").split("?")[0] if parsed.path else ""
            except Exception:
                pass

        connections.append({
            "name": name,
            "db_type": db_type,
            "host": host or "localhost",
            "port": int(port) if port else default_port,
            "database_name": database,
            "username": username,
            "password": password,
            "description": f"从 DBeaver 导入: {driver}",
        })

    return connections


def _import_to_ops(connections: List[Dict[str, Any]], environment: str, dry_run: bool = False) -> None:
    """将连接信息导入 ops 数据库。"""
    from app.db.base import SessionLocal
    from app.maintenance.service import CleanupService
    from app.core.secret_store import encrypt_secret

    db = SessionLocal()
    try:
        svc = CleanupService(db)
        existing = {c.name for c in svc.list_connections()}

        imported = 0
        skipped = 0

        for conn in connections:
            name = conn["name"]
            if name in existing:
                logger.info(f"跳过已存在: {name}")
                skipped += 1
                continue

            data = {
                "name": name,
                "environment": environment,
                "db_type": conn["db_type"],
                "host": conn["host"],
                "port": conn["port"],
                "username": conn["username"],
                "password": conn["password"],
                "database_name": conn["database_name"],
                "description": conn["description"],
            }

            if dry_run:
                logger.info(f"[DRY-RUN] 将导入: {name} ({conn['db_type']}://{conn['host']}:{conn['port']})")
                imported += 1
                continue

            try:
                svc.create_connection(data, created_by="dbeaver_import")
                logger.info(f"已导入: {name}")
                imported += 1
            except Exception as e:
                logger.error(f"导入失败 {name}: {e}")

        logger.info(f"导入完成: 成功 {imported}, 跳过 {skipped}, 总计 {len(connections)}")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="从 DBeaver 导入数据库连接到 ops 系统")
    parser.add_argument("--env", default="development", help="目标环境 (默认: development)")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不实际写入数据库")
    parser.add_argument("--config-dir", type=str, help="手动指定 DBeaver 配置目录")
    args = parser.parse_args()

    try:
        if args.config_dir:
            config_dir = Path(args.config_dir)
        else:
            config_dir = _get_dbeaver_config_dir()

        logger.info(f"DBeaver 配置目录: {config_dir}")

        data_sources = _read_data_sources(config_dir)
        credentials = _read_credentials(config_dir)
        connections = _extract_connections(data_sources, credentials)

        if not connections:
            logger.warning("未找到有效的数据库连接配置")
            return

        logger.info(f"发现 {len(connections)} 个数据库连接")
        for c in connections:
            logger.info(f"  - {c['name']}: {c['db_type']}://{c['host']}:{c['port']}/{c['database_name']}")

        _import_to_ops(connections, args.env, args.dry_run)

    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)
    except Exception as e:
        logger.error(f"执行失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
