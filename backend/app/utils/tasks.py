"""进程内后台任务管理：线程池 + 状态字典（单进程架构下够用，无跨进程需求）。

用于回测、ML 训练、数据同步等耗时操作；前端提交后轮询状态。
"""
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="qstock-task")
_tasks: dict[str, dict] = {}
_lock = threading.Lock()


def submit(fn, *args, **kwargs) -> str:
    """提交后台任务，返回 task_id。状态: pending → running → done/failed。"""
    task_id = uuid.uuid4().hex[:12]
    now = time.time()
    with _lock:
        _tasks[task_id] = {"status": "pending", "error": "", "result": None,
                           "created_at": now, "started_at": None, "finished_at": None}

    def _run():
        with _lock:
            _tasks[task_id]["status"] = "running"
            _tasks[task_id]["started_at"] = time.time()
        try:
            result = fn(*args, **kwargs)
            with _lock:
                _tasks[task_id].update(status="done", result=result,
                                       finished_at=time.time())
        except Exception as exc:  # noqa: BLE001 后台任务必须兜底
            with _lock:
                _tasks[task_id].update(status="failed", error=str(exc),
                                       finished_at=time.time())

    _executor.submit(_run)
    return task_id


def get_task(task_id: str) -> dict | None:
    with _lock:
        task = _tasks.get(task_id)
        return dict(task) if task else None


def list_tasks() -> list[dict]:
    with _lock:
        now = time.time()
        result = []
        for k, v in _tasks.items():
            item = {"task_id": k, **dict(v)}
            if item["finished_at"]:
                item["elapsed"] = round(item["finished_at"] - item["created_at"], 1)
            elif item["started_at"]:
                item["elapsed"] = round(now - item["started_at"], 1)
            else:
                item["elapsed"] = round(now - item["created_at"], 1)
            result.append(item)
        return result
