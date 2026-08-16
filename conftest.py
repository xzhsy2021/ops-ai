"""pytest 全局隔离：让测试套件使用独立临时数据库，避免污染共享开发库 data/ops.db。

必须在导入任何 app 模块之前设置 DATABASE_URL，否则 app/db/base 的 engine 会指向
开发库。pytest 在收集测试模块之前先加载本 conftest，因此顶层设置环境变量即可生效。
"""
import os
import tempfile
from pathlib import Path

_TEST_DB_DIR = Path(tempfile.gettempdir()) / "opsi_test_dbs"
_TEST_DB_DIR.mkdir(parents=True, exist_ok=True)
_TEST_DB_PATH = _TEST_DB_DIR / f"test_{os.getpid()}.db"

os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _isolated_test_db():
    """在独立临时数据库上建表并执行迁移，session 结束后清理临时文件。"""
    from app.db.base import init_db

    init_db()
    yield
    for suffix in ("", "-wal", "-shm"):
        try:
            p = Path(str(_TEST_DB_PATH) + suffix)
            if p.exists():
                p.unlink()
        except OSError:
            pass