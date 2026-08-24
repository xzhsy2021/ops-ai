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

# ── Matrix bot integration (Matrix 部署包对接) ──
# 专用 Bot / Service 账号 access token（推荐），用于拉房间事件与下载媒体。
MATRIX_HOMESERVER_URL = os.getenv("MATRIX_HOMESERVER_URL", "").strip().rstrip("/")
MATRIX_ACCESS_TOKEN = os.getenv("MATRIX_ACCESS_TOKEN", "").strip()
# 拉取媒体事件时的回看窗口（分钟），默认 15 分钟
MATRIX_MEDIA_WINDOW_MINUTES = int(os.getenv("MATRIX_MEDIA_WINDOW_MINUTES", "15"))
# Matrix 请求超时（秒）
MATRIX_HTTP_TIMEOUT = float(os.getenv("MATRIX_HTTP_TIMEOUT", "30"))
# 允许匹配的 msgtype 列表（逗号分隔）
MATRIX_MEDIA_MSGTYPES = [
    x.strip() for x in os.getenv("MATRIX_MEDIA_MSGTYPES", "m.file,m.image,m.video,m.audio").split(",") if x.strip()
]

# ── Matrix E2EE（加密房间 / 加密媒体解密）──
# 接入 Matrix E2EE SDK：matrix-nio[e2e]（vodozemac 加密后端）。
# - MATRIX_CRYPTO_STORE_PATH 为持久化 E2EE crypto store（SQLite）：
#   保存 Bot 设备的 Olm 账号密钥与 Megolm 入站会话密钥，跨进程/重启保留，
#   缺失历史 room key 时无法解密旧消息（to-device 密钥只投递一次）。
# - MATRIX_DEVICE_ID 必须稳定：更换 device id 会生成全新设备密钥，
#   其他成员客户端会看到"新设备"并可能拒绝自动分发 room key。
# - MATRIX_USER_ID 可留空：首次使用时通过 /account/whoami 自动解析。
# - MATRIX_E2EE_ENABLED 默认开启；未安装 matrix-nio[e2e] 时自动降级为不可用。
MATRIX_E2EE_ENABLED = os.getenv("MATRIX_E2EE_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
MATRIX_USER_ID = os.getenv("MATRIX_USER_ID", "").strip()
MATRIX_DEVICE_ID = os.getenv("MATRIX_DEVICE_ID", "").strip() or "OPS-AI-BOT"
# crypto store 目录（nio SqliteStore 在其下创建 <user_id>_<device_id>.db），
# 默认 <APP_DATA_DIR>/matrix/crypto_store/
MATRIX_CRYPTO_STORE_PATH = get_runtime_path(
    "MATRIX_CRYPTO_STORE_PATH", os.path.join("matrix", "crypto_store")
)
# 解密前增量 sync 的超时（毫秒）：太短可能收不到 pending 的 m.room_key
MATRIX_E2EE_SYNC_TIMEOUT_MS = int(os.getenv("MATRIX_E2EE_SYNC_TIMEOUT_MS", "8000"))


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
        # Matrix E2EE crypto store 目录（<APP_DATA_DIR>/matrix/crypto_store/）
        MATRIX_CRYPTO_STORE_PATH,
    ):
        os.makedirs(path, exist_ok=True)
