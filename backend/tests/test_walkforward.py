"""Walk-Forward 窗口切分：embargo 边界与折数。"""
from app.ml.walkforward import split_windows


def test_split_windows_embargo():
    dates = [f"2020-01-{i:02d}" for i in range(1, 30)]  # 29 个交易日
    windows = split_windows(dates, train_days=10, test_days=5, embargo_days=2)
    # 每窗占 10+2+5=17 天，步长 5：start=0,5,10 → 3 窗
    assert len(windows) == 3

    for w in windows:
        train_start, train_end = w["train"]
        test_start, test_end = w["test"]
        i_train_end = dates.index(train_end)
        i_test_start = dates.index(test_start)
        # embargo：训练结束与测试开始之间隔 2 个交易日
        assert i_test_start - i_train_end == 3  # 2 天 embargo + 1 天起点
        assert dates.index(test_end) - i_test_start == 4  # 5 天测试窗

    # 窗口滚动步长 = 测试窗长度
    assert windows[1]["train"][0] == dates[5]
    assert windows[2]["train"][0] == dates[10]


def test_split_windows_insufficient():
    dates = [f"2020-01-{i:02d}" for i in range(1, 10)]  # 9 天 < 17 天需求
    assert split_windows(dates, train_days=10, test_days=5, embargo_days=2) == []
