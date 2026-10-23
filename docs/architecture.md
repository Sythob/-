# 架构说明

## 总览

唯我量化是个人单机部署的 A股量化研究系统：数据获取 → 回测 → ML 信号 → AI 辅助 → 模拟盘，Web 可视化操作。

```
┌────────────────────── 一个 uvicorn 进程（127.0.0.1:8000）──────────────────────┐
│  FastAPI (REST /api/*)                                                          │
│  ├─ APScheduler BackgroundScheduler（数据同步/新闻/日报/行情轮询）                │
│  ├─ PaperEngine（模拟盘：轮询行情→撮合→日线合成→结算）                            │
│  ├─ BacktestEngine（回测，线程池内按需运行）                                     │
│  └─ SQLite（WAL + busy_timeout）← 唯一写入口                                     │
└──────────────────────────────────────────────────────────────────────────────────┘
       ▲ HTTP（dev: Vite 5173 代理；prod: FastAPI 挂载 frontend/dist）
       │
┌──────┴───────────────┐          ┌────────────────────────────┐
│ Vue3 + Element Plus  │          │ akshare（行情/新闻，爬虫接口）│
│ + ECharts 浏览器     │          │ DeepSeek（LLM，OpenAI 兼容） │
└──────────────────────┘          └────────────────────────────┘
```

**单进程假设**：API + 调度器 + 模拟盘同一进程，SQLite 只有一个写入口，杜绝写冲突。
禁止多 worker 启动 uvicorn。

## 后端模块

| 模块 | 职责 |
|---|---|
| `app/data` | 数据源抽象（akshare 实现 + tushare 预留）、SQLite 16 张表、增量同步、前复权加载 |
| `app/engine` | 自研事件驱动回测引擎：撮合/费用/T+1/整手/涨跌停/绩效指标（纯函数，可测） |
| `app/strategies` | Strategy 基类 + 注册表 + params_schema（驱动 UI 表单）；内置双均线/RSI/ML 信号 |
| `app/ml` | 自研技术指标、特征工程（防未来函数）、Walk-Forward 验证、LightGBM 训练与模型注册表 |
| `app/llm` | LLM Provider 抽象（DeepSeek + 离线 Mock）、四个任务：复盘/情绪/风控/代码生成 |
| `app/paper` | 模拟盘：账户/持仓落库、行情轮询、撮合（复用回测组件）、AI 风控钩子 |
| `app/scheduler` | APScheduler 定时任务（同步/新闻/日报/轮询/结算，全部带交易日守卫） |
| `app/api` | REST 路由（market/backtest/strategy/ml/ai/paper/system） |

## 关键设计决策

1. **防未来函数（三重防线）**
   - 特征工程：t 时刻特征只用 ≤t 数据（rolling/shift 回看算子）
   - 回测撮合：信号在 t 收盘产生，t+1 开盘价 ± 滑点成交
   - Walk-Forward：严格按时间切分 + 窗口间 embargo
2. **复权铁律**：库存不复权原始价 + adj_factor，加载时「最新因子法」算前复权，
   增量 upsert 永不产生历史断点
3. **模拟盘与回测同一套规则**：matching/costs/portfolio 组件复用；
   策略信号单次日才撮合（与回测铁律一致）；状态全落库，重启可恢复
4. **AI 故障不阻塞交易链路**：LLM 10s 超时兜底放行；`LLM_MOCK=1` 全流程离线可跑
5. **数据是本地事实源**：一旦入库，断网也能回测、演示、生成报告

## 数据库（16 张表）

instrument / trade_calendar / daily_bar / minute_bar(预留) / news / strategy /
backtest_run / backtest_metric / trade_record / equity_curve /
paper_account / paper_order / paper_position / ai_report / ml_model / ml_prediction
