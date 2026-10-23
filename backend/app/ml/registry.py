"""ModelRegistry：lightgbm / xgboost 实现，pytorch 预留插槽。

策略与预测服务只依赖 BaseModel 接口（fit/predict_proba/save/load），
不 import 具体框架实现；未来 TorchModel 实现同一接口即可接入。
"""
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np
import pandas as pd


class BaseModel(ABC):
    feature_names: list[str] = []

    @abstractmethod
    def fit(self, X: pd.DataFrame, y, **kwargs) -> "BaseModel":
        ...

    @abstractmethod
    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        ...

    @abstractmethod
    def save(self, path: str) -> None:
        ...

    @classmethod
    @abstractmethod
    def load(cls, path: str) -> "BaseModel":
        ...

    def _align(self, X: pd.DataFrame) -> pd.DataFrame:
        """按训练时的特征顺序对齐列，防止列序漂移导致预测错误。"""
        if self.feature_names:
            missing = [c for c in self.feature_names if c not in X.columns]
            if missing:
                raise ValueError(f"预测数据缺少特征: {missing}")
            return X[self.feature_names]
        return X


class LGBMModel(BaseModel):
    """LightGBM 分类器封装（懒加载，避免非 ML 场景强制依赖）。"""

    def __init__(self, **params):
        import lightgbm as lgb

        defaults = dict(
            n_estimators=300, learning_rate=0.05, num_leaves=31,
            subsample=0.8, colsample_bytree=0.8, random_state=42, verbosity=-1,
        )
        defaults.update(params)
        self._model = lgb.LGBMClassifier(**defaults)
        self.feature_names: list[str] = []

    def fit(self, X: pd.DataFrame, y, **kwargs) -> "LGBMModel":
        self.feature_names = list(X.columns)
        self._model.fit(X, y, **kwargs)
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self._model.predict_proba(self._align(X))[:, 1]

    @property
    def feature_importances(self) -> dict:
        return dict(zip(self.feature_names, self._model.feature_importances_))

    def save(self, path: str) -> None:
        import joblib

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self._model, path)

    @classmethod
    def load(cls, path: str) -> "LGBMModel":
        import joblib

        obj = cls()
        obj._model = joblib.load(path)
        obj.feature_names = list(obj._model.feature_name_ or [])
        return obj


class XGBModel(BaseModel):
    """XGBoost 分类器封装（备用树模型）。"""

    def __init__(self, **params):
        import xgboost as xgb

        defaults = dict(
            n_estimators=300, learning_rate=0.05, max_depth=6,
            subsample=0.8, colsample_bytree=0.8, random_state=42, verbosity=0,
            eval_metric="logloss",
        )
        defaults.update(params)
        self._model = xgb.XGBClassifier(**defaults)
        self.feature_names: list[str] = []

    def fit(self, X: pd.DataFrame, y, **kwargs) -> "XGBModel":
        self.feature_names = list(X.columns)
        self._model.fit(X, y, **kwargs)
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self._model.predict_proba(self._align(X))[:, 1]

    @property
    def feature_importances(self) -> dict:
        return dict(zip(self.feature_names, self._model.feature_importances_))

    def save(self, path: str) -> None:
        import joblib

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self._model, path)

    @classmethod
    def load(cls, path: str) -> "XGBModel":
        import joblib

        obj = cls()
        obj._model = joblib.load(path)
        obj.feature_names = list(obj._model.get_booster().feature_names or [])
        return obj


# pytorch 预留插槽：实现 BaseModel 接口后放入此表即可
MODEL_REGISTRY: dict[str, type[BaseModel] | None] = {
    "lightgbm": LGBMModel,
    "xgboost": XGBModel,
    "pytorch": None,
}
