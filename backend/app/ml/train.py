"""训练流水线：构建特征 → Walk-Forward 验证 → 汇总 OOS 报告 → 用最近窗口重训最终模型。

最终模型只用最近训练窗的数据训练（与实盘使用场景一致），
OOS 各折指标来自 Walk-Forward，反映真实样本外表现。
"""
from datetime import datetime

import pandas as pd
from sklearn.metrics import accuracy_score, roc_auc_score

from app.data.loader import load_daily
from app.ml.features import build_features, feature_columns
from app.ml.persistence import save_model
from app.ml.registry import MODEL_REGISTRY
from app.ml.walkforward import split_windows
from app.utils.logger import get_logger

logger = get_logger("ml.train")


def train_pipeline(
    symbols: list[str],
    start: str = "",
    end: str = "",
    framework: str = "lightgbm",
    train_days: int = 504,
    test_days: int = 63,
    embargo_days: int = 1,
    name: str = "",
) -> dict:
    """训练并保存模型，返回报告 dict（含 model_id/folds/oos_auc 等）。"""
    model_cls = MODEL_REGISTRY.get(framework)
    if model_cls is None:
        raise ValueError(f"框架不可用: {framework}")

    # 1. 加载行情 → 特征（多标的池化）
    frames = []
    for symbol in symbols:
        df = load_daily(symbol, start=start, end=end, adjust="qfq")
        if df.empty:
            logger.warning("跳过无数据的标的: %s", symbol)
            continue
        frames.append(build_features(df))
    if not frames:
        raise ValueError("所有标的均无数据，无法训练")
    pooled = pd.concat(frames, ignore_index=True)

    feats = feature_columns(pooled)
    pooled = pooled.dropna(subset=feats + ["label"])
    if len(pooled) < train_days + test_days:
        raise ValueError(f"有效样本不足（{len(pooled)} 行），请扩大标的池或时间区间")

    # 2. Walk-Forward
    dates = sorted(pooled["trade_date"].unique())
    windows = split_windows(dates, train_days, test_days, embargo_days)
    if not windows:
        raise ValueError("数据不足以划分 Walk-Forward 窗口")

    folds = []
    oos_rows = []
    for i, w in enumerate(windows):
        train = pooled[(pooled["trade_date"] >= w["train"][0]) & (pooled["trade_date"] <= w["train"][1])]
        test = pooled[(pooled["trade_date"] >= w["test"][0]) & (pooled["trade_date"] <= w["test"][1])]
        # dropna 后的索引不连续，必须重置才能与预测概率的 RangeIndex 对齐
        train = train.reset_index(drop=True)
        test = test.reset_index(drop=True)
        model = model_cls().fit(train[feats], train["label"])
        proba = model.predict_proba(test[feats])
        fold = {
            "fold": i, "train": list(w["train"]), "test": list(w["test"]),
            "n_train": len(train), "n_test": len(test),
            "auc": float(roc_auc_score(test["label"], proba)),
            "accuracy": float(accuracy_score(test["label"], proba > 0.5)),
        }
        quantiles = pd.qcut(pd.Series(proba), 5, labels=False, duplicates="drop")
        top_mask = quantiles == quantiles.max()
        fold["top_quantile_next_ret"] = float(test.loc[top_mask, "next_ret"].mean()) if top_mask.any() else 0.0
        folds.append(fold)
        oos_rows.append(pd.DataFrame({
            "prob": proba, "label": test["label"].to_numpy(), "next_ret": test["next_ret"].to_numpy(),
        }))
        logger.info("折 %d: train=%s~%s test=%s~%s auc=%.4f",
                    i, w["train"][0], w["train"][1], w["test"][0], w["test"][1], fold["auc"])

    # 3. 汇总 OOS + 最终模型
    oos = pd.concat(oos_rows, ignore_index=True)
    oos_auc = float(roc_auc_score(oos["label"], oos["prob"]))
    last = windows[-1]
    final_train = pooled[
        (pooled["trade_date"] >= last["train"][0]) & (pooled["trade_date"] <= last["train"][1])
    ]
    final_model = model_cls().fit(final_train[feats], final_train["label"])

    model_name = name or f"{framework}_{'_'.join(symbols)[:20]}_{datetime.now():%Y%m%d_%H%M}"
    row = save_model(
        final_model, model_name, feats,
        train_window=f"{last['train'][0]}~{last['train'][1]}",
        framework=framework,
        metrics={"oos_auc": oos_auc, "folds": folds},
    )
    return {
        "model_id": row.id,
        "name": model_name,
        "framework": framework,
        "features": feats,
        "n_features": len(feats),
        "n_samples": len(pooled),
        "folds": folds,
        "oos_auc": oos_auc,
        "avg_fold_auc": sum(f["auc"] for f in folds) / len(folds),
    }
