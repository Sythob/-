"""手动同步 CLI。

用法（在 backend 目录下）：
  .venv\\Scripts\\python scripts/sync_data.py                     # 同步自选池日线
  .venv\\Scripts\\python scripts/sync_data.py --symbols 600519,000001
  .venv\\Scripts\\python scripts/sync_data.py --full               # 全市场股票主表（周末手动）
  .venv\\Scripts\\python scripts/sync_data.py --news               # 顺带抓取新闻
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.data import database
from app.data.sync import sync_all, sync_instruments


def main():
    parser = argparse.ArgumentParser(description="唯我量化数据同步")
    parser.add_argument("--symbols", default="", help="逗号分隔代码，默认用 .env 的 WATCHLIST")
    parser.add_argument("--full", action="store_true", help="同步全市场股票主表（周末手动）")
    parser.add_argument("--news", action="store_true", help="同步完成后抓取自选池新闻")
    args = parser.parse_args()

    database.init_db()
    settings = get_settings()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or settings.watchlist_symbols

    if args.full:
        sync_instruments(full=True)
    result = sync_all(symbols=symbols, with_news=args.news)

    print(f"同步完成：更新 {result['bars']['updated']}，跳过 {result['bars']['skipped']}，"
          f"失败 {result['bars']['failed']}，新闻 {result['news']} 条")


if __name__ == "__main__":
    main()
