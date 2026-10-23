"""命令行多智能体分析：4 位分析师并行 → 经理汇总 → 交易员 → 风控官。

用法（在 backend 目录下）：
  .venv\\Scripts\\python scripts/analyze_multiagent.py 600519
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data import database
from app.llm.tasks.multiagent import analyze_multiagent


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/analyze_multiagent.py <股票代码> [更多代码...]")
        sys.exit(1)
    database.init_db()
    for symbol in sys.argv[1:]:
        print(f"\n{'=' * 60}\n分析 {symbol}（约 1~2 分钟）...\n{'=' * 60}")
        result = asyncio.run(analyze_multiagent(symbol))
        print(result["content_md"])
        print(f"\n[模型: {result['model']}，报告已存入 AI 报告页（多智能体 Tab）]")


if __name__ == "__main__":
    main()
