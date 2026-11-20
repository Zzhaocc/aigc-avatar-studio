"""测试入口：导入 app 之前切到独立测试库与测试数据目录。"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TEST_DB = BASE_DIR / "data" / "test_studio.db"

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["AIGC_DATA_DIR"] = str(BASE_DIR / "data")
os.environ["AIGC_DOWNLOAD_STEP"] = "0.01"

for suffix in ("", "-wal", "-shm"):
    candidate = Path(str(TEST_DB) + suffix)
    if candidate.exists():
        candidate.unlink()

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def prepared_db():
    from app.db import init_db
    from app.seed import seed_all

    init_db()
    seed_all()
    yield
