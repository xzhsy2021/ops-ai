import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TARGET_DB_WRITE_NOTICE = "当前版本支持受控数据库执行：仅允许经过 Dry Run、复核、确认和审计的批量清理或 SQL 执行操作。"

MAX_BATCH_SIZE = 100000

HIGH_RISK_TABLE_PATTERNS = ["orders", "wallets", "statements", "capital_flows", "users", "agents", "merchants", "pay_accounts"]

def _validate_identifier(name: str) -> str:
    import re
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name or ""):
        raise ValueError(f"Invalid identifier: {name}")
    return name

def _extract_table_names(rows: List[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    for row in rows or []:
        if not row:
            continue
        names.append(str(next(iter(row.values()))))
    return names


def calculate_risk_level(
    environment: str,
    matched_rows: int,
    batch_size: int,
    has_index: bool,
    table_name: str,
    max_delete_rows: Optional[int],
    cutoff_days_from_now: int,
) -> str:
    reasons = []
    if (environment or "").lower() in ("prod", "production"):
        reasons.append("production environment")
    if matched_rows > 1000000:
        reasons.append(f"matched_rows={matched_rows} > 1000000")
    if batch_size > 50000:
        reasons.append(f"batch_size={batch_size} > 50000")
    if not has_index:
        reasons.append("no index on date_column")
    for pattern in HIGH_RISK_TABLE_PATTERNS:
        if pattern in (table_name or "").lower():
            reasons.append(f"table matches high-risk pattern: {pattern}")
            break
    if max_delete_rows is None:
        reasons.append("no max_delete_rows limit")
    if cutoff_days_from_now < 30:
        reasons.append(f"cutoff_time within {cutoff_days_from_now} days of now")
    if len(reasons) >= 2:
        return "high"
    if len(reasons) == 1:
        return "medium"
    return "low"


class MySQLExecutor:
    def __init__(self, host: str, port: int, username: str, password: str, database: str, tunnel_config: Optional[Dict[str, Any]] = None):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.database = database
        self.tunnel_config = tunnel_config or None
        self._conn = None
        self._tunnel = None

    def _get_connection(self):
        if self._conn is not None:
            try:
                self._conn.ping(reconnect=True)
                return self._conn
            except Exception:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
        if self._tunnel is not None:
            try:
                self._tunnel.__exit__(None, None, None)
            except Exception:
                pass
            self._tunnel = None
        import pymysql
        connect_host = self.host
        connect_port = self.port
        if self.tunnel_config:
            from app.maintenance.ssh_tunnel import SSHTunnel
            self._tunnel = SSHTunnel(
                ssh_host=self.tunnel_config["ssh_host"],
                ssh_port=self.tunnel_config.get("ssh_port", 22),
                ssh_username=self.tunnel_config["ssh_username"],
                ssh_password=self.tunnel_config.get("ssh_password"),
                ssh_key_path=self.tunnel_config.get("ssh_key_path"),
                ssh_key_passphrase=self.tunnel_config.get("ssh_key_passphrase"),
                ssh_key_content=self.tunnel_config.get("ssh_key_content"),
                target_ssh_host=self.tunnel_config.get("target_ssh_host"),
                target_ssh_port=self.tunnel_config.get("target_ssh_port", 22),
                target_ssh_username=self.tunnel_config.get("target_ssh_username"),
                target_ssh_password=self.tunnel_config.get("target_ssh_password"),
                target_ssh_key_path=self.tunnel_config.get("target_ssh_key_path"),
                target_ssh_key_passphrase=self.tunnel_config.get("target_ssh_key_passphrase"),
                target_ssh_key_content=self.tunnel_config.get("target_ssh_key_content"),
                remote_host=self.tunnel_config.get("remote_bind_host") or self.host,
                remote_port=self.port,
            ).__enter__()
            connect_host = self._tunnel.local_host
            connect_port = self._tunnel.local_port
        self._conn = pymysql.connect(
            host=connect_host,
            port=connect_port,
            user=self.username,
            password=self.password,
            database=self.database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10,
            read_timeout=60,
            write_timeout=60,
        )
        return self._conn

    def close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        if self._tunnel is not None:
            try:
                self._tunnel.__exit__(None, None, None)
            except Exception:
                pass
            self._tunnel = None

    def dry_run(self, table_name: str, date_column: str, cutoff_time: str, batch_size: int) -> Dict[str, Any]:
        _validate_identifier(table_name)
        _validate_identifier(date_column)
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than 0")
        if batch_size > MAX_BATCH_SIZE:
            raise ValueError(f"batch_size cannot exceed {MAX_BATCH_SIZE}")
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) AS cnt FROM `{table_name}` WHERE `{date_column}` < %s",
                (cutoff_time,),
            )
            matched_rows = cur.fetchone()["cnt"]

            cur.execute(
                f"SELECT `{date_column}` FROM `{table_name}` WHERE `{date_column}` < %s ORDER BY `{date_column}` ASC LIMIT 1",
                (cutoff_time,),
            )
            first_row = cur.fetchone()
            first_row_time = first_row[date_column] if first_row else None

            boundary_time = None
            if matched_rows > batch_size:
                cur.execute(
                    f"SELECT `{date_column}` FROM `{table_name}` WHERE `{date_column}` < %s ORDER BY `{date_column}` ASC LIMIT 1 OFFSET %s",
                    (cutoff_time, batch_size),
                )
                boundary = cur.fetchone()
                boundary_time = boundary[date_column] if boundary else None

            cur.execute(f"SHOW INDEX FROM `{table_name}`")
            indexes = cur.fetchall()
            index_names = sorted({str(idx.get("Key_name") or "") for idx in indexes if idx.get("Column_name") == date_column and idx.get("Key_name")})
            has_index = bool(index_names)

        estimated_batches = (matched_rows + batch_size - 1) // batch_size if matched_rows > 0 else 0
        preview_sql = f"SELECT COUNT(*) AS cnt FROM `{table_name}` WHERE `{date_column}` < ?"
        sample_sql = f"SELECT * FROM `{table_name}` WHERE `{date_column}` < ? ORDER BY `{date_column}` ASC LIMIT {min(batch_size, 100)}"
        return {
            "matched_rows": matched_rows,
            "estimated_batches": estimated_batches,
            "first_row_time": str(first_row_time) if first_row_time else None,
            "batch_boundary_time": str(boundary_time) if boundary_time else None,
            "has_index": has_index,
            "index_names": index_names,
            "generated_sql": preview_sql,
            "sample_sql": sample_sql,
            "execution_mode": "controlled_write_preview",
            "write_enabled": True,
            "write_notice": TARGET_DB_WRITE_NOTICE,
        }

    def delete_batch(self, table_name: str, date_column: str, cutoff_time: str, batch_size: int) -> Tuple[int, str]:
        _validate_identifier(table_name)
        _validate_identifier(date_column)
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than 0")
        if batch_size > MAX_BATCH_SIZE:
            raise ValueError(f"batch_size cannot exceed {MAX_BATCH_SIZE}")
        conn = self._get_connection()
        sql = f"DELETE FROM `{table_name}` WHERE `{date_column}` < %s ORDER BY `{date_column}` ASC LIMIT %s"
        with conn.cursor() as cur:
            cur.execute(sql, (cutoff_time, int(batch_size)))
            affected = int(cur.rowcount or 0)
        conn.commit()
        audit_sql = f"DELETE FROM `{table_name}` WHERE `{date_column}` < ? ORDER BY `{date_column}` ASC LIMIT {int(batch_size)}"
        return affected, audit_sql

    def list_tables(self, database_name: str = None) -> List[str]:
        conn = self._get_connection()
        with conn.cursor() as cur:
            if database_name:
                cur.execute(f"USE `{_validate_identifier(database_name)}`")
            cur.execute("SHOW TABLES")
            result = cur.fetchall()
        return _extract_table_names(result)

    def list_columns(self, table_name: str) -> List[Dict[str, Any]]:
        _validate_identifier(table_name)
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute(f"SHOW COLUMNS FROM `{table_name}`")
            return cur.fetchall()


