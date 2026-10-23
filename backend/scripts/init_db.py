"""初始化数据库：建表 + 基础数据（交易日历/证券主表/指数）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data import database
from app.data.sync import sync_instruments, sync_trade_calendar


def main():
    database.init_db()
    print("数据库表已创建")
    n_cal = sync_trade_calendar()
    print(f"交易日历已同步：{n_cal} 条")
    n_inst = sync_instruments(full=False)
    print(f"证券主表已同步：{n_inst} 条")


if __name__ == "__main__":
    main()
