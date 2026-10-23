"""Walk-Forward 滚动验证：训练窗 → embargo → 测试窗，滚动推进。

embargo 防止训练窗末端与测试窗首端的信息边界泄漏。
"""
from typing import TypedDict


class Window(TypedDict):
    train: tuple[str, str]
    test: tuple[str, str]


def split_windows(
    dates: list[str],
    train_days: int = 504,
    test_days: int = 63,
    embargo_days: int = 1,
) -> list[Window]:
    """按交易日数切分滚动窗口。dates 为升序唯一日期列表。

    train = dates[i : i+train_days]，embargo 跳过 embargo_days 个交易日，
    test = 其后 test_days 个交易日；步长 = test_days。
    """
    n = len(dates)
    windows: list[Window] = []
    start = 0
    while start + train_days + embargo_days + test_days <= n:
        train_end = start + train_days
        test_start = train_end + embargo_days
        windows.append({
            "train": (dates[start], dates[train_end - 1]),
            "test": (dates[test_start], dates[test_start + test_days - 1]),
        })
        start += test_days
    return windows
