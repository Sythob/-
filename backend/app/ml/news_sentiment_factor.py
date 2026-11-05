"""新闻情绪因子：中文财经新闻情绪 BERT 对新闻批量打分 → 每日个股情绪聚合。

模型：hw2942/bert-base-chinese-finetuning-financial-news-sentiment-v2
（bert-base-chinese 在中文财经新闻上微调，标签 0负/1中/2正；约 400MB，本地 CPU 推理）。
注：曾选用 yiyanghkust/finbert-tone（研报语气模型），实测对新闻标题无区分度（近全
"中性"），已弃用。

打分：score = P(正面) - P(负面) ∈ [-1, 1]。
网络：优先本地目录 data/models/finbert-news（离线可用）；否则在线下载——
国内 huggingface.co 通常不可达 → 镜像优先，官方兜底（环境变量可覆盖）。

情绪因子加入 ML 特征（train.py 中按 (symbol, trade_date) 合并）：
    news_score_mean/std/count（当日）+ 3日/5日滚动均值与新闻数。
无新闻 → 0（中性）；新闻发布于 t 日，用于预测 t+1，无未来函数。
"""
import logging
import threading

import numpy as np
import pandas as pd

logger = logging.getLogger("ml.news_sentiment")

_model = None
_tokenizer = None
_lock = threading.Lock()
_LABELS = ("negative", "neutral", "positive")  # 类别顺序（模型 id2label）


def _ensure_model():
    """懒加载单例：首次调用加载模型（约 400MB），失败抛异常由上层处理。"""
    global _model, _tokenizer
    if _model is not None:
        return
    with _lock:
        if _model is not None:
            return
        import os
        from pathlib import Path

        from transformers import BertForSequenceClassification, BertTokenizer

        # 优先本地目录（data/models/finbert-news，离线可用）；否则在线下载：
        # 国内网络 huggingface.co 通常不可达 → 镜像优先，官方兜底（环境变量可覆盖）
        local_dir = Path(__file__).resolve().parent.parent.parent / "data" / "models" / "finbert-news"
        if (local_dir / "pytorch_model.bin").exists():
            sources = [str(local_dir)]
        else:
            user_endpoint = os.environ.get("HF_ENDPOINT")
            endpoints = (
                [user_endpoint] if user_endpoint
                else ["https://hf-mirror.com", "https://huggingface.co"]
            )
            sources = []
            for endpoint in endpoints:
                os.environ["HF_ENDPOINT"] = endpoint
                sources.append("hw2942/bert-base-chinese-finetuning-financial-news-sentiment-v2")
        for source in sources:
            try:
                tokenizer = BertTokenizer.from_pretrained(source)
                model = BertForSequenceClassification.from_pretrained(source, num_labels=3)
                model.eval()
                _tokenizer, _model = tokenizer, model
                logger.info("新闻情绪模型加载成功（%s）", source)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("模型加载失败（%s）: %s", source, exc)
        raise RuntimeError("新闻情绪模型加载失败（本地/镜像/官方均不可用）")


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


def batch_score(texts: list[str], batch_size: int = 16) -> list[float]:
    """批量打分 → [-1, 1]（正面概率 - 负面概率）。模型未就绪时抛异常。"""
    import torch

    _ensure_model()
    scores: list[float] = []
    for i in range(0, len(texts), batch_size):
        chunk = [str(t)[:510] for t in texts[i : i + batch_size]]
        enc = _tokenizer(chunk, padding=True, truncation=True, max_length=128,
                         return_tensors="pt")
        with torch.no_grad():
            logits = _model(**enc).logits.numpy()
        proba = _softmax(logits)
        scores.extend((proba[:, 2] - proba[:, 0]).tolist())  # P(正) - P(负)
    return scores


def symbol_daily_sentiment(rows: list[dict]) -> pd.DataFrame:
    """新闻行（symbol/pub_time/score）→ 每日每标的情绪因子表。

    返回列：symbol/trade_date/news_score_mean/news_score_std/news_count
        /news_score_3d/news_count_3d/news_score_5d/news_count_5d
    """
    if not rows:
        return pd.DataFrame(columns=[
            "symbol", "trade_date", "news_score_mean", "news_score_std", "news_count",
            "news_score_3d", "news_count_3d", "news_score_5d", "news_count_5d",
        ])
    df = pd.DataFrame(rows)
    df["trade_date"] = df["pub_time"].str[:10]
    daily = (
        df.groupby(["symbol", "trade_date"], as_index=False)
        .agg(news_score_mean=("score", "mean"), news_score_std=("score", "std"),
             news_count=("score", "size"))
    )
    daily["news_score_std"] = daily["news_score_std"].fillna(0.0)
    out = []
    for sym, g in daily.groupby("symbol"):
        g = g.sort_values("trade_date").reset_index(drop=True)
        for n in (3, 5):
            g[f"news_score_{n}d"] = g["news_score_mean"].rolling(n, min_periods=1).mean()
            g[f"news_count_{n}d"] = g["news_count"].rolling(n, min_periods=1).sum()
        out.append(g)
    return pd.concat(out, ignore_index=True)


SENTIMENT_COLS = [
    "news_score_mean", "news_score_std", "news_count",
    "news_score_3d", "news_count_3d", "news_score_5d", "news_count_5d",
]


def load_sentiment_frame(symbols: list[str]) -> pd.DataFrame:
    """从 News 表加载已打分的新闻 → 每日每标的情绪聚合表（训练/推理共用）。"""
    from app.data import database
    from app.data.models import News

    with database.get_session() as session:
        rows = (
            session.query(News.symbol, News.pub_time, News.finbert_score)
            .filter(News.finbert_score.isnot(None), News.symbol.in_(symbols))
            .all()
        )
    return symbol_daily_sentiment([
        {"symbol": s, "pub_time": p, "score": sc} for s, p, sc in rows
    ])


def merge_sentiment_features(feats: pd.DataFrame, symbol: str,
                             sent: pd.DataFrame | None = None) -> pd.DataFrame:
    """把情绪因子按 trade_date 并入特征表；无新闻的交易日填 0（中性）。

    sent 为 load_sentiment_frame 的预载结果（批量训练时复用，避免重复查库）。
    """
    out = feats.copy()
    g = sent[sent["symbol"] == symbol] if sent is not None else pd.DataFrame()
    if not g.empty:
        g = g.set_index("trade_date")[SENTIMENT_COLS]
    for c in SENTIMENT_COLS:
        out[c] = out["trade_date"].map(g[c]).fillna(0.0) if not g.empty else 0.0
    return out
