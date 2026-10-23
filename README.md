# 唯我量化 — AI 智能量化交易系统

个人使用的 A股量化研究平台：数据获取 → 策略回测 → 机器学习信号 → AI 辅助分析 → 模拟盘，全流程 Web 可视化。

## 功能

- **数据层**：akshare 免费行情（日线/实时快照/新闻/交易日历），SQLite 本地存储，增量自动同步，前复权计算
- **回测引擎**：自研事件驱动引擎，还原 A股规则（T+1、整手 100 股、涨跌停、佣金/印花税/过户费/滑点），防未来函数（信号次日开盘成交）
- **策略**：双均线、RSI 超跌反弹内置示例，参数可调；支持用自然语言让大模型生成策略代码
- **机器学习**：自研技术指标 + 特征工程（严格防泄漏）+ LightGBM 信号模型，Walk-Forward 滚动验证
- **AI 助手**（DeepSeek，可插拔）：每日复盘报告、新闻情绪打分、下单前风险检查、策略代码生成；`LLM_MOCK=1` 可离线体验
- **模拟盘**：实时行情轮询 + 模拟撮合，与回测同一套规则，AI 风控把关，重启不丢状态

## 快速开始

1. 双击 `start.bat`（首次运行会自动建环境、装依赖、生成 `.env`）
2. 打开 http://localhost:5173
3. （可选）在 `backend\.env` 填入 `DEEPSEEK_API_KEY` 启用真实 AI 功能；不填则保持 `LLM_MOCK=1` 体验模拟 AI
4. （可选）修改 `WATCHLIST` 自选池后，在「系统」页点击「同步数据」或运行 `backend\.venv\Scripts\python scripts/sync_data.py`

## 常用命令

```bash
# 后端
cd backend
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 手动同步自选池数据
.venv\Scripts\python scripts/sync_data.py

# 命令行回测
.venv\Scripts\python scripts/run_backtest.py --strategy dual_ma --symbols 600519 --start 2020-01-01 --end 2023-12-31

# 训练 ML 模型
.venv\Scripts\python scripts/train_model.py --symbols 600519,000001

# 测试
.venv\Scripts\python -m pytest tests -q

# 前端
cd frontend
npm run dev
```

## 目录结构

```
backend/   Python 后端（FastAPI + 数据/回测/ML/LLM/模拟盘 + APScheduler）
frontend/  Vue3 前端（Element Plus + ECharts）
data/      运行时数据（SQLite 库、模型文件、日志，不入 git）
docs/      架构说明、使用手册、策略开发指南、数据口径说明
```

## 免责声明

本项目仅供学习与研究，所有回测与模拟盘结果不代表真实交易收益，不构成任何投资建议。市场有风险，投资需谨慎。
