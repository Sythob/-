"""唯我量化 - FastAPI 入口。

单进程架构：API + 调度器 + 模拟盘引擎同跑在本进程内（SQLite 唯一写入口，禁多 worker）。
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.data import database
from app.utils.logger import get_logger

logger = get_logger("main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from app.utils.netcompat import patch_ipv4_preference

    patch_ipv4_preference()
    database.init_db()
    logger.info("数据库初始化完成")

    from app.scheduler.jobs import start_scheduler, stop_scheduler

    start_scheduler()

    # 启动补齐：错过定时任务时自动修复（与「进入系统补跑」共用 catchup.run_catch_up）
    def _catch_up():
        try:
            from app.config import get_settings

            if not get_settings().startup_catchup:
                return  # 测试环境关闭补跑
            from app.scheduler.catchup import run_catch_up

            run_catch_up(force=True)  # 启动补跑不受收盘时间限制（数据自愈优先）
        except Exception:  # noqa: BLE001
            logger.exception("启动补齐失败（不影响正常使用）")


    import threading

    threading.Thread(target=_catch_up, daemon=True).start()

    yield
    stop_scheduler()
    logger.info("应用关闭")


app = FastAPI(title="唯我量化", version="0.1.0", description="AI 智能量化交易系统", lifespan=lifespan)

# 开发模式前端（Vite 5173）跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "app": "唯我量化", "version": app.version}
