"""命令行训练 ML 模型。

用法（在 backend 目录下）：
  .venv\\Scripts\\python scripts/train_model.py --symbols 600519,000001,600036 --name pool_daily
  .venv\\Scripts\\python scripts/train_model.py --symbols 600519 --framework xgboost --start 2018-01-01
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data import database
from app.ml.train import train_pipeline


def main():
    parser = argparse.ArgumentParser(description="唯我量化 ML 模型训练")
    parser.add_argument("--symbols", required=True, help="逗号分隔股票代码")
    parser.add_argument("--name", default="", help="模型名称（默认自动生成）")
    parser.add_argument("--framework", default="lightgbm", choices=["lightgbm", "xgboost"])
    parser.add_argument("--start", default="", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", default="", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--train-days", type=int, default=504, help="训练窗交易日数（默认 2 年）")
    parser.add_argument("--test-days", type=int, default=63, help="测试窗交易日数（默认 1 季度）")
    args = parser.parse_args()

    database.init_db()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    report = train_pipeline(
        symbols, start=args.start, end=args.end, framework=args.framework,
        train_days=args.train_days, test_days=args.test_days, name=args.name,
    )

    print(f"\n===== 训练完成：{report['name']} (id={report['model_id']}) =====")
    print(f"框架: {report['framework']}  特征数: {report['n_features']}  样本数: {report['n_samples']}")
    print(f"Walk-Forward 折数: {len(report['folds'])}  平均折 AUC: {report['avg_fold_auc']:.4f}  OOS AUC: {report['oos_auc']:.4f}")
    for fold in report["folds"]:
        print(f"  折 {fold['fold']}: train {fold['train'][0]}~{fold['train'][1]}"
              f" test {fold['test'][0]}~{fold['test'][1]}  auc={fold['auc']:.4f}"
              f"  top组次日均收益={fold['top_quantile_next_ret']:+.4f}")


if __name__ == "__main__":
    main()
