# 使用手册

## 1. 启动

双击项目根目录 `start.bat`（首次运行自动建环境、装依赖、生成 `backend\.env`），
浏览器打开 http://localhost:5173 。

手动启动：
```bash
# 后端
cd backend && .venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 前端（另一个终端）
cd frontend && npm run dev
```

## 2. 第一次使用

1. **系统页 → 同步自选池数据**：拉取交易日历、自选池日线、指数（约 1~2 分钟）。
   修改自选池：编辑项目根目录 `.env` 的 `WATCHLIST`（逗号分隔）后重启后端再同步。
2. **总览页**：指数按四组 Tab 展示——A股（上证/深成/创业板/沪深300/上证50/科创50）、
   港股（恒生/国企/恒生科技）、美股（道指/标普500/纳指）、全球（英富时100/德DAX/韩综合；
   日经与法国CAC 新浪源已下线，暂不可用）。各组独立缓存，盘中 5~30 分钟刷新。
3. **行情页**：查看自选池实时行情与 K 线（前复权默认；akshare 快照约延迟 30~60 秒）。
3. **回测页**：选策略 → 填标的/区间 → 开始回测 → 查看指标卡、净值/回撤曲线、成交明细。
4. **策略页**：用自然语言描述策略，AI 生成代码（校验+空跑通过后入库，可用于回测）。
5. **AI 报告页**：生成复盘日报、批量新闻情绪打分。

## 3. 启用真实 AI（DeepSeek）

1. 到 https://platform.deepseek.com 申请 API Key
2. 编辑 `backend\.env`：
   ```
   DEEPSEEK_API_KEY=sk-xxxx
   LLM_MOCK=0
   ```
3. 重启后端。不填 Key 时系统自动使用模拟模型，全部 AI 功能离线可跑。

## 4. 训练 ML 模型

- 网页：回测页没有训练入口，训练用命令行（或直接调用 API `POST /api/ml/train`）：
  ```bash
  cd backend
  .venv\Scripts\python scripts/train_model.py --symbols 600519,000001,600036 --name pool_daily
  ```
- 训练完成会输出 Walk-Forward 各折 AUC 与 OOS 报告，模型存 `data/models/`。
- 回测页策略选「LightGBM 信号策略」，参数 `model_name` 填模型名即可使用。
- 自动重训：`.env` 设 `ML_AUTO_RETRAIN=1` 后每周日自动重训，OOS AUC 明显退化会推送告警。

## 5. 回测进阶功能

- **组合回测**：多标的 + 仓位分配（全仓/等权/固定比例/半凯利）；配合「多因子轮动策略」实现横截面选股
- **风控**：表单可设单笔止损 X%、单日亏损限额 X%；策略内可挂引擎化止损（海龟策略示范了 ATR 追踪止损）
- **参数优化**：回测页「参数网格优化」自动生成网格，后台遍历排序，附过拟合提示
- **完整报告**：回测结果页「完整 HTML 报告」生成自包含报告（净值/回撤/月度热力图/滚动夏普/成交明细），离线可看可存档
- **60 分钟线**：回测和行情页支持 60m 周期（每日 16:30 自动积累自选池数据）

## 6. 市场情绪与板块

- 总览页「市场情绪」：涨跌停家数、涨跌家数（每日 16:05 自动同步）+ 两融余额（交易所官网实时）
- 行情页「行业板块」：新浪行业板块涨幅榜（东财板块接口在本机网络不可用）
- 行情页「AI 个股解读」：行情统计 + 新闻情绪 → LLM 生成解读报告（存入 AI 报告页「个股解读」Tab）

## 7. 通知推送（可选）

`.env` 配置 SMTP 邮箱 / Server酱（微信）/ Telegram 后，模拟盘成交与 AI 日报生成会自动推送；不配置则静默跳过。

## 8. 多模型切换

`.env` 设 `LLM_PROVIDER=claude`（配 `ANTHROPIC_API_KEY`）或 `LLM_PROVIDER=qwen`（配 `QWEN_API_KEY`）即可切换；未配 Key 自动回退离线 Mock。

## 9. 模拟盘

- 模拟交易页：账户初始资金 100 万；下单走 AI 风控检查（block/warn/off 模式在
  `backend\.env` 的 `AI_RISK_MODE` 配置）。
- 规则与真实 A股一致：整手 100 股、T+1（当日买入不可卖）、涨跌停不成交、佣金/印花税/过户费/滑点。
- 交易时段每 30 秒轮询撮合一次；每日 15:05 结算（T+1 解锁 + 净值快照）。
- 重置账户可清空全部持仓与记录。
- 模拟盘自动策略：在 `backend\.env` 设置 `PAPER_STRATEGY=dual_ma` 后重启，
  每日 14:50 用当日合成日线触发策略信号，次日撮合。

## 10. 命令行工具

```bash
cd backend
.venv\Scripts\python scripts/init_db.py                       # 建库+日历+证券主表
.venv\Scripts\python scripts/sync_data.py --symbols 600519    # 手动同步（--full 全市场主表）
.venv\Scripts\python scripts/run_backtest.py --strategy dual_ma --symbols 600519 --start 2020-01-01
.venv\Scripts\python scripts/train_model.py --symbols 600519,000001
.venv\Scripts\python scripts/smoke_e2e.py                     # 端到端冒烟
.venv\Scripts\python -m pytest tests -q                       # 全部测试
```

## 11. 定时任务（自动运行，无需干预）

| 时间 | 任务 |
|---|---|
| 周一 06:10 | 证券主表 + 交易日历刷新 |
| 交易日 08:30 / 16:05 | 自选池日线增量 |
| 交易日 16:10 | 指数日线（沪深300 等） |
| 交易日 17:00 / 17:30 | 新闻抓取 / LLM 情绪打分 |
| 交易日 18:00 | AI 复盘日报 |
| 交易时段每 30s | 模拟盘行情轮询撮合 |
| 交易日 15:05 | 模拟盘结算 |

## 12. 常见问题

- **实时行情获取失败**：akshare 是爬虫接口，可能被限流。本地已同步的数据不受影响，
  回测/复盘/模拟盘结算均可继续；稍后重试即可。
- **前端页面显示"后端连接失败"**：确认 8000 端口 uvicorn 已启动（start.bat 会开两个窗口）。
- **回测提示"无行情数据"**：先同步数据（系统页按钮或 sync_data.py）。
- **AI 报告为空/是模拟内容**：未配置 Key 时是模拟模型，见第 3 节。
