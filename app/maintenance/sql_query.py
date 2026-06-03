import re
import time
from typing import Any, Dict, List, Optional

from app.core.secret_store import decrypt_secret
from app.db.models import DatabaseConnection, SqlQueryHistory
from app.maintenance.executor import MySQLExecutor, PostgreSQLExecutor

READONLY_PREFIXES = ("select", "show", "describe", "desc", "explain", "with")
WRITE_PREFIXES = ("update", "delete", "insert")
FORBIDDEN_PATTERNS = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|replace|grant|revoke|merge|call|lock|unlock|set\s+password|load\s+data|outfile|dumpfile|for\s+update|procedure\s+analyse)\b|\b(sleep|benchmark|load_file)\s*\(",
    re.IGNORECASE,
)
WRITE_FORBIDDEN_PATTERNS = re.compile(
    r"\b(drop|alter|truncate|create|replace|grant|revoke|merge|call|lock|unlock|set\s+password|load\s+data|outfile|dumpfile|attach|detach|pragma|vacuum|procedure\s+analyse)\b|\b(sleep|benchmark|load_file)\s*\(",
    re.IGNORECASE,
)
WRITE_REQUIRES_WHERE = {"update", "delete"}
LINE_COMMENT_RE = re.compile(r"(^|\s)--|#")
BLOCK_COMMENT_RE = re.compile(r"/\*|\*/")
LIMIT_RE = re.compile(r"\blimit\s+(?:(\d+)\s*,\s*)?(\d+)\b", re.IGNORECASE)
DEFAULT_QUERY_TIMEOUT_SECONDS = 30
MAX_QUERY_TIMEOUT_SECONDS = 120
MAX_RETURN_ROWS = 1000


def normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", (sql or "").strip()).rstrip(";")


def infer_query_type(sql: str) -> str:
    norm = normalize_sql(sql).lower()
    return norm.split(" ", 1)[0] if norm else "unknown"


def validate_readonly_sql(sql: str) -> str:
    raw = sql or ""
    if LINE_COMMENT_RE.search(raw) or BLOCK_COMMENT_RE.search(raw):
        raise ValueError("SQL 中不允许包含注释，避免隐藏多语句或高风险片段")
    norm = normalize_sql(raw)
    if not norm:
        raise ValueError("SQL 不能为空")
    if ";" in norm:
        raise ValueError("仅允许单条 SQL 查询")
    head = norm.split(" ", 1)[0].lower()
    if head not in READONLY_PREFIXES:
        raise ValueError("仅允许 SELECT / SHOW / DESCRIBE / EXPLAIN / WITH 查询")
    if FORBIDDEN_PATTERNS.search(norm):
        raise ValueError("检测到写入、锁表、文件读取或长耗时关键字，已拦截")
    return norm


def extract_write_table(sql: str) -> str:
    norm = normalize_sql(sql)
    lowered = norm.lower()
    patterns = [
        r"^update\s+([`\"\[]?)([A-Za-z0-9_.\-]+)\1\s+set\s+",
        r"^delete\s+from\s+([`\"\[]?)([A-Za-z0-9_.\-]+)\1\s+",
        r"^insert\s+into\s+([`\"\[]?)([A-Za-z0-9_.\-]+)\1\s*",
    ]
    for pattern in patterns:
        match = re.search(pattern, norm, re.IGNORECASE)
        if match:
            return match.group(2).split(".")[-1].strip('`"[]')
    return ""


