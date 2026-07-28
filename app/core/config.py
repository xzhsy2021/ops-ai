import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_app_data_dir() -> str:
    return os.path.abspath(os.getenv("APP_DATA_DIR", os.path.join(ROOT_DIR, "data")))


def _runtime_path(env_name: str, default_name: str) -> str:
    configured = os.getenv(env_name)
    if configured:
        return os.path.abspath(configured)
    return os.path.join(get_app_data_dir(), default_name)


def get_runtime_path(env_name: str, default_name: str) -> str:
    return _runtime_path(env_name, default_name)


def get_database_path() -> str:
    return os.path.abspath(os.getenv("OPS_DB_PATH", os.path.join(get_app_data_dir(), "ops.db")))


APP_DATA_DIR = get_app_data_dir()
UPLOAD_DIR = _runtime_path("UPLOAD_DIR", "uploads")
KEYS_DIR = _runtime_path("KEYS_DIR", "keys")
BACKUP_DIR = _runtime_path("BACKUP_DIR", "backups")
LOG_DIR = _runtime_path("LOG_DIR", "logs")
RUNTIME_DIR = _runtime_path("RUNTIME_DIR", "runtime")
REPORT_DIR = _runtime_path("REPORT_DIR", "reports")
DATABASE_PATH = get_database_path()

# ── Element Approval integration (通用 claw 接入) ──
# APPROVAL_STAGING_DIR 为通用配置名，QCLAW_STAGING_DIR 保留向后兼容。
_approval_staging = os.getenv("APPROVAL_STAGING_DIR") or os.getenv("QCLAW_STAGING_DIR")
APPROVAL_STAGING_DIR = os.path.abspath(_approval_staging) if _approval_staging else os.path.join(get_app_data_dir(), "claw-staging")
QCLAW_STAGING_DIR = APPROVAL_STAGING_DIR  # 向后兼容别名
APPROVAL_SIGNING_KEY = os.getenv("APPROVAL_SIGNING_KEY") or os.getenv("QCLAW_APPROVAL_SIGNING_KEY", "")
QCLAW_APPROVAL_SIGNING_KEY = APPROVAL_SIGNING_KEY  # 向后兼容别名
APPROVAL_TTL_SECONDS = int(os.getenv("APPROVAL_TTL_SECONDS") or os.getenv("QCLAW_APPROVAL_TTL_SECONDS", "900"))
QCLAW_APPROVAL_TTL_SECONDS = APPROVAL_TTL_SECONDS  # 向后兼容别名


def get_initial_admin_credentials_path() -> str:
    """Return the runtime-scoped one-time bootstrap credential file path.

    Keeping this under APP_DATA_DIR makes local installs easier to back up,
    migrate and protect. A legacy root-level file is no longer created.
    """
    return os.path.join(get_app_data_dir(), "initial_admin_password")

MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100MB
ALLOWED_EXTENSIONS = {".tar.gz", ".tgz", ".tar", ".gz", ".zip"}


def ensure_runtime_dirs() -> None:
    for path in (
        get_app_data_dir(),
        _runtime_path("UPLOAD_DIR", "uploads"),
        _runtime_path("KEYS_DIR", "keys"),
        _runtime_path("BACKUP_DIR", "backups"),
        _runtime_path("LOG_DIR", "logs"),
        _runtime_path("RUNTIME_DIR", "runtime"),
        _runtime_path("REPORT_DIR", "reports"),
        APPROVAL_STAGING_DIR,
    ):
        os.makedirs(path, exist_ok=True)
