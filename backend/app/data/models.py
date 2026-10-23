"""SQLAlchemy ORM 模型（16 张表）。

约定：所有日期存 "YYYY-MM-DD" 文本（无时区问题）；成交量统一为「股」。
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.data.database import Base


class Instrument(Base):
    """证券主表：股票/ETF/指数"""

    __tablename__ = "instrument"

    symbol: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), default="")
    exchange: Mapped[str] = mapped_column(String(10), default="")  # sh/sz/bj
    type: Mapped[str] = mapped_column(String(10), default="stock")  # stock/index/etf
    board: Mapped[str] = mapped_column(String(10), default="")  # 主板/创业板/科创板/北交所
    status: Mapped[str] = mapped_column(String(10), default="active")
    list_date: Mapped[str] = mapped_column(String(10), default="")
    is_st: Mapped[int] = mapped_column(Integer, default=0)


class TradeCalendar(Base):
    """交易日历"""

    __tablename__ = "trade_calendar"

    trade_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    is_open: Mapped[int] = mapped_column(Integer, default=1)


class DailyBar(Base):
    """日线：存不复权原始价 + 复权因子。

    复权铁律：只存原始价 + adj_factor，加载时用「最新因子法」算前复权，
    保证增量 upsert 不会产生历史断点。
    """

    __tablename__ = "daily_bar"

    symbol: Mapped[str] = mapped_column(String(10), primary_key=True)
    trade_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    open: Mapped[float] = mapped_column(Float, default=0.0)
    high: Mapped[float] = mapped_column(Float, default=0.0)
    low: Mapped[float] = mapped_column(Float, default=0.0)
    close: Mapped[float] = mapped_column(Float, default=0.0)
    volume: Mapped[float] = mapped_column(Float, default=0.0)  # 股
    amount: Mapped[float] = mapped_column(Float, default=0.0)  # 元
    pct_change: Mapped[float] = mapped_column(Float, default=0.0)
    turnover_rate: Mapped[float] = mapped_column(Float, default=0.0)
    adj_factor: Mapped[float] = mapped_column(Float, default=1.0)


class MinuteBar(Base):
    """分钟/小时线（预留，M7 起积累 60 分钟线）"""

    __tablename__ = "minute_bar"

    symbol: Mapped[str] = mapped_column(String(10), primary_key=True)
    dt: Mapped[str] = mapped_column(String(19), primary_key=True)  # YYYY-MM-DD HH:MM:SS
    interval: Mapped[str] = mapped_column(String(5), primary_key=True)  # 60m 等
    open: Mapped[float] = mapped_column(Float, default=0.0)
    high: Mapped[float] = mapped_column(Float, default=0.0)
    low: Mapped[float] = mapped_column(Float, default=0.0)
    close: Mapped[float] = mapped_column(Float, default=0.0)
    volume: Mapped[float] = mapped_column(Float, default=0.0)  # 股


class News(Base):
    """新闻（含 LLM 情绪打分结果）"""

    __tablename__ = "news"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(10), index=True, default="")
    source: Mapped[str] = mapped_column(String(50), default="")
    title: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(255), unique=True, default="")
    pub_time: Mapped[str] = mapped_column(String(19), default="")
    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    sentiment_label: Mapped[str | None] = mapped_column(String(10), nullable=True)  # 利好/利空/中性
    sentiment_reason: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Strategy(Base):
    """策略登记（内置 + 自然语言生成）"""

    __tablename__ = "strategy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    kind: Mapped[str] = mapped_column(String(10), default="builtin")  # builtin/generated
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    code: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class BacktestRun(Base):
    """回测任务"""

    __tablename__ = "backtest_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_name: Mapped[str] = mapped_column(String(50), default="")
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    universe_json: Mapped[str] = mapped_column(Text, default="[]")
    start_date: Mapped[str] = mapped_column(String(10), default="")
    end_date: Mapped[str] = mapped_column(String(10), default="")
    capital: Mapped[float] = mapped_column(Float, default=1000000.0)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/running/done/failed
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class BacktestMetric(Base):
    """回测指标（EAV：run_id + 指标名 → 值）"""

    __tablename__ = "backtest_metric"

    run_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    metric_name: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[float] = mapped_column(Float, default=0.0)


class TradeRecord(Base):
    """成交记录（回测与模拟盘共用；run_id 与 account_id 二选一）"""

    __tablename__ = "trade_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    account_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(10), default="")
    direction: Mapped[str] = mapped_column(String(4), default="buy")  # buy/sell
    price: Mapped[float] = mapped_column(Float, default=0.0)
    volume: Mapped[int] = mapped_column(Integer, default=0)  # 股
    trade_time: Mapped[str] = mapped_column(String(19), default="")
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    slippage: Mapped[float] = mapped_column(Float, default=0.0)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)


class EquityCurve(Base):
    """净值曲线（回测与模拟盘共用）"""

    __tablename__ = "equity_curve"

    scope_type: Mapped[str] = mapped_column(String(10), primary_key=True)  # backtest/paper
    scope_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    equity: Mapped[float] = mapped_column(Float, default=0.0)
    cash: Mapped[float] = mapped_column(Float, default=0.0)
    return_rate: Mapped[float] = mapped_column(Float, default=0.0)


class PaperAccount(Base):
    """模拟账户"""

    __tablename__ = "paper_account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), default="默认账户")
    initial_capital: Mapped[float] = mapped_column(Float, default=1000000.0)
    cash: Mapped[float] = mapped_column(Float, default=1000000.0)
    risk_mode: Mapped[str] = mapped_column(String(10), default="warn")  # block/warn/off
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class PaperOrder(Base):
    """模拟委托（含 AI 风控检查结果）"""

    __tablename__ = "paper_order"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    symbol: Mapped[str] = mapped_column(String(10), default="")
    direction: Mapped[str] = mapped_column(String(4), default="buy")  # buy/sell
    order_type: Mapped[str] = mapped_column(String(10), default="market")  # market/limit
    price: Mapped[float] = mapped_column(Float, default=0.0)
    volume: Mapped[int] = mapped_column(Integer, default=0)  # 股
    filled_price: Mapped[float] = mapped_column(Float, default=0.0)
    filled_volume: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="submitted")  # submitted/filled/cancelled/rejected
    next_session: Mapped[int] = mapped_column(Integer, default=0)  # 1=策略信号单（次日撮合，防未来函数）
    condition_type: Mapped[str] = mapped_column(String(20), default="")  # ""/stop_loss/take_profit 条件单
    trigger_price: Mapped[float] = mapped_column(Float, default=0.0)     # 条件触发价
    ai_check_json: Mapped[str] = mapped_column(Text, default="")
    reject_reason: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class PaperPosition(Base):
    """模拟持仓"""

    __tablename__ = "paper_position"

    account_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(10), primary_key=True)
    volume: Mapped[int] = mapped_column(Integer, default=0)  # 总持仓（股）
    available_volume: Mapped[int] = mapped_column(Integer, default=0)  # 可卖（股，T+1）
    avg_cost: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MarketBreadth(Base):
    """市场广度（涨跌停/涨跌家数），每日收盘后同步"""

    __tablename__ = "market_breadth"

    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    limit_up: Mapped[int] = mapped_column(Integer, default=0)
    limit_down: Mapped[int] = mapped_column(Integer, default=0)
    advance: Mapped[int] = mapped_column(Integer, default=0)
    decline: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)


class AutoBacktest(Base):
    """智能批量回测战绩（策略 × 标的 × 参数，记录胜率与收益率）"""

    __tablename__ = "auto_backtest"
    __table_args__ = (
        UniqueConstraint("strategy", "symbol", "params_json", "start_date", "end_date",
                         name="uq_auto_backtest_run"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy: Mapped[str] = mapped_column(String(50))
    symbol: Mapped[str] = mapped_column(String(10))
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    start_date: Mapped[str] = mapped_column(String(10), default="")
    end_date: Mapped[str] = mapped_column(String(10), default="")
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    total_return: Mapped[float] = mapped_column(Float, default=0.0)
    annualized_return: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown: Mapped[float] = mapped_column(Float, default=0.0)
    sharpe_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    trade_count: Mapped[int] = mapped_column(Integer, default=0)
    run_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AiLesson(Base):
    """AI 复盘经验教训库（每期报告沉淀，供后续报告引用改进）"""

    __tablename__ = "ai_lesson"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String(10), default="")
    lesson: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class BoardWatch(Base):
    """打板候选池（当日涨停/近涨停候选，含连板高度与风险标注）"""

    __tablename__ = "board_watch"

    symbol: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), default="")
    pct_change: Mapped[float] = mapped_column(Float, default=0.0)
    streak: Mapped[int] = mapped_column(Integer, default=0)
    turnover: Mapped[float] = mapped_column(Float, default=0.0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    risk: Mapped[str] = mapped_column(String(20), default="")
    date: Mapped[str] = mapped_column(String(10), default="")


class TscreenWatch(Base):
    """做T候选粗筛观察区（全市场快照粗筛结果，每日刷新，未过趋势/换手精筛）"""

    __tablename__ = "tscreen_watch"

    symbol: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), default="")
    amplitude_today: Mapped[float] = mapped_column(Float, default=0.0)
    amount_today: Mapped[float] = mapped_column(Float, default=0.0)
    pct_change: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    date: Mapped[str] = mapped_column(String(10), default="")


class AiReport(Base):
    """AI 产出（复盘日报/情绪/风控/代码生成）"""

    __tablename__ = "ai_report"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_type: Mapped[str] = mapped_column(String(20), index=True)  # daily/weekly/news/risk/codegen
    title: Mapped[str] = mapped_column(String(200), default="")
    content_md: Mapped[str] = mapped_column(Text, default="")
    meta_json: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


class AiPrediction(Base):
    """AI 预测台账：每条预测逐条记录、次日自动核验对错、错误沉淀教训。"""

    __tablename__ = "ai_prediction"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date: Mapped[str] = mapped_column(String(10), index=True)  # 预测来源报告日期
    horizon: Mapped[str] = mapped_column(String(10), default="daily")  # daily/weekly
    category: Mapped[str] = mapped_column(String(10), default="大盘")  # 大盘/板块/个股
    symbol: Mapped[str] = mapped_column(String(10), default="")
    content: Mapped[str] = mapped_column(Text, default="")  # 预测原文
    verdict: Mapped[str] = mapped_column(String(10), default="pending")  # pending/correct/wrong/partial
    actual: Mapped[str] = mapped_column(Text, default="")  # 实际行情描述
    lesson: Mapped[str] = mapped_column(Text, default="")  # 错误教训（正确的为空）
    error_analysis: Mapped[str] = mapped_column(Text, default="")  # 错误归因：哪条数据依据判断失误（正确的为空）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MlModel(Base):
    """ML 模型登记"""

    __tablename__ = "ml_model"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    framework: Mapped[str] = mapped_column(String(20), default="lightgbm")  # lightgbm/xgboost/pytorch
    features_json: Mapped[str] = mapped_column(Text, default="[]")
    train_window: Mapped[str] = mapped_column(String(100), default="")
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    file_path: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(20), default="trained")  # trained/failed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MlPrediction(Base):
    """模型预测缓存"""

    __tablename__ = "ml_prediction"

    model_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(10), primary_key=True)
    trade_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    prob: Mapped[float] = mapped_column(Float, default=0.0)