def _where_clause_for_estimate(sql: str) -> str:
    norm = normalize_sql(sql)
    match = re.search(r"\bwhere\b(.+)$", norm, re.IGNORECASE)
    if not match:
        return ""
    where = match.group(1).strip()
    where = re.split(r"\border\s+by\b|\blimit\b", where, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    return where


def summarize_write_where(sql: str, max_len: int = 600) -> str:
    where = _where_clause_for_estimate(sql)
    return mask_sql_for_history(where, max_len=max_len) if where else ""


def extract_update_columns(sql: str) -> List[str]:
    norm = normalize_sql(sql)
    if infer_query_type(norm) != "update":
        return []
    match = re.search(r"\bset\b(.+?)\bwhere\b", norm, re.IGNORECASE)
    if not match:
        return []
    columns: List[str] = []
    for part in match.group(1).split(","):
        left = part.split("=", 1)[0].strip().strip('`"[]')
        if left:
            columns.append(left.split(".")[-1])
    return columns[:50]


def estimate_write_count_sql(sql: str) -> Optional[str]:
    norm = normalize_sql(sql)
    qtype = infer_query_type(norm)
    if qtype not in {"update", "delete"}:
        return None
    table = extract_write_table(norm)
    where = _where_clause_for_estimate(norm)
    if not table or not where:
        return None
    return f"SELECT COUNT(*) AS cnt FROM {table} WHERE {where}"


def validate_write_sql(sql: str) -> str:
    raw = sql or ""
    if LINE_COMMENT_RE.search(raw) or BLOCK_COMMENT_RE.search(raw):
        raise ValueError("SQL 中不允许包含注释，避免隐藏多语句或高风险片段")
    norm = normalize_sql(raw)
    if not norm:
        raise ValueError("SQL 不能为空")
    if ";" in norm:
        raise ValueError("仅允许单条 SQL 执行")
    head = infer_query_type(norm)
    if head not in WRITE_PREFIXES:
        raise ValueError("执行操作仅允许 UPDATE / DELETE / INSERT；查询请使用只读查询入口")
    if WRITE_FORBIDDEN_PATTERNS.search(norm):
        raise ValueError("检测到 DDL、权限变更、锁表、文件读取或长耗时关键字，已拦截")
    lowered = f" {norm.lower()} "
    if head in WRITE_REQUIRES_WHERE and " where " not in lowered:
        raise ValueError("UPDATE / DELETE 必须包含 WHERE 条件")
    if head in WRITE_REQUIRES_WHERE and re.search(r"\bwhere\s+(1\s*=\s*1|true)\b", lowered, re.IGNORECASE):
        raise ValueError("WHERE 条件过宽，禁止使用 1=1 / TRUE 作为唯一条件")
    if not extract_write_table(norm):
        raise ValueError("无法识别目标表，请使用标准 UPDATE table SET ... WHERE ... / DELETE FROM table WHERE ... / INSERT INTO table ... 语法")
    return norm


def analyze_write_sql_guard(sql: str, environment: str = "", *, max_affected_rows: int = 1000) -> Dict[str, Any]:
    raw = sql or ""
    norm = normalize_sql(raw)
    qtype = infer_query_type(norm)
    table = extract_write_table(norm)
    env = (environment or "").lower()
    is_prod = env in {"prod", "production", "online", "release", "live", "线上", "生产"}
    warnings: List[str] = []
    blockers: List[str] = []
    suggestions: List[str] = []
    if not norm:
        blockers.append("SQL 不能为空")
    if LINE_COMMENT_RE.search(raw) or BLOCK_COMMENT_RE.search(raw):
        blockers.append("SQL 中不允许包含注释，避免隐藏多语句或高风险片段")
    if ";" in norm:
        blockers.append("仅允许单条 SQL")
    if qtype not in WRITE_PREFIXES:
        blockers.append("执行操作仅允许 UPDATE / DELETE / INSERT")
    if WRITE_FORBIDDEN_PATTERNS.search(norm):
        blockers.append("检测到 DDL、权限变更、锁表、文件读取或长耗时关键字")
    if qtype in WRITE_REQUIRES_WHERE and " where " not in f" {norm.lower()} ":
        blockers.append("UPDATE / DELETE 必须包含 WHERE 条件")
    if qtype in WRITE_REQUIRES_WHERE and re.search(r"\bwhere\s+(1\s*=\s*1|true)\b", norm, re.IGNORECASE):
        blockers.append("WHERE 条件过宽，禁止使用 1=1 / TRUE")
    if not table and qtype in WRITE_PREFIXES:
        blockers.append("无法识别目标表")
    if is_prod:
        warnings.append("生产连接写操作风险较高，请优先在低峰期执行，并确认已有备份或回滚方案")
    if qtype in {"update", "delete"}:
        warnings.append("建议先执行预览，核对预计影响行数后再执行")
    if max_affected_rows:
        suggestions.append(f"本次执行将受最大影响行数 {max_affected_rows} 保护，超过阈值会回滚")
    status = "blocked" if blockers else "warning" if warnings else "passed"
    return {
        "query_type": qtype,
        "status": status,
        "write": status != "blocked" and qtype in WRITE_PREFIXES,
        "is_production": is_prod,
        "table_name": table,
        "blockers": blockers,
        "warnings": warnings,
        "suggestions": suggestions,
        "normalized_sql": norm,
        "history_sql": mask_sql_for_history(norm),
        "risk_level": "high" if qtype in {"update", "delete"} else "medium",
        "confirm_text": "EXECUTE SQL",
        "protections": [
            "仅允许单条 UPDATE / DELETE / INSERT",
            "UPDATE / DELETE 必须包含 WHERE 条件",
            "禁止 DDL、权限变更、锁表、文件读取与长耗时函数",
            "执行时记录审计并受最大影响行数保护",
        ],
    }


def _clamp_limit_match(match: re.Match, limit: int) -> str:
    offset = match.group(1)
    row_count = int(match.group(2))
    safe_count = min(max(row_count, 1), limit)
    if offset is not None:
        return f"LIMIT {int(offset)}, {safe_count}"
    return f"LIMIT {safe_count}"


def add_limit_if_needed(sql: str, limit: int) -> str:
    norm = normalize_sql(sql)
    limit = max(1, min(int(limit or 100), MAX_RETURN_ROWS))
    if infer_query_type(norm) not in ("select", "with"):
        return norm
    if LIMIT_RE.search(norm):
        return LIMIT_RE.sub(lambda m: _clamp_limit_match(m, limit), norm, count=1)
    return f"{norm} LIMIT {limit}"


def mask_sql_for_history(sql: str, max_len: int = 4000) -> str:
    """Keep SQL history useful while reducing the chance of leaking secrets."""
    text = normalize_sql(sql)
    text = re.sub(r"'[^']{24,}'", "'***'", text)
    text = re.sub(r'"[^"]{24,}"', '"***"', text)
    text = re.sub(r"\b(password|passwd|pwd|secret|token|api_key|apikey)\s*=\s*('[^']*'|\"[^\"]*\"|\S+)", r"\1=***", text, flags=re.IGNORECASE)
    return text[:max_len]


def analyze_sql_guard(sql: str, environment: str = "") -> Dict[str, Any]:
    raw = sql or ""
    norm = normalize_sql(raw)
    qtype = infer_query_type(norm)
    env = (environment or "").lower()
    is_prod = env in {"prod", "production", "线上", "生产"}
    warnings: List[str] = []
    blockers: List[str] = []
    suggestions: List[str] = []

    if not norm:
        blockers.append("SQL 不能为空")
    if LINE_COMMENT_RE.search(raw) or BLOCK_COMMENT_RE.search(raw):
        blockers.append("SQL 中不允许包含注释，避免隐藏多语句或高风险片段")
    if ";" in norm:
        blockers.append("仅允许单条 SQL")
    if qtype not in READONLY_PREFIXES:
        blockers.append("当前入口仅允许只读 SQL；UPDATE / DELETE / INSERT 请切换到数据库工作台的 SQL 执行入口")
    if FORBIDDEN_PATTERNS.search(norm):
        blockers.append("检测到写入、锁表、文件读取或长耗时关键字")
    if qtype in ("select", "with") and not LIMIT_RE.search(norm):
        warnings.append("SELECT/WITH 未包含 LIMIT，执行时会自动追加行数限制")
    if is_prod:
        warnings.append("生产连接查询请确认不会造成大范围扫描；写操作请走 SQL 执行预检和确认")
    if qtype in {"delete", "update"} and " where " not in f" {norm.lower()} ":
        blockers.append("DELETE / UPDATE 必须包含 WHERE 条件")
    if qtype in {"delete", "update", "insert", "replace", "truncate", "drop", "alter"}:
        suggestions.append("写操作请切换到 SQL 执行入口，并先完成预检、影响行数保护和一键确认")
    if qtype in ("select", "with"):
        suggestions.append("大表查询建议带上索引字段条件，并限制返回行数")

    status = "blocked" if blockers else "warning" if warnings else "passed"
    return {
        "query_type": qtype,
        "status": status,
        "readonly": status != "blocked" and qtype in READONLY_PREFIXES,
        "is_production": is_prod,
        "blockers": blockers,
        "warnings": warnings,
        "suggestions": suggestions,
        "normalized_sql": norm,
        "history_sql": mask_sql_for_history(norm),
        "protections": [
            "只允许单条只读 SQL",
            "SELECT/WITH 自动限制返回行数",
            "禁止注释、多语句、锁表、文件读取与长耗时函数",
            "SQL 历史自动做基础脱敏",
        ],
    }


class SqlQueryService:
    def __init__(self, db):
        self.db = db

    def _get_connection(self, connection_id: str) -> DatabaseConnection:
        conn = self.db.query(DatabaseConnection).filter(DatabaseConnection.id == connection_id).first()
        if not conn:
            raise ValueError("Connection not found")
        db_type = (conn.db_type or "mysql").lower()
        if db_type not in ("mysql", "postgresql", "postgres"):
            raise ValueError(f"当前不支持 {db_type} 数据库查询")
        return conn

    def _load_inventory_server(self, server_name: Optional[str], role: str) -> Optional[Dict[str, Any]]:
        if not server_name:
            return None
        try:
            from app.maintenance.server_assets import get_server_asset
            selected_server = get_server_asset(server_name, self.db)
        except Exception:
            selected_server = None
        if not selected_server:
            raise ValueError(f"{role} server '{server_name}' not found in server inventory")
        return selected_server

    def _server_auth(self, selected_server: Optional[Dict[str, Any]], key_path_override: Optional[str]) -> Dict[str, Any]:
        server = selected_server or {}
        return {
            "password": server.get("password"),
            "key_path": key_path_override or server.get("key") or server.get("key_file"),
            "key_content": server.get("key_content") if not key_path_override else None,
        }

    def _build_tunnel_config(self, conn: DatabaseConnection) -> Optional[Dict[str, Any]]:
        if not getattr(conn, "use_ssh_tunnel", False):
            return None

        ssh_mode = getattr(conn, "ssh_mode", None) or "manual"
        bastion_name = getattr(conn, "ssh_server_name", None)
        bastion_server = None
        bastion_auth = {"password": None, "key_path": None, "key_content": None}

        if ssh_mode == "server" and bastion_name:
            bastion_server = self._load_inventory_server(bastion_name, "SSH bastion")
            bastion_auth = self._server_auth(bastion_server, conn.ssh_key_path)

        ssh_host = conn.ssh_host or (bastion_server or {}).get("host")
        ssh_port = conn.ssh_port or (bastion_server or {}).get("port") or 22
        ssh_username = conn.ssh_username or (bastion_server or {}).get("user") or (bastion_server or {}).get("username")
        ssh_password = decrypt_secret(conn.ssh_password_encrypted) if conn.ssh_password_encrypted else bastion_auth.get("password")
        ssh_key_path = bastion_auth.get("key_path") or conn.ssh_key_path
        ssh_key_content = bastion_auth.get("key_content")
        if conn.ssh_key_content_encrypted:
            ssh_key_content = decrypt_secret(conn.ssh_key_content_encrypted)

        if not ssh_host or not ssh_username:
            raise ValueError("SSH tunnel is enabled but bastion ssh_host or ssh_username is missing")

        target_name = getattr(conn, "ssh_target_server_name", None)
        target_server = self._load_inventory_server(target_name, "SSH target") if target_name else None
        target_key_override = getattr(conn, "ssh_target_key_path", None)
        target_auth = self._server_auth(target_server, target_key_override)
        target_host = getattr(conn, "ssh_target_host", None) or (target_server or {}).get("host")
        target_port = getattr(conn, "ssh_target_port", None) or (target_server or {}).get("port") or 22
        target_username = getattr(conn, "ssh_target_username", None) or (target_server or {}).get("user") or (target_server or {}).get("username")
        target_password_encrypted = getattr(conn, "ssh_target_password_encrypted", None)
        target_password = decrypt_secret(target_password_encrypted) if target_password_encrypted else target_auth.get("password")

        if (target_name or target_host or target_username) and (not target_host or not target_username):
            raise ValueError("Two-hop SSH is enabled but target host or username is missing")

        return {
            "ssh_server_name": bastion_name,
            "ssh_host": ssh_host,
            "ssh_port": ssh_port,
            "ssh_username": ssh_username,
            "ssh_password": ssh_password,
            "ssh_key_path": ssh_key_path,
            "ssh_key_content": ssh_key_content,
            "ssh_key_passphrase": decrypt_secret(conn.ssh_key_passphrase_encrypted) if conn.ssh_key_passphrase_encrypted else None,
            "target_ssh_server_name": target_name,
            "target_ssh_host": target_host,
            "target_ssh_port": target_port,
            "target_ssh_username": target_username,
            "target_ssh_password": target_password,
            "target_ssh_key_path": target_auth.get("key_path"),
            "target_ssh_key_content": target_auth.get("key_content"),
            "target_ssh_key_passphrase": decrypt_secret(conn.ssh_target_key_passphrase_encrypted) if getattr(conn, "ssh_target_key_passphrase_encrypted", None) else None,
            # Database address is resolved from the final execution host. In two-hop mode this is the target host view.
            "remote_bind_host": conn.ssh_remote_bind_host or conn.host,
        }

    def _executor(self, conn: DatabaseConnection, database_name: Optional[str] = None):
        db_type = (conn.db_type or "mysql").lower()
        common_kwargs = dict(
            host=conn.host,
            port=conn.port,
            username=conn.username,
            password=decrypt_secret(conn.password_encrypted),
            database=database_name or conn.database_name,
            tunnel_config=self._build_tunnel_config(conn),
        )
        if db_type in ("postgresql", "postgres"):
            return PostgreSQLExecutor(**common_kwargs)
        return MySQLExecutor(**common_kwargs)

    def preview(self, connection_id: str, sql: str, limit: int = 100, timeout_seconds: int = DEFAULT_QUERY_TIMEOUT_SECONDS) -> Dict[str, Any]:
        conn = self._get_connection(connection_id)
        guard = analyze_sql_guard(sql, getattr(conn, "environment", ""))
        if guard.get("blockers"):
            raise ValueError("; ".join(guard["blockers"]))
        normalized = validate_readonly_sql(sql)
        limit = max(1, min(int(limit or 100), MAX_RETURN_ROWS))
        timeout_seconds = max(1, min(int(timeout_seconds or DEFAULT_QUERY_TIMEOUT_SECONDS), MAX_QUERY_TIMEOUT_SECONDS))
        executable = add_limit_if_needed(normalized, limit)
        warnings = []
        if executable != normalized:
            if LIMIT_RE.search(normalized):
                warnings.append(f"检测到 LIMIT，执行时会将返回行数限制在 {limit} 行以内")
            else:
                warnings.append(f"未检测到 LIMIT，执行时将自动追加 LIMIT {limit}")
        if infer_query_type(normalized) in ("show", "describe", "desc", "explain"):
            warnings.append("该语句为元数据/执行计划查询，不会追加 LIMIT")
        return {
            "query_type": infer_query_type(normalized),
            "readonly": True,
            "limit": limit,
            "timeout_seconds": timeout_seconds,
            "max_return_rows": MAX_RETURN_ROWS,
            "normalized_sql": normalized,
            "executable_sql": executable,
            "history_sql": mask_sql_for_history(normalized),
            "warnings": warnings,
            "risk": guard,
            "guard_status": guard.get("status"),
            "blockers": guard.get("blockers", []),
            "suggestions": guard.get("suggestions", []),
            "protections": [
                "只允许单条只读 SQL",
                f"SELECT/WITH 最大返回 {limit} 行",
                f"查询超时上限 {timeout_seconds} 秒",
                "SQL 历史会做基础脱敏",
            ],
        }

    def execute(self, connection_id: str, sql: str, operator: str, database_name: Optional[str] = None, limit: int = 100, timeout_seconds: int = DEFAULT_QUERY_TIMEOUT_SECONDS) -> Dict[str, Any]:
        conn = self._get_connection(connection_id)
        preview = self.preview(connection_id, sql, limit, timeout_seconds)
        executor = self._executor(conn, database_name or conn.database_name)
        started = time.time()
        status = "success"
        rows: List[Dict[str, Any]] = []
        columns: List[str] = []
        err = None
        try:
            raw_conn = executor._get_connection()
            db_type = (conn.db_type or "mysql").lower()
            with raw_conn.cursor() as cur:
                if db_type == "mysql":
                    max_ms = int(preview["timeout_seconds"]) * 1000
                    try:
                        cur.execute("SET SESSION MAX_EXECUTION_TIME=%s", (max_ms,))
                    except Exception:
                        # MariaDB or older MySQL may not support MAX_EXECUTION_TIME.
                        pass
                    try:
                        cur.execute("SET SESSION TRANSACTION READ ONLY")
                    except Exception:
                        pass
                cur.execute(preview["executable_sql"])
                fetched = cur.fetchall() or []
                if cur.description:
                    columns = [d[0] for d in cur.description]
                    rows = [dict(zip(columns, r)) for r in fetched]
                else:
                    rows = []
        except Exception as e:
            status = "failed"
            err = str(e)
            raise
        finally:
            duration_ms = int((time.time() - started) * 1000)
            self.db.add(SqlQueryHistory(
                connection_id=connection_id,
                connection_name=conn.name,
                database_name=database_name or conn.database_name,
                sql_text=preview.get("history_sql") or mask_sql_for_history(sql),
                normalized_sql=preview.get("normalized_sql"),
                query_type=preview.get("query_type"),
                status=status,
                row_count=len(rows),
                duration_ms=duration_ms,
                error_message=err,
                executed_by=operator,
            ))
            self.db.commit()
            executor.close()
        return {
            **preview,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "duration_ms": duration_ms,
            "connection_name": conn.name,
            "database_name": database_name or conn.database_name,
        }

    def history(self, connection_id: Optional[str] = None, limit: int = 50, offset: int = 0, status: Optional[str] = None, query_type: Optional[str] = None) -> Dict[str, Any]:
        limit = max(1, min(int(limit or 50), 200))
        offset = max(0, int(offset or 0))
        q = self.db.query(SqlQueryHistory)
        if connection_id:
            q = q.filter(SqlQueryHistory.connection_id == connection_id)
        if status:
            q = q.filter(SqlQueryHistory.status == status)
        if query_type:
            q = q.filter(SqlQueryHistory.query_type == query_type)
        total = q.count()
        items = q.order_by(SqlQueryHistory.created_at.desc()).offset(offset).limit(limit).all()
        return {"items": items, "pagination": {"limit": limit, "offset": offset, "total": total, "has_more": offset + len(items) < total}}

class SavedSqlService:
    def __init__(self, db):
        self.db = db

    def _model(self):
        from app.db.models import SavedSql
        return SavedSql

    def list(self, connection_id: Optional[str] = None, category: Optional[str] = None, keyword: Optional[str] = None, limit: int = 200):
        SavedSql = self._model()
        q = self.db.query(SavedSql).order_by(SavedSql.updated_at.desc())
        if connection_id:
            q = q.filter((SavedSql.connection_id == connection_id) | (SavedSql.connection_id.is_(None)))
        if category:
            q = q.filter(SavedSql.category == category)
        if keyword:
            like = f"%{keyword}%"
            q = q.filter((SavedSql.name.like(like)) | (SavedSql.sql_text.like(like)) | (SavedSql.description.like(like)))
        return q.limit(max(1, min(int(limit or 200), 500))).all()

    def create(self, data: Dict[str, Any], operator: str):
        SavedSql = self._model()
        sql = validate_readonly_sql(data.get("sql_text") or data.get("sql") or "")
        item = SavedSql(
            name=data.get("name") or "未命名 SQL",
            category=data.get("category") or "default",
            description=data.get("description"),
            connection_id=data.get("connection_id") or None,
            database_name=data.get("database_name") or None,
            sql_text=sql,
            created_by=operator,
            updated_by=operator,
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        return item

    def update(self, item_id: str, data: Dict[str, Any], operator: str):
        SavedSql = self._model()
        item = self.db.query(SavedSql).filter(SavedSql.id == item_id).first()
        if not item:
            raise ValueError("Saved SQL not found")
        for field in ["name", "category", "description", "connection_id", "database_name"]:
            if field in data:
                value = data.get(field)
                if field in ("connection_id", "database_name") and not value:
                    value = None
                setattr(item, field, value)
        if "sql_text" in data or "sql" in data:
            item.sql_text = validate_readonly_sql(data.get("sql_text") or data.get("sql") or "")
        item.updated_by = operator
        self.db.commit()
        self.db.refresh(item)
        return item

    def delete(self, item_id: str) -> bool:
        SavedSql = self._model()
        item = self.db.query(SavedSql).filter(SavedSql.id == item_id).first()
        if not item:
            return False
        self.db.delete(item)
        self.db.commit()
        return True
