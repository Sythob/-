"""SQLite 引擎与会话工厂。

单进程架构约定：本系统只允许一个 uvicorn 进程写库（禁止多 worker 启动），
SQLite 开 WAL 模式 + busy_timeout，读写并发由同进程内的线程共享。
"""
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        db_path = Path(settings.database_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 5.0},
        )

        @event.listens_for(_engine, "connect")
        def _set_pragma(dbapi_conn, _record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return _engine


def get_session():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal()


def init_db() -> None:
    """建库建表 + 轻量迁移（幂等）。"""
    from app.data import models  # noqa: F401  确保所有模型已注册

    Base.metadata.create_all(get_engine())
    from app.data.migrations import run_migrations

    run_migrations()


def reset_engine() -> None:
    """测试专用：关闭并重建引擎（配合临时数据库文件）。"""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
