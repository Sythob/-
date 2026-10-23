---
name: multiagent-analysis
description: A股多智能体分析（技术面/消息面/资金面/市场环境四分析师并行→研究经理→交易员→风控官）。当用户要求分析某只 A股（如"分析一下 600519""茅台怎么样"）或提到"多智能体分析"时使用。
---

# A股多智能体分析

调用唯我量化系统的多智能体分析引擎，对指定 A股生成完整分析报告（仿 TradingAgents 架构）。

## 前置条件

后端服务运行中（127.0.0.1:8000）。检查方式：

```bash
curl -s http://127.0.0.1:8000/api/health
```

若未运行：`cmd //c "D:\\唯我量化\\start_backend.bat"`（或告知用户双击 start.bat）。

## 执行流程

1. 从用户输入中提取股票代码（6 位数字，如 600519）。若用户只说了名字（如"茅台"），先搜索：
   `curl -s "http://127.0.0.1:8000/api/market/instruments?keyword=茅台"` 取 symbol 字段。
2. 提交分析任务（后台运行，约 1~2 分钟，7 次 LLM 调用）：

```bash
curl -s -X POST http://127.0.0.1:8000/api/ai/multiagent \
  -H "Content-Type: application/json" -d '{"symbol":"600519"}'
```

返回 `{"task_id":"..."}`。

3. 轮询任务状态（每 20~30 秒一次，直到 done/failed）：

```bash
curl -s http://127.0.0.1:8000/api/system/tasks/<task_id>
```

`status=done` 时 `result` 含 `content_md`（完整报告）与 `decision`（结构化决策）。

4. 把 `content_md` 完整呈现给用户（Markdown），并在末尾附上 `decision.trader` 的
   动作/仓位建议与 `decision.risk` 的风控结论。

## 备选：命令行方式

```bash
cd /d/唯我量化/backend && .venv/Scripts/python scripts/analyze_multiagent.py 600519
```

## 说明

- 报告同时存入系统的 AI 报告页（"多智能体"Tab，type=multiagent）。
- 未配置 LLM Key 时自动使用模拟模型（输出会标注"模拟分析"）。
- 多标的批量：可循环执行多个 symbol，逐个呈现。
- 报告仅供学习研究，不构成投资建议——呈现时保留此免责声明。