class PostgreSQLExecutor:
    """PostgreSQL query executor with SSH tunnel support."""

    def __init__(self, host: str, port: int, username: str, password: str, database: str, tunnel_config: Optional[Dict[str, Any]] = None):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.database = database
        self.tunnel_config = tunnel_config or None
        self._conn = None
        self._tunnel = None

    def _get_connection(self):
        if self._conn is not None:
            try:
                self._conn.ping(reconnect=True)
                return self._conn
            except Exception:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
        if self._tunnel is not None:
            try:
                self._tunnel.__exit__(None, None, None)
            except Exception:
                pass
            self._tunnel = None
        import psycopg2
        from psycopg2.extras import RealDictCursor
        connect_host = self.host
        connect_port = self.port
        if self.tunnel_config:
            from app.maintenance.ssh_tunnel import SSHTunnel
            self._tunnel = SSHTunnel(
                ssh_host=self.tunnel_config["ssh_host"],
                ssh_port=self.tunnel_config.get("ssh_port", 22),
                ssh_username=self.tunnel_config["ssh_username"],
                ssh_password=self.tunnel_config.get("ssh_password"),
                ssh_key_path=self.tunnel_config.get("ssh_key_path"),
                ssh_key_passphrase=self.tunnel_config.get("ssh_key_passphrase"),
                ssh_key_content=self.tunnel_config.get("ssh_key_content"),
                target_ssh_host=self.tunnel_config.get("target_ssh_host"),
                target_ssh_port=self.tunnel_config.get("target_ssh_port", 22),
                target_ssh_username=self.tunnel_config.get("target_ssh_username"),
                target_ssh_password=self.tunnel_config.get("target_ssh_password"),
                target_ssh_key_path=self.tunnel_config.get("target_ssh_key_path"),
                target_ssh_key_passphrase=self.tunnel_config.get("target_ssh_key_passphrase"),
                target_ssh_key_content=self.tunnel_config.get("target_ssh_key_content"),
                remote_host=self.tunnel_config.get("remote_bind_host") or self.host,
                remote_port=self.port,
            ).__enter__()
            connect_host = self._tunnel.local_host
            connect_port = self._tunnel.local_port
        self._conn = psycopg2.connect(
            host=connect_host,
            port=connect_port,
            user=self.username,
            password=self.password,
            dbname=self.database,
            connect_timeout=10,
            options="-c statement_timeout=60000",
        )
        return self._conn

    def close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        if self._tunnel is not None:
            try:
                self._tunnel.__exit__(None, None, None)
            except Exception:
                pass
            self._tunnel = None

    def dry_run(self, table_name: str, date_column: str, cutoff_time: str, batch_size: int) -> Dict[str, Any]:
        _validate_identifier(table_name)
        _validate_identifier(date_column)
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than 0")
        if batch_size > MAX_BATCH_SIZE:
            raise ValueError(f"batch_size cannot exceed {MAX_BATCH_SIZE}")
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) AS cnt FROM \"{table_name}\" WHERE \"{date_column}\" < %s",
                (cutoff_time,),
            )
            matched_rows = cur.fetchone()["cnt"]

            cur.execute(
                f"SELECT \"{date_column}\" FROM \"{table_name}\" WHERE \"{date_column}\" < %s ORDER BY \"{date_column}\" ASC LIMIT 1",
                (cutoff_time,),
            )
            first_row = cur.fetchone()
            first_row_time = first_row[date_column] if first_row else None

            boundary_time = None
            if matched_rows > batch_size:
                cur.execute(
                    f"SELECT \"{date_column}\" FROM \"{table_name}\" WHERE \"{date_column}\" < %s ORDER BY \"{date_column}\" ASC LIMIT 1 OFFSET %s",
                    (cutoff_time, batch_size),
                )
                boundary = cur.fetchone()
                boundary_time = boundary[date_column] if boundary else None

            cur.execute(f"SELECT indexname FROM pg_indexes WHERE tablename = %s", (table_name,))
            indexes = cur.fetchall()
            index_names = sorted({str(idx.get("indexname") or "") for idx in indexes})
            has_index = bool(index_names)

        estimated_batches = (matched_rows + batch_size - 1) // batch_size if matched_rows > 0 else 0
        preview_sql = f'SELECT COUNT(*) AS cnt FROM "{table_name}" WHERE "{date_column}" < ?'
        sample_sql = f'SELECT * FROM "{table_name}" WHERE "{date_column}" < ? ORDER BY "{date_column}" ASC LIMIT {min(batch_size, 100)}'
        return {
            "matched_rows": matched_rows,
            "estimated_batches": estimated_batches,
            "first_row_time": str(first_row_time) if first_row_time else None,
            "batch_boundary_time": str(boundary_time) if boundary_time else None,
            "has_index": has_index,
            "index_names": index_names,
            "generated_sql": preview_sql,
            "sample_sql": sample_sql,
            "execution_mode": "controlled_write_preview",
            "write_enabled": True,
            "write_notice": TARGET_DB_WRITE_NOTICE,
        }

    def delete_batch(self, table_name: str, date_column: str, cutoff_time: str, batch_size: int) -> Tuple[int, str]:
        _validate_identifier(table_name)
        _validate_identifier(date_column)
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than 0")
        if batch_size > MAX_BATCH_SIZE:
            raise ValueError(f"batch_size cannot exceed {MAX_BATCH_SIZE}")
        conn = self._get_connection()
        sql = f'DELETE FROM "{table_name}" WHERE "{date_column}" < %s ORDER BY "{date_column}" ASC LIMIT %s'
        with conn.cursor() as cur:
            cur.execute(sql, (cutoff_time, int(batch_size)))
            affected = int(cur.rowcount or 0)
        conn.commit()
        audit_sql = f'DELETE FROM "{table_name}" WHERE "{date_column}" < ? ORDER BY "{date_column}" ASC LIMIT {int(batch_size)}'
        return affected, audit_sql

    def list_tables(self, database_name: str = None) -> List[str]:
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
            result = cur.fetchall()
        return [r[0] for r in result]

    def list_columns(self, table_name: str) -> List[Dict[str, Any]]:
        _validate_identifier(table_name)
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = %s AND table_schema = 'public'
                ORDER BY ordinal_position
                """,
                (table_name,),
            )
            rows = cur.fetchall()
            return [{"Field": r[0], "Type": r[1], "Null": r[2]} for r in rows]
