"""pytest 公共夹具：临时 SQLite 库，全程离线不触碰真实数据。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.data import database


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """把默认库替换为临时文件并建表；返回 session 工厂；结束重置引擎。"""
    monkeypatch.setattr(get_settings(), "database_path", str(tmp_path / "test.db"))
    database.reset_engine()
    database.init_db()
    from app.data.calendar import invalidate_cache

    invalidate_cache()  # 日历缓存是模块级单例，切库必须失效
    yield database.get_session
    database.reset_engine()
    invalidate_cache()
