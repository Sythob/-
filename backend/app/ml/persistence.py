"""模型持久化：文件落 data/models/<name>/，元数据登记 ml_model 表。"""
import json
from pathlib import Path

from app.config import get_settings
from app.data import database
from app.data.models import MlModel
from app.ml.registry import MODEL_REGISTRY, BaseModel


def models_dir() -> Path:
    """模型目录跟随数据库位置（测试时自动落到临时目录）。"""
    return Path(get_settings().database_path).parent / "models"


def save_model(
    model: BaseModel,
    name: str,
    features: list[str],
    train_window: str,
    framework: str,
    metrics: dict,
) -> MlModel:
    """保存模型文件并登记 ml_model 表，返回记录。"""
    directory = models_dir() / name
    file_path = directory / "model.joblib"
    model.save(str(file_path))

    with database.get_session() as session:
        row = session.query(MlModel).filter(MlModel.name == name).first()
        if row is None:
            row = MlModel(name=name)
            session.add(row)
        row.framework = framework
        row.features_json = json.dumps(features)
        row.train_window = train_window
        row.metrics_json = json.dumps(metrics, default=str)
        row.file_path = str(file_path)
        row.status = "trained"
        session.commit()
        session.refresh(row)
        return row


def load_model_by_id(model_id: int) -> tuple[BaseModel, MlModel]:
    with database.get_session() as session:
        row = session.get(MlModel, model_id)
        if row is None:
            raise ValueError(f"模型不存在: id={model_id}")
        return _load_from_row(row), row


def load_model_by_name(name: str) -> tuple[BaseModel, MlModel] | None:
    with database.get_session() as session:
        row = session.query(MlModel).filter(MlModel.name == name).first()
        if row is None:
            return None
        return _load_from_row(row), row


def _load_from_row(row: MlModel) -> BaseModel:
    model_cls = MODEL_REGISTRY.get(row.framework)
    if model_cls is None:
        raise ValueError(f"框架未实现: {row.framework}（pytorch 预留中）")
    return model_cls.load(row.file_path)


def list_models() -> list[dict]:
    with database.get_session() as session:
        rows = session.query(MlModel).order_by(MlModel.id.desc()).all()
        return [
            {
                "id": r.id, "name": r.name, "framework": r.framework,
                "features": json.loads(r.features_json or "[]"),
                "train_window": r.train_window,
                "metrics": json.loads(r.metrics_json or "{}"),
                "status": r.status, "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            }
            for r in rows
        ]
