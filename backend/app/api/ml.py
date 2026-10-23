"""ML API：模型列表、训练任务、Walk-Forward 报告。"""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.data import database
from app.data.models import MlModel
from app.ml.persistence import list_models
from app.ml.train import train_pipeline
from app.utils import tasks

router = APIRouter(tags=["ml"])


class TrainRequest(BaseModel):
    symbols: list[str] = []
    framework: str = "lightgbm"
    name: str = ""
    start: str = ""
    end: str = ""


@router.get("/ml/models")
def models():
    return list_models()


@router.post("/ml/train")
def train(req: TrainRequest):
    symbols = req.symbols or get_settings().watchlist_symbols
    if not symbols:
        raise HTTPException(400, "请提供标的列表")
    if req.framework not in ("lightgbm", "xgboost"):
        raise HTTPException(400, "framework 仅支持 lightgbm/xgboost（pytorch 预留）")
    task_id = tasks.submit(
        train_pipeline, symbols, req.start, req.end, req.framework, 504, 63, 1, req.name
    )
    return {"task_id": task_id}


@router.get("/ml/models/{model_id}")
def model_detail(model_id: int):
    with database.get_session() as session:
        row = session.get(MlModel, model_id)
        if row is None:
            raise HTTPException(404, "模型不存在")
        return {
            "id": row.id, "name": row.name, "framework": row.framework,
            "features": json.loads(row.features_json or "[]"),
            "train_window": row.train_window,
            "metrics": json.loads(row.metrics_json or "{}"),
            "status": row.status,
            "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        }
