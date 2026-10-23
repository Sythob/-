"""系统 API：配置（key 脱敏）、后台任务、调度器状态。"""
from fastapi import APIRouter
from pydantic import BaseModel

from app.config import get_settings
from app.utils import tasks

router = APIRouter(tags=["system"])


def _mask(key: str) -> str:
    if not key:
        return "未配置"
    if len(key) <= 8:
        return "已配置"
    return f"{key[:4]}****{key[-4:]}"


@router.get("/system/config")
def config():
    s = get_settings()
    return {
        "watchlist": s.watchlist_symbols,
        "llm_mock": s.llm_mock,
        "llm_model": s.llm_model,
        "llm_base_url": s.llm_base_url,
        "deepseek_api_key": _mask(s.deepseek_api_key),
        "ai_risk_mode": s.ai_risk_mode,
        "timezone": s.timezone,
        "data_source": s.data_source,
        "database_path": s.database_path,
    }


class WatchlistRequest(BaseModel):
    symbols: list[str]


@router.put("/system/watchlist")
def update_watchlist(req: WatchlistRequest):
    """更新自选池：写入 .env 并热更新内存缓存（无需重启后端）。"""
    from app.config import BASE_DIR

    symbols = [s.strip() for s in req.symbols if s.strip()][:50]
    env_path = BASE_DIR / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    replaced = False
    for i, line in enumerate(lines):
        if line.strip().startswith("WATCHLIST="):
            lines[i] = "WATCHLIST=" + ",".join(symbols)
            replaced = True
            break
    if not replaced:
        lines.append("WATCHLIST=" + ",".join(symbols))
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # 热更新缓存（get_settings 为 lru_cached 单例）
    get_settings().watchlist = ",".join(symbols)
    return {"watchlist": symbols}


@router.get("/system/tasks")
def task_list():
    return tasks.list_tasks()


@router.get("/system/tasks/{task_id}")
def task_detail(task_id: str):
    from fastapi import HTTPException

    task = tasks.get_task(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在或已过期")
    return task


@router.get("/system/scheduler")
def scheduler():
    from app.scheduler.jobs import scheduler_status

    return scheduler_status()
